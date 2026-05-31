package main

import (
	"os"
	"strings"
	"testing"
)

func TestExtractContentToMarkdown(t *testing.T) {
	raw, err := os.ReadFile("testdata/article.html")
	if err != nil {
		t.Fatal(err)
	}
	got, err := extractContent(string(raw), "https://example.com/articles/test")
	if err != nil {
		t.Fatal(err)
	}
	if got.Title != "Useful Page Title" {
		t.Fatalf("title = %q, want Useful Page Title", got.Title)
	}
	if !strings.Contains(got.Markdown, "This is the important paragraph.") {
		t.Fatalf("markdown missing article text:\n%s", got.Markdown)
	}
	if strings.Contains(got.Markdown, "Cookie settings") || strings.Contains(got.Markdown, "Footer legal text") {
		t.Fatalf("markdown contains repetitive chrome:\n%s", got.Markdown)
	}
}

func TestExtractContentRemovesCookieConsent(t *testing.T) {
	raw, err := os.ReadFile("testdata/cookie_article.html")
	if err != nil {
		t.Fatal(err)
	}
	got, err := extractContent(string(raw), "https://example.com/articles/cookies")
	if err != nil {
		t.Fatal(err)
	}
	for _, unwanted := range []string{"Consenso Cookie", "Accetta tutto", "Impostazioni cookie"} {
		if strings.Contains(got.Markdown, unwanted) {
			t.Fatalf("markdown contains cookie banner text %q:\n%s", unwanted, got.Markdown)
		}
	}
	if !strings.Contains(got.Markdown, "Dance classes start again in September.") {
		t.Fatalf("markdown missing article content:\n%s", got.Markdown)
	}
}

func TestLooksLikeCookieConsent(t *testing.T) {
	cookie := "Consenso Cookie Utilizziamo i cookie. Impostazioni cookie Accetta tutto"
	if !looksLikeCookieConsent(cookie) {
		t.Fatal("expected cookie consent text to be detected")
	}
	article := "This article explains how cookie-based sessions work in web applications."
	if looksLikeCookieConsent(article) {
		t.Fatal("technical article should not be classified as cookie consent")
	}
}

func TestCleanMarkdownCollapsesBlankLines(t *testing.T) {
	got := cleanMarkdown("a\n\n\nb  \n\n")
	want := "a\n\nb"
	if got != want {
		t.Fatalf("cleanMarkdown() = %q, want %q", got, want)
	}
}
