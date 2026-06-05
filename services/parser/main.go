package main

import (
	"context"
	"crypto/sha256"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"golang.org/x/net/publicsuffix"
	"unicrawler/shared/contracts"
)

type config struct {
	RedisAddr      string
	RedisPassword  string
	RedisDB        int
	RedisPoolSize  int
	PostgresDSN    string
	InputQueue     string
	OutputQueue    string
	Workers        int
	RequestTimeout time.Duration
	RenderTimeout  time.Duration
	MaxRetries     int
	UserAgent      string
	QueueBlockTime time.Duration
	ChromePath     string
	RemoteDebugURL string
	MaxPDFBytes    int64
	HTTPAddr       string
}

type parseJob struct {
	Request contracts.ParseRequest
}

func main() {
	cfg := loadConfig()
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	rdb := redis.NewClient(&redis.Options{
		Addr:     cfg.RedisAddr,
		Password: cfg.RedisPassword,
		DB:       cfg.RedisDB,
		PoolSize: cfg.RedisPoolSize,
	})
	defer rdb.Close()

	if err := rdb.Ping(ctx).Err(); err != nil {
		logger.Error("redis ping failed", "err", err)
		os.Exit(1)
	}

	db, err := openStore(ctx, cfg.PostgresDSN)
	if err != nil {
		logger.Error("postgres connection failed", "err", err)
		os.Exit(1)
	}
	defer db.close()

	renderer, err := newChromiumRenderer(ctx, cfg)
	if err != nil {
		logger.Error("renderer init failed", "err", err)
		os.Exit(1)
	}
	defer renderer.Close()
	client := newHTTPClient(cfg)

	logger.Info("parser ready",
		"redis", cfg.RedisAddr,
		"input_queue", cfg.InputQueue,
		"output_queue", cfg.OutputQueue,
		"workers", cfg.Workers,
	)
	startHealthServer(cfg.HTTPAddr, "parser", logger)
	if err := db.heartbeat(ctx, "parser", "ready", map[string]any{"input_queue": cfg.InputQueue, "output_queue": cfg.OutputQueue}); err != nil {
		logger.Warn("heartbeat write failed", "err", err)
	}

	jobs := make(chan parseJob)
	var wg sync.WaitGroup
	wg.Add(cfg.Workers)
	for i := 0; i < cfg.Workers; i++ {
		workerID := i + 1
		go func() {
			defer wg.Done()
			for job := range jobs {
				processJob(ctx, cfg, logger, rdb, db, renderer, client, workerID, job)
			}
		}()
	}

	for ctx.Err() == nil {
		item, err := rdb.BRPop(ctx, cfg.QueueBlockTime, cfg.InputQueue).Result()
		if errors.Is(err, redis.Nil) {
			continue
		}
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			logger.Error("queue pop failed", "err", err)
			time.Sleep(time.Second)
			continue
		}
		if len(item) != 2 {
			continue
		}
		request, err := contracts.ParseParseRequest(item[1])
		if err != nil {
			logger.Warn("discarding invalid parse request", "value", item[1], "err", err)
			continue
		}
		select {
		case jobs <- parseJob{Request: request}:
		case <-ctx.Done():
		}
	}

	close(jobs)
	wg.Wait()
	logger.Info("parser stopped")
}

func processJob(ctx context.Context, cfg config, logger *slog.Logger, rdb *redis.Client, db *store, renderer Renderer, client *http.Client, workerID int, job parseJob) {
	target, err := db.getParseTarget(ctx, job.Request.URLID)
	if err != nil {
		logger.Warn("parse target not found", "url_id", job.Request.URLID, "err", err)
		return
	}
	normalized, err := normalizeURL(target.URL)
	if err != nil {
		logger.Warn("discarding invalid url", "value", target.URL, "err", err)
		return
	}
	domain := target.Domain
	urlHash := target.URLHash
	_ = db.pipelineEvent(ctx, "parser", "parse_started", target.DomainID, target.LastCrawlRunID, target.ID, 0, 0, map[string]any{"url": target.URL})
	if isSkippedAssetURL(normalized) {
		logger.Info("skipping unsupported document url", "url", normalized.String(), "worker", workerID)
		return
	}
	if isPDFURL(normalized) {
		processPDFJob(ctx, cfg, logger, rdb, db, client, workerID, normalized, "")
		return
	}

	var rendered RenderedPage
	err = withRetries(ctx, cfg.MaxRetries, func(attempt int) error {
		attemptCtx, cancel := context.WithTimeout(ctx, cfg.RenderTimeout)
		defer cancel()
		var renderErr error
		rendered, renderErr = renderer.Render(attemptCtx, normalized.String())
		if renderErr != nil {
			logger.Warn("render failed", "url", normalized.String(), "attempt", attempt, "err", renderErr)
		}
		return renderErr
	})
	if err != nil {
		storeCtx, cancel := storeContext()
		defer cancel()
		if saveErr := db.recordParseError(storeCtx, normalized.String(), urlHash, domain, err.Error()); saveErr != nil {
			logger.Error("parse error save failed", "url", normalized.String(), "err", saveErr)
		}
		logger.Error("url parse failed", "url", normalized.String(), "worker", workerID, "err", err)
		return
	}

	extracted, err := extractContent(rendered.HTML, rendered.FinalURL)
	if err != nil {
		storeCtx, cancel := storeContext()
		defer cancel()
		if saveErr := db.recordParseError(storeCtx, normalized.String(), urlHash, domain, err.Error()); saveErr != nil {
			logger.Error("parse error save failed", "url", normalized.String(), "err", saveErr)
		}
		logger.Error("content extraction failed", "url", normalized.String(), "worker", workerID, "err", err)
		return
	}

	now := time.Now().UTC()
	record := pageRecord{
		URL:          normalized.String(),
		URLHash:      urlHash,
		Domain:       domain,
		Title:        firstNonEmpty(extracted.Title, rendered.Title),
		Language:     rendered.Language,
		Markdown:     extracted.Markdown,
		ContentHash:  hashBytes(extracted.Markdown),
		HTMLHash:     hashBytes(rendered.HTML),
		StatusCode:   rendered.StatusCode,
		ContentType:  rendered.ContentType,
		FinalURL:     rendered.FinalURL,
		ParsedAt:     now,
		DocumentType: "html",
	}

	storeCtx, cancel := storeContext()
	defer cancel()
	result, err := db.saveParsedPage(storeCtx, record)
	if err != nil {
		logger.Error("parsed page save failed", "url", normalized.String(), "err", err)
		return
	}
	for _, pdfURL := range extractPDFLinks(rendered.HTML, rendered.FinalURL) {
		processPDFJob(ctx, cfg, logger, rdb, db, client, workerID, pdfURL, normalized.String())
	}
	if !result.Changed {
		logger.Info("page unchanged", "url", normalized.String(), "document_id", result.DocumentID)
		_ = db.pipelineEvent(ctx, "parser", "parse_unchanged", target.DomainID, target.LastCrawlRunID, target.ID, result.DocumentID, 0, map[string]any{"url": normalized.String()})
		return
	}

	payload, err := contracts.MarshalEnvelope(contracts.VectorizeRequestType, contracts.VectorizeRequest{
		DocumentID: result.DocumentID,
		VersionID:  result.VersionID,
	})
	if err != nil {
		logger.Error("output message marshal failed", "url", normalized.String(), "err", err)
		return
	}
	if err := rdb.RPush(ctx, cfg.OutputQueue, payload).Err(); err != nil {
		logger.Error("output queue write failed", "url", normalized.String(), "err", err)
		return
	}
	_ = db.pipelineEvent(ctx, "parser", "parse_changed", target.DomainID, target.LastCrawlRunID, target.ID, result.DocumentID, result.VersionID, map[string]any{"url": normalized.String(), "document_type": "html"})
	logger.Info("page changed", "url", normalized.String(), "document_id", result.DocumentID, "version_id", result.VersionID)
}

func processPDFJob(ctx context.Context, cfg config, logger *slog.Logger, rdb *redis.Client, db *store, client *http.Client, workerID int, pdfURL *url.URL, sourceURL string) {
	domain, err := domainForURL(pdfURL)
	if err != nil {
		logger.Warn("discarding pdf without valid domain", "url", pdfURL.String(), "err", err)
		return
	}
	urlHash := hashBytes(pdfURL.String())

	var parsed parsedPDF
	err = withRetries(ctx, cfg.MaxRetries, func(attempt int) error {
		attemptCtx, cancel := context.WithTimeout(ctx, cfg.RequestTimeout)
		defer cancel()
		var parseErr error
		parsed, parseErr = fetchAndParsePDF(attemptCtx, client, pdfURL.String(), cfg.UserAgent, cfg.MaxPDFBytes)
		if parseErr != nil {
			logger.Warn("pdf parse failed", "url", pdfURL.String(), "source_url", sourceURL, "attempt", attempt, "err", parseErr)
		}
		return parseErr
	})
	if err != nil {
		storeCtx, cancel := storeContext()
		defer cancel()
		if saveErr := db.recordParseError(storeCtx, pdfURL.String(), urlHash, domain, err.Error()); saveErr != nil {
			logger.Error("pdf parse error save failed", "url", pdfURL.String(), "err", saveErr)
		}
		logger.Error("pdf parse failed", "url", pdfURL.String(), "source_url", sourceURL, "worker", workerID, "err", err)
		return
	}

	now := time.Now().UTC()
	record := pageRecord{
		URL:          pdfURL.String(),
		URLHash:      urlHash,
		Domain:       domain,
		Title:        parsed.Title,
		Markdown:     parsed.Markdown,
		ContentHash:  hashBytes(parsed.Markdown),
		StatusCode:   parsed.StatusCode,
		ContentType:  parsed.ContentType,
		FinalURL:     parsed.FinalURL,
		ParsedAt:     now,
		DocumentType: "pdf",
		SourceURL:    sourceURL,
	}
	storeCtx, cancel := storeContext()
	defer cancel()
	result, err := db.saveParsedPage(storeCtx, record)
	if err != nil {
		logger.Error("parsed pdf save failed", "url", pdfURL.String(), "source_url", sourceURL, "err", err)
		return
	}
	if !result.Changed {
		logger.Info("pdf unchanged", "url", pdfURL.String(), "source_url", sourceURL, "document_id", result.DocumentID)
		return
	}
	payload, err := contracts.MarshalEnvelope(contracts.VectorizeRequestType, contracts.VectorizeRequest{
		DocumentID: result.DocumentID,
		VersionID:  result.VersionID,
	})
	if err != nil {
		logger.Error("pdf output message marshal failed", "url", pdfURL.String(), "err", err)
		return
	}
	if err := rdb.RPush(ctx, cfg.OutputQueue, payload).Err(); err != nil {
		logger.Error("pdf output queue write failed", "url", pdfURL.String(), "err", err)
		return
	}
	logger.Info("pdf changed", "url", pdfURL.String(), "source_url", sourceURL, "document_id", result.DocumentID, "version_id", result.VersionID)
}

func withRetries(ctx context.Context, maxRetries int, fn func(attempt int) error) error {
	var last error
	for attempt := 1; attempt <= maxRetries+1; attempt++ {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err := fn(attempt); err != nil {
			last = err
		} else {
			return nil
		}
		if attempt <= maxRetries {
			delay := time.Duration(attempt*attempt) * 500 * time.Millisecond
			timer := time.NewTimer(delay)
			select {
			case <-timer.C:
			case <-ctx.Done():
				timer.Stop()
				return ctx.Err()
			}
		}
	}
	return last
}

func normalizeURL(raw string) (*url.URL, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" || strings.HasPrefix(raw, "#") {
		return nil, errors.New("empty url")
	}
	lower := strings.ToLower(raw)
	if strings.HasPrefix(lower, "mailto:") || strings.HasPrefix(lower, "tel:") || strings.HasPrefix(lower, "javascript:") || strings.HasPrefix(lower, "data:") {
		return nil, errors.New("unsupported scheme")
	}

	parsed, err := url.Parse(raw)
	if err != nil {
		return nil, err
	}
	if parsed.Scheme == "" {
		parsed.Scheme = "https"
	}
	parsed.Scheme = strings.ToLower(parsed.Scheme)
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, errors.New("unsupported scheme")
	}
	if parsed.Host == "" {
		return nil, errors.New("missing host")
	}
	parsed.Host = strings.ToLower(parsed.Host)
	parsed.Fragment = ""
	parsed.User = nil
	parsed.Path = cleanPath(parsed.EscapedPath())
	parsed.RawPath = ""
	if (parsed.Scheme == "http" && strings.HasSuffix(parsed.Host, ":80")) ||
		(parsed.Scheme == "https" && strings.HasSuffix(parsed.Host, ":443")) {
		parsed.Host = strings.TrimSuffix(strings.TrimSuffix(parsed.Host, ":80"), ":443")
	}
	return parsed, nil
}

func cleanPath(path string) string {
	if path == "" {
		return "/"
	}
	u, err := url.PathUnescape(path)
	if err != nil {
		return path
	}
	parts := strings.Split(u, "/")
	stack := make([]string, 0, len(parts))
	for _, part := range parts {
		switch part {
		case "", ".":
			continue
		case "..":
			if len(stack) > 0 {
				stack = stack[:len(stack)-1]
			}
		default:
			stack = append(stack, part)
		}
	}
	out := "/" + strings.Join(stack, "/")
	if strings.HasSuffix(u, "/") && len(out) > 1 {
		out += "/"
	}
	return out
}

func domainForURL(u *url.URL) (string, error) {
	host := u.Hostname()
	if host == "" {
		return "", errors.New("missing hostname")
	}
	registrable, err := publicsuffix.EffectiveTLDPlusOne(host)
	if err != nil {
		return host, nil
	}
	return registrable, nil
}

func isSkippedAssetURL(u *url.URL) bool {
	ext := strings.ToLower(path.Ext(u.EscapedPath()))
	switch ext {
	case ".7z", ".aac", ".avi", ".avif", ".bmp", ".bz2", ".css", ".csv", ".doc", ".docx",
		".eot", ".epub", ".flac", ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".js", ".json",
		".m4a", ".m4v", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".ogg", ".ogv", ".otf",
		".png", ".ppt", ".pptx", ".rar", ".rss", ".svg", ".tar", ".tif", ".tiff",
		".ttf", ".txt", ".wav", ".webm", ".webp", ".woff", ".woff2", ".xls", ".xlsx",
		".xml", ".zip":
		return true
	default:
		return false
	}
}

func isPDFURL(u *url.URL) bool {
	return strings.EqualFold(path.Ext(u.EscapedPath()), ".pdf")
}

func hashBytes(value string) []byte {
	sum := sha256.Sum256([]byte(value))
	out := make([]byte, len(sum))
	copy(out, sum[:])
	return out
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" {
			return value
		}
	}
	return ""
}

func newHTTPClient(cfg config) *http.Client {
	return &http.Client{
		Timeout: cfg.RequestTimeout,
		Transport: &http.Transport{
			Proxy: http.ProxyFromEnvironment,
			DialContext: (&net.Dialer{
				Timeout:   10 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			MaxIdleConns:          cfg.Workers * 4,
			MaxIdleConnsPerHost:   cfg.Workers * 2,
			IdleConnTimeout:       90 * time.Second,
			TLSHandshakeTimeout:   10 * time.Second,
			ExpectContinueTimeout: 1 * time.Second,
			ForceAttemptHTTP2:     true,
		},
	}
}

func storeContext() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 10*time.Second)
}

func loadConfig() config {
	workers := envInt("WORKERS", 8)
	return config{
		RedisAddr:      envString("REDIS_ADDR", "redis:6379"),
		RedisPassword:  envString("REDIS_PASSWORD", ""),
		RedisDB:        envInt("REDIS_DB", 0),
		RedisPoolSize:  envInt("REDIS_POOL_SIZE", workers*2),
		PostgresDSN:    envString("POSTGRES_DSN", "postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable"),
		InputQueue:     envString("INPUT_QUEUE", "mapper:out"),
		OutputQueue:    envString("OUTPUT_QUEUE", "parser:out"),
		Workers:        workers,
		RequestTimeout: envDuration("REQUEST_TIMEOUT", 15*time.Second),
		RenderTimeout:  envDuration("RENDER_TIMEOUT", 30*time.Second),
		MaxRetries:     envInt("MAX_RETRIES", 2),
		UserAgent:      envString("USER_AGENT", "UniCrawlerParser/0.1"),
		QueueBlockTime: envDuration("QUEUE_BLOCK_TIME", 5*time.Second),
		ChromePath:     envString("CHROME_PATH", ""),
		RemoteDebugURL: envString("RENDER_REMOTE_DEBUG_URL", ""),
		MaxPDFBytes:    int64(envInt("MAX_PDF_BYTES", 50<<20)),
		HTTPAddr:       envString("HTTP_ADDR", ":8081"),
	}
}

func startHealthServer(addr string, service string, logger *slog.Logger) {
	if addr == "" {
		return
	}
	mux := http.NewServeMux()
	started := time.Now()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("content-type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("content-type", "text/plain; version=0.0.4")
		_, _ = w.Write([]byte("unicrawler_node_uptime_seconds{service=\"" + service + "\"} " + strconv.FormatInt(int64(time.Since(started).Seconds()), 10) + "\n"))
	})
	go func() {
		if err := http.ListenAndServe(addr, mux); err != nil {
			logger.Warn("health server stopped", "addr", addr, "err", err)
		}
	}()
}

func envString(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func envInt(key string, fallback int) int {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func envDuration(key string, fallback time.Duration) time.Duration {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	parsed, err := time.ParseDuration(value)
	if err != nil {
		return fallback
	}
	return parsed
}
