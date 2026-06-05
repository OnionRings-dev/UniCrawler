package main

import (
	"net/url"
	"strings"
	"testing"
)

func TestLooksLikePDFResponse(t *testing.T) {
	u, err := url.Parse("https://example.com/file.pdf")
	if err != nil {
		t.Fatal(err)
	}
	if !looksLikePDFResponse("application/pdf; charset=binary", u) {
		t.Fatal("expected application/pdf to be accepted")
	}
	if !looksLikePDFResponse("application/octet-stream", u) {
		t.Fatal("expected .pdf URL to be accepted")
	}
	html, err := url.Parse("https://example.com/file")
	if err != nil {
		t.Fatal(err)
	}
	if looksLikePDFResponse("text/html", html) {
		t.Fatal("did not expect text/html without .pdf URL")
	}
}

func TestCleanPDFMarkdown(t *testing.T) {
	got := cleanPDFMarkdown(" First line \r\n\r\n\r\nSecond line  ")
	want := "First line\n\nSecond line"
	if got != want {
		t.Fatalf("cleanPDFMarkdown() = %q, want %q", got, want)
	}
}

func TestTitleFromURL(t *testing.T) {
	u, err := url.Parse("https://example.com/docs/price-list_2026.pdf")
	if err != nil {
		t.Fatal(err)
	}
	got := titleFromURL(u)
	if !strings.Contains(got, "price list 2026") {
		t.Fatalf("titleFromURL() = %q", got)
	}
}
