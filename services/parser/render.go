package main

import (
	"context"
	"errors"
	"strings"
	"sync"
	"time"

	"github.com/chromedp/cdproto/emulation"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/chromedp"
)

type Renderer interface {
	Render(ctx context.Context, rawURL string) (RenderedPage, error)
	Close()
}

type RenderedPage struct {
	HTML        string
	FinalURL    string
	Title       string
	Language    string
	StatusCode  int
	ContentType string
}

type chromiumRenderer struct {
	allocator context.Context
	cancel    context.CancelFunc
	userAgent string
	requestTO time.Duration
}

func newChromiumRenderer(parent context.Context, cfg config) (*chromiumRenderer, error) {
	var alloc context.Context
	var cancel context.CancelFunc
	if cfg.RemoteDebugURL != "" {
		alloc, cancel = chromedp.NewRemoteAllocator(parent, cfg.RemoteDebugURL)
	} else {
		opts := append(chromedp.DefaultExecAllocatorOptions[:],
			chromedp.Flag("headless", true),
			chromedp.Flag("disable-gpu", true),
			chromedp.Flag("disable-dev-shm-usage", true),
			chromedp.Flag("no-sandbox", true),
			chromedp.Flag("blink-settings", "imagesEnabled=false"),
			chromedp.UserAgent(cfg.UserAgent),
		)
		if cfg.ChromePath != "" {
			opts = append(opts, chromedp.ExecPath(cfg.ChromePath))
		}
		alloc, cancel = chromedp.NewExecAllocator(parent, opts...)
	}
	ctx, ctxCancel := chromedp.NewContext(alloc)
	defer ctxCancel()
	if err := chromedp.Run(ctx); err != nil {
		cancel()
		return nil, err
	}
	return &chromiumRenderer{allocator: alloc, cancel: cancel, userAgent: cfg.UserAgent, requestTO: cfg.RequestTimeout}, nil
}

func (r *chromiumRenderer) Render(ctx context.Context, rawURL string) (RenderedPage, error) {
	tabCtx, cancel := chromedp.NewContext(r.allocator)
	defer cancel()
	runCtx, runCancel := context.WithCancel(tabCtx)
	defer runCancel()
	go func() {
		select {
		case <-ctx.Done():
			runCancel()
		case <-runCtx.Done():
		}
	}()

	var out RenderedPage
	var responseSeen bool
	var mu sync.Mutex
	chromedp.ListenTarget(tabCtx, func(ev any) {
		if e, ok := ev.(*network.EventResponseReceived); ok && e.Type == network.ResourceTypeDocument {
			mu.Lock()
			defer mu.Unlock()
			responseSeen = true
			out.StatusCode = int(e.Response.Status)
			out.ContentType = e.Response.MimeType
			out.FinalURL = e.Response.URL
		}
	})

	requestCtx, requestCancel := context.WithTimeout(runCtx, r.requestTO)
	defer requestCancel()
	err := chromedp.Run(requestCtx,
		network.Enable(),
		network.SetBlockedURLs([]string{
			"*.avif",
			"*.gif",
			"*.ico",
			"*.jpg",
			"*.jpeg",
			"*.mp4",
			"*.png",
			"*.svg",
			"*.webm",
			"*.webp",
			"*.woff",
			"*.woff2",
		}),
		emulation.SetUserAgentOverride(r.userAgent),
		chromedp.Navigate(rawURL),
		chromedp.WaitReady("body", chromedp.ByQuery),
	)
	if err != nil {
		return RenderedPage{}, err
	}

	err = chromedp.Run(runCtx,
		chromedp.Title(&out.Title),
		chromedp.AttributeValue("html", "lang", &out.Language, nil, chromedp.ByQuery),
		chromedp.OuterHTML("html", &out.HTML, chromedp.ByQuery),
		chromedp.Location(&out.FinalURL),
	)
	if err != nil {
		return RenderedPage{}, err
	}
	mu.Lock()
	defer mu.Unlock()
	if !responseSeen {
		return RenderedPage{}, errors.New("missing document response")
	}
	out.Language = strings.TrimSpace(out.Language)
	return out, nil
}

func (r *chromiumRenderer) Close() {
	r.cancel()
}
