package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"path"
	"strings"

	"github.com/ledongthuc/pdf"
)

type parsedPDF struct {
	Title       string
	Markdown    string
	StatusCode  int
	ContentType string
	FinalURL    string
}

func fetchAndParsePDF(ctx context.Context, client *http.Client, rawURL string, userAgent string, maxBytes int64) (parsedPDF, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return parsedPDF{}, err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "application/pdf,*/*;q=0.5")

	resp, err := client.Do(req)
	if err != nil {
		return parsedPDF{}, err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return parsedPDF{}, fmt.Errorf("unexpected pdf status %d", resp.StatusCode)
	}
	contentType := resp.Header.Get("Content-Type")
	if !looksLikePDFResponse(contentType, resp.Request.URL) {
		return parsedPDF{}, fmt.Errorf("unsupported pdf content-type %q", contentType)
	}
	if maxBytes <= 0 {
		maxBytes = 50 << 20
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBytes+1))
	if err != nil {
		return parsedPDF{}, err
	}
	if int64(len(body)) > maxBytes {
		return parsedPDF{}, fmt.Errorf("pdf exceeds max size %d bytes", maxBytes)
	}
	if !bytes.HasPrefix(bytes.TrimSpace(body), []byte("%PDF-")) {
		return parsedPDF{}, errors.New("response is not a pdf")
	}

	reader, err := pdf.NewReader(bytes.NewReader(body), int64(len(body)))
	if err != nil {
		return parsedPDF{}, err
	}
	textReader, err := reader.GetPlainText()
	if err != nil {
		return parsedPDF{}, err
	}
	text, err := io.ReadAll(textReader)
	if err != nil {
		return parsedPDF{}, err
	}
	markdown := cleanPDFMarkdown(string(text))
	if markdown == "" {
		return parsedPDF{}, errors.New("empty pdf text")
	}
	return parsedPDF{
		Title:       titleFromURL(resp.Request.URL),
		Markdown:    markdown,
		StatusCode:  resp.StatusCode,
		ContentType: contentType,
		FinalURL:    resp.Request.URL.String(),
	}, nil
}

func looksLikePDFResponse(contentType string, finalURL *url.URL) bool {
	mediaType, _, err := mime.ParseMediaType(contentType)
	if err == nil && strings.EqualFold(mediaType, "application/pdf") {
		return true
	}
	return finalURL != nil && isPDFURL(finalURL)
}

func cleanPDFMarkdown(text string) string {
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	lines := strings.Split(text, "\n")
	out := make([]string, 0, len(lines))
	blank := false
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			if !blank && len(out) > 0 {
				out = append(out, "")
			}
			blank = true
			continue
		}
		out = append(out, line)
		blank = false
	}
	return strings.TrimSpace(strings.Join(out, "\n"))
}

func titleFromURL(u *url.URL) string {
	if u == nil {
		return ""
	}
	base := path.Base(u.Path)
	base = strings.TrimSuffix(base, path.Ext(base))
	base, _ = url.PathUnescape(base)
	base = strings.ReplaceAll(base, "-", " ")
	base = strings.ReplaceAll(base, "_", " ")
	return strings.TrimSpace(base)
}
