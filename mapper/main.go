package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha1"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"golang.org/x/net/html"
	"golang.org/x/net/publicsuffix"
)

type config struct {
	RedisAddr       string
	RedisPassword   string
	RedisDB         int
	RedisPoolSize   int
	PostgresDSN     string
	InputQueue      string
	OutputQueue     string
	VisitedPrefix   string
	Workers         int
	MaxPagesPerSeed int
	RequestTimeout  time.Duration
	IdleConnTimeout time.Duration
	UserAgent       string
	SameDomainMode  string
	BatchSize       int
	QueueBlockTime  time.Duration
}

type crawlJob struct {
	Seed       *url.URL
	DomainKey  string
	VisitedKey string
	DomainID   int64
	RunID      int64
}

func main() {
	cfg := loadConfig()
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))

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

	if len(os.Args) > 1 && os.Args[1] == "replay" {
		if err := replay(ctx, cfg, logger, rdb, db, os.Args[2:]); err != nil {
			logger.Error("replay failed", "err", err)
			os.Exit(1)
		}
		return
	}

	client := &http.Client{
		Timeout: cfg.RequestTimeout,
		Transport: &http.Transport{
			Proxy: http.ProxyFromEnvironment,
			DialContext: (&net.Dialer{
				Timeout:   10 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			MaxIdleConns:          cfg.Workers * 4,
			MaxIdleConnsPerHost:   cfg.Workers * 2,
			IdleConnTimeout:       cfg.IdleConnTimeout,
			TLSHandshakeTimeout:   10 * time.Second,
			ExpectContinueTimeout: 1 * time.Second,
			ForceAttemptHTTP2:     true,
		},
	}

	logger.Info("mapper ready",
		"redis", cfg.RedisAddr,
		"input_queue", cfg.InputQueue,
		"output_queue", cfg.OutputQueue,
		"workers", cfg.Workers,
		"same_domain_mode", cfg.SameDomainMode,
	)

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

		seed, err := normalizeURL(item[1], nil)
		if err != nil {
			logger.Warn("discarding invalid seed", "value", item[1], "err", err)
			continue
		}
		job, err := newCrawlJob(seed, cfg)
		if err != nil {
			logger.Warn("discarding seed without valid domain", "url", seed.String(), "err", err)
			continue
		}

		logger.Info("crawl started", "seed", seed.String(), "domain", job.DomainKey)
		domain, err := db.upsertDomain(ctx, job.DomainKey)
		if err != nil {
			logger.Error("domain upsert failed", "domain", job.DomainKey, "err", err)
			continue
		}
		run, err := db.startRun(ctx, domain.ID, seed.String())
		if err != nil {
			logger.Error("crawl run create failed", "domain", job.DomainKey, "err", err)
			continue
		}
		if err := db.upsertURLs(ctx, domain.ID, run.ID, []string{seed.String()}); err != nil {
			logger.Error("seed url upsert failed", "domain", job.DomainKey, "err", err)
			continue
		}
		job.DomainID = domain.ID
		job.RunID = run.ID

		stats := crawl(ctx, cfg, logger, rdb, db, client, job)
		finishCtx, finishCancel := context.WithTimeout(context.Background(), 10*time.Second)
		if err := db.finishRun(finishCtx, run.ID, runStatus(ctx), stats); err != nil {
			logger.Error("crawl run finish failed", "run_id", run.ID, "err", err)
		}
		finishCancel()
		logger.Info("crawl finished",
			"seed", seed.String(),
			"domain", job.DomainKey,
			"run_id", run.ID,
			"pages", stats.Pages,
			"links", stats.Links,
			"errors", stats.Errors,
			"duration", stats.Duration.String(),
		)
	}
}

func replay(ctx context.Context, cfg config, logger *slog.Logger, rdb *redis.Client, db *store, args []string) error {
	if len(args) == 0 || args[0] == "" {
		return errors.New("usage: mapper replay <domain-or-url> [redis-output-queue]")
	}
	requested := args[0]
	domain, err := replayDomainKey(requested, cfg.SameDomainMode)
	if err != nil {
		return err
	}
	queue := cfg.OutputQueue
	if len(args) > 1 && args[1] != "" {
		queue = args[1]
	}

	total, err := db.replayDomain(ctx, domain, cfg.BatchSize, func(batch []string) error {
		items := make([]interface{}, len(batch))
		for i, raw := range batch {
			items[i] = raw
		}
		return rdb.RPush(ctx, queue, items...).Err()
	})
	if err != nil {
		return err
	}
	logger.Info("replay finished", "requested", requested, "domain", domain, "queue", queue, "urls", total)
	return nil
}

func replayDomainKey(raw string, mode string) (string, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", errors.New("missing replay domain")
	}
	if !strings.Contains(raw, "://") {
		raw = "https://" + strings.Trim(raw, "/")
	}
	u, err := normalizeURL(raw, nil)
	if err != nil {
		return "", err
	}
	return domainForURL(u, mode)
}

type crawlStats struct {
	Pages    int64
	Links    int64
	Errors   int64
	Duration time.Duration
}

func crawl(ctx context.Context, cfg config, logger *slog.Logger, rdb *redis.Client, db *store, client *http.Client, job crawlJob) crawlStats {
	started := time.Now()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	frontier := newURLQueue()
	discovered := make(chan string, cfg.BatchSize*2)
	persisted := make(chan []string, cfg.Workers)
	var workers sync.WaitGroup
	var stats crawlStats
	var statsMu sync.Mutex

	var dbWriter sync.WaitGroup
	dbWriter.Add(1)
	go func() {
		defer dbWriter.Done()
		for urls := range persisted {
			if err := db.upsertURLs(ctx, job.DomainID, job.RunID, urls); err != nil && ctx.Err() == nil {
				logger.Error("postgres url upsert failed", "run_id", job.RunID, "items", len(urls), "err", err)
			}
		}
	}()

	addURLs := func(urls []*url.URL) []string {
		if len(urls) == 0 {
			return nil
		}
		if cfg.MaxPagesPerSeed > 0 {
			statsMu.Lock()
			limitReached := stats.Pages >= int64(cfg.MaxPagesPerSeed)
			statsMu.Unlock()
			if limitReached {
				return nil
			}
		}

		unique := make(map[string]*url.URL, len(urls))
		for _, u := range urls {
			unique[u.String()] = u
		}

		pipe := rdb.Pipeline()
		cmds := make(map[string]*redis.IntCmd, len(unique))
		for raw := range unique {
			cmds[raw] = pipe.SAdd(ctx, job.VisitedKey, raw)
		}
		if _, err := pipe.Exec(ctx); err != nil {
			if ctx.Err() == nil {
				logger.Error("visited set pipeline failed", "err", err, "items", len(unique))
			}
			return nil
		}

		added := make([]string, 0, len(unique))
		for raw, cmd := range cmds {
			if cmd.Val() == 0 {
				continue
			}
			u := unique[raw]
			if frontier.push(u) {
				added = append(added, raw)
			}
		}
		if len(added) > 0 {
			select {
			case persisted <- added:
			case <-ctx.Done():
				return nil
			}
		}
		return added
	}

	addURL := func(u *url.URL) bool {
		added := addURLs([]*url.URL{u})
		return len(added) == 1
	}

	sendDiscovered := func(links []string) bool {
		for _, link := range links {
			select {
			case discovered <- link:
			case <-ctx.Done():
				return false
			}
		}
		if len(links) > 0 {
			statsMu.Lock()
			stats.Links += int64(len(links))
			statsMu.Unlock()
		}
		return true
	}

	seedAdded := addURL(job.Seed)
	if !seedAdded {
		logger.Info("seed already mapped", "seed", job.Seed.String(), "visited_key", job.VisitedKey)
		frontier.close()
	}

	var writer sync.WaitGroup
	writer.Add(1)
	go func() {
		defer writer.Done()
		flushLinks(ctx, rdb, cfg.OutputQueue, cfg.BatchSize, discovered, logger)
	}()

	go func() {
		<-ctx.Done()
		frontier.close()
	}()

	workers.Add(cfg.Workers)
	for i := 0; i < cfg.Workers; i++ {
		go func() {
			defer workers.Done()
			for {
				page, ok := frontier.pop()
				if !ok {
					return
				}
				pageLinks, err := fetchLinks(ctx, client, cfg.UserAgent, page)
				if err != nil {
					statsMu.Lock()
					stats.Errors++
					statsMu.Unlock()
					frontier.done()
					continue
				}
				statsMu.Lock()
				stats.Pages++
				statsMu.Unlock()

				candidates := make([]*url.URL, 0, len(pageLinks))
				for _, href := range pageLinks {
					link, err := normalizeURL(href, page)
					if err != nil || !sameDomain(job.DomainKey, link, cfg.SameDomainMode) {
						continue
					}
					candidates = append(candidates, link)
				}
				added := addURLs(candidates)
				if !sendDiscovered(added) {
					frontier.done()
					return
				}
				frontier.done()
			}
		}()
	}

	workers.Wait()
	close(discovered)
	writer.Wait()
	close(persisted)
	dbWriter.Wait()

	statsMu.Lock()
	stats.Duration = time.Since(started)
	out := stats
	statsMu.Unlock()
	return out
}

type urlQueue struct {
	mu          sync.Mutex
	cond        *sync.Cond
	items       []*url.URL
	head        int
	outstanding int
	closed      bool
}

func newURLQueue() *urlQueue {
	q := &urlQueue{}
	q.cond = sync.NewCond(&q.mu)
	return q
}

func (q *urlQueue) push(u *url.URL) bool {
	q.mu.Lock()
	defer q.mu.Unlock()
	if !q.closed {
		q.items = append(q.items, u)
		q.outstanding++
		q.cond.Signal()
		return true
	}
	return false
}

func (q *urlQueue) pop() (*url.URL, bool) {
	q.mu.Lock()
	defer q.mu.Unlock()

	for q.head >= len(q.items) && !q.closed {
		q.cond.Wait()
	}
	if q.head >= len(q.items) {
		return nil, false
	}
	u := q.items[q.head]
	q.items[q.head] = nil
	q.head++
	if q.head > 4096 && q.head*2 >= len(q.items) {
		q.items = append([]*url.URL(nil), q.items[q.head:]...)
		q.head = 0
	}
	return u, true
}

func (q *urlQueue) close() {
	q.mu.Lock()
	q.closed = true
	q.cond.Broadcast()
	q.mu.Unlock()
}

func (q *urlQueue) done() {
	q.mu.Lock()
	if q.outstanding > 0 {
		q.outstanding--
	}
	if q.outstanding == 0 {
		q.closed = true
		q.cond.Broadcast()
	}
	q.mu.Unlock()
}

func fetchLinks(ctx context.Context, client *http.Client, userAgent string, page *url.URL) ([]string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, page.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "text/html,application/xhtml+xml")

	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("unexpected status %d", resp.StatusCode)
	}
	mediaType, _, err := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	if err == nil && mediaType != "" && mediaType != "text/html" && mediaType != "application/xhtml+xml" {
		return nil, fmt.Errorf("unsupported content-type %s", mediaType)
	}

	const maxPageBytes = 16 << 20
	return extractLinks(io.LimitReader(resp.Body, maxPageBytes)), nil
}

func extractLinks(r io.Reader) []string {
	z := html.NewTokenizer(bufio.NewReaderSize(r, 64*1024))
	links := make([]string, 0, 64)
	for {
		tt := z.Next()
		switch tt {
		case html.ErrorToken:
			return links
		case html.StartTagToken, html.SelfClosingTagToken:
			token := z.Token()
			if token.Data != "a" && token.Data != "area" {
				continue
			}
			for _, attr := range token.Attr {
				if attr.Key == "href" {
					links = append(links, attr.Val)
				}
			}
		}
	}
}

func flushLinks(ctx context.Context, rdb *redis.Client, queue string, batchSize int, links <-chan string, logger *slog.Logger) {
	batch := make([]interface{}, 0, batchSize)
	flush := func() {
		if len(batch) == 0 {
			return
		}
		if err := rdb.RPush(ctx, queue, batch...).Err(); err != nil && ctx.Err() == nil {
			logger.Error("output queue write failed", "err", err, "items", len(batch))
		}
		batch = batch[:0]
	}
	for link := range links {
		batch = append(batch, link)
		if len(batch) >= batchSize {
			flush()
		}
	}
	flush()
}

func normalizeURL(raw string, base *url.URL) (*url.URL, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" || strings.HasPrefix(raw, "#") {
		return nil, errors.New("empty url")
	}
	lower := strings.ToLower(raw)
	if strings.HasPrefix(lower, "mailto:") || strings.HasPrefix(lower, "tel:") || strings.HasPrefix(lower, "javascript:") || strings.HasPrefix(lower, "data:") {
		return nil, errors.New("unsupported scheme")
	}

	var parsed *url.URL
	var err error
	if base != nil {
		rel, err := url.Parse(raw)
		if err != nil {
			return nil, err
		}
		parsed = base.ResolveReference(rel)
	} else {
		parsed, err = url.Parse(raw)
		if err != nil {
			return nil, err
		}
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
	var b bytes.Buffer
	b.WriteByte('/')
	b.WriteString(strings.Join(stack, "/"))
	if strings.HasSuffix(u, "/") && b.Len() > 1 {
		b.WriteByte('/')
	}
	return b.String()
}

func sameDomain(domainKey string, u *url.URL, mode string) bool {
	key, err := domainForURL(u, mode)
	return err == nil && key == domainKey
}

func newCrawlJob(seed *url.URL, cfg config) (crawlJob, error) {
	domainKey, err := domainForURL(seed, cfg.SameDomainMode)
	if err != nil {
		return crawlJob{}, err
	}
	sum := sha1.Sum([]byte(domainKey))
	return crawlJob{
		Seed:       seed,
		DomainKey:  domainKey,
		VisitedKey: cfg.VisitedPrefix + ":" + hex.EncodeToString(sum[:]),
	}, nil
}

func domainForURL(u *url.URL, mode string) (string, error) {
	host := u.Hostname()
	if host == "" {
		return "", errors.New("missing hostname")
	}
	if mode == "host" {
		return host, nil
	}
	registrable, err := publicsuffix.EffectiveTLDPlusOne(host)
	if err != nil {
		return host, nil
	}
	return registrable, nil
}

func loadConfig() config {
	return config{
		RedisAddr:       envString("REDIS_ADDR", "redis:6379"),
		RedisPassword:   envString("REDIS_PASSWORD", ""),
		RedisDB:         envInt("REDIS_DB", 0),
		RedisPoolSize:   envInt("REDIS_POOL_SIZE", envInt("WORKERS", 128)*2),
		PostgresDSN:     envString("POSTGRES_DSN", "postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable"),
		InputQueue:      envString("INPUT_QUEUE", "mapper:in"),
		OutputQueue:     envString("OUTPUT_QUEUE", "mapper:out"),
		VisitedPrefix:   envString("VISITED_PREFIX", "mapper:visited"),
		Workers:         envInt("WORKERS", 128),
		MaxPagesPerSeed: envInt("MAX_PAGES_PER_SEED", 0),
		RequestTimeout:  envDuration("REQUEST_TIMEOUT", 15*time.Second),
		IdleConnTimeout: envDuration("IDLE_CONN_TIMEOUT", 90*time.Second),
		UserAgent:       envString("USER_AGENT", "UniCrawlerMapper/0.1"),
		SameDomainMode:  envString("SAME_DOMAIN_MODE", "registrable"),
		BatchSize:       envInt("REDIS_BATCH_SIZE", 1000),
		QueueBlockTime:  envDuration("QUEUE_BLOCK_TIME", 5*time.Second),
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
