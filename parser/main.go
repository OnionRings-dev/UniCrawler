package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log/slog"
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
}

type parseJob struct {
	RawURL string
}

type parserOutputMessage struct {
	URL         string    `json:"url"`
	Domain      string    `json:"domain"`
	DocumentID  int64     `json:"document_id"`
	ContentHash string    `json:"content_hash"`
	Changed     bool      `json:"changed"`
	ParsedAt    time.Time `json:"parsed_at"`
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

	logger.Info("parser ready",
		"redis", cfg.RedisAddr,
		"input_queue", cfg.InputQueue,
		"output_queue", cfg.OutputQueue,
		"workers", cfg.Workers,
	)

	jobs := make(chan parseJob)
	var wg sync.WaitGroup
	wg.Add(cfg.Workers)
	for i := 0; i < cfg.Workers; i++ {
		workerID := i + 1
		go func() {
			defer wg.Done()
			for job := range jobs {
				processJob(ctx, cfg, logger, rdb, db, renderer, workerID, job)
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
		select {
		case jobs <- parseJob{RawURL: item[1]}:
		case <-ctx.Done():
		}
	}

	close(jobs)
	wg.Wait()
	logger.Info("parser stopped")
}

func processJob(ctx context.Context, cfg config, logger *slog.Logger, rdb *redis.Client, db *store, renderer Renderer, workerID int, job parseJob) {
	normalized, err := normalizeURL(job.RawURL)
	if err != nil {
		logger.Warn("discarding invalid url", "value", job.RawURL, "err", err)
		return
	}
	domain, err := domainForURL(normalized)
	if err != nil {
		logger.Warn("discarding url without valid domain", "url", normalized.String(), "err", err)
		return
	}
	urlHash := hashBytes(normalized.String())
	if isUnsupportedDocumentURL(normalized) {
		logger.Info("skipping unsupported document url", "url", normalized.String(), "worker", workerID)
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
		URL:         normalized.String(),
		URLHash:     urlHash,
		Domain:      domain,
		Title:       firstNonEmpty(extracted.Title, rendered.Title),
		Language:    rendered.Language,
		Markdown:    extracted.Markdown,
		ContentHash: hashBytes(extracted.Markdown),
		HTMLHash:    hashBytes(rendered.HTML),
		StatusCode:  rendered.StatusCode,
		ContentType: rendered.ContentType,
		FinalURL:    rendered.FinalURL,
		ParsedAt:    now,
	}

	storeCtx, cancel := storeContext()
	defer cancel()
	result, err := db.saveParsedPage(storeCtx, record)
	if err != nil {
		logger.Error("parsed page save failed", "url", normalized.String(), "err", err)
		return
	}
	if !result.Changed {
		logger.Info("page unchanged", "url", normalized.String(), "document_id", result.DocumentID)
		return
	}

	msg := parserOutputMessage{
		URL:         normalized.String(),
		Domain:      domain,
		DocumentID:  result.DocumentID,
		ContentHash: hex.EncodeToString(record.ContentHash),
		Changed:     true,
		ParsedAt:    now,
	}
	payload, err := json.Marshal(msg)
	if err != nil {
		logger.Error("output message marshal failed", "url", normalized.String(), "err", err)
		return
	}
	if err := rdb.RPush(ctx, cfg.OutputQueue, payload).Err(); err != nil {
		logger.Error("output queue write failed", "url", normalized.String(), "err", err)
		return
	}
	logger.Info("page changed", "url", normalized.String(), "document_id", result.DocumentID, "version_id", result.VersionID)
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

func isUnsupportedDocumentURL(u *url.URL) bool {
	ext := strings.ToLower(path.Ext(u.EscapedPath()))
	switch ext {
	case ".7z", ".aac", ".avi", ".avif", ".bmp", ".bz2", ".css", ".csv", ".doc", ".docx",
		".eot", ".epub", ".flac", ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".js", ".json",
		".m4a", ".m4v", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".ogg", ".ogv", ".otf",
		".pdf", ".png", ".ppt", ".pptx", ".rar", ".rss", ".svg", ".tar", ".tif", ".tiff",
		".ttf", ".txt", ".wav", ".webm", ".webp", ".woff", ".woff2", ".xls", ".xlsx",
		".xml", ".zip":
		return true
	default:
		return false
	}
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
	}
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
