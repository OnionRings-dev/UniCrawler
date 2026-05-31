package main

import (
	"errors"
	"net/url"
	"strings"

	md "github.com/JohannesKaufmann/html-to-markdown"
	"github.com/PuerkitoBio/goquery"
	"github.com/go-shiori/go-readability"
)

type extractedContent struct {
	Title    string
	Markdown string
}

func extractContent(renderedHTML string, finalURL string) (extractedContent, error) {
	if strings.TrimSpace(renderedHTML) == "" {
		return extractedContent{}, errors.New("empty rendered html")
	}
	base, err := url.Parse(finalURL)
	if err != nil {
		return extractedContent{}, err
	}
	cleanHTML, err := stripBoilerplateHTML(renderedHTML)
	if err != nil {
		return extractedContent{}, err
	}
	article, err := readability.FromReader(strings.NewReader(cleanHTML), base)
	if err != nil {
		return extractedContent{}, err
	}
	content := strings.TrimSpace(article.Content)
	if content == "" {
		return extractedContent{}, errors.New("empty readable content")
	}
	converter := md.NewConverter(base.String(), true, nil)
	markdown, err := converter.ConvertString(content)
	if err != nil {
		return extractedContent{}, err
	}
	markdown = cleanMarkdown(markdown)
	if markdown == "" {
		return extractedContent{}, errors.New("empty markdown")
	}
	return extractedContent{Title: strings.TrimSpace(article.Title), Markdown: markdown}, nil
}

func stripBoilerplateHTML(renderedHTML string) (string, error) {
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(renderedHTML))
	if err != nil {
		return "", err
	}

	doc.Find(strings.Join([]string{
		"script",
		"style",
		"noscript",
		"template",
		"[aria-modal='true']",
		"[role='dialog']",
		"[id*='cookie' i]",
		"[class*='cookie' i]",
		"[id*='consent' i]",
		"[class*='consent' i]",
		"[id*='gdpr' i]",
		"[class*='gdpr' i]",
		"[id*='iubenda' i]",
		"[class*='iubenda' i]",
		"[id*='cookiebot' i]",
		"[class*='cookiebot' i]",
		"[id*='complianz' i]",
		"[class*='complianz' i]",
		"[id*='cmplz' i]",
		"[class*='cmplz' i]",
		"[id*='moove_gdpr' i]",
		"[class*='moove_gdpr' i]",
	}, ",")).Remove()

	doc.Find("aside, div, section, form, dialog, footer, header, nav").Each(func(_ int, sel *goquery.Selection) {
		text := compactText(sel.Text())
		if looksLikeCookieConsent(text) {
			sel.Remove()
		}
	})

	html, err := doc.Html()
	if err != nil {
		return "", err
	}
	return html, nil
}

func looksLikeCookieConsent(text string) bool {
	if text == "" || len(text) > 2500 {
		return false
	}
	lower := strings.ToLower(text)
	hasCookieWord := strings.Contains(lower, "cookie") || strings.Contains(lower, "cookies")
	if !hasCookieWord {
		return false
	}

	signals := 0
	for _, term := range []string{
		"accetta",
		"accept",
		"consenso",
		"consent",
		"impostazioni",
		"settings",
		"preferenze",
		"preferences",
		"gdpr",
		"privacy",
		"rifiuta",
		"reject",
	} {
		if strings.Contains(lower, term) {
			signals++
		}
	}
	return signals >= 2
}

func compactText(text string) string {
	return strings.Join(strings.Fields(text), " ")
}

func cleanMarkdown(value string) string {
	lines := strings.Split(strings.ReplaceAll(value, "\r\n", "\n"), "\n")
	out := make([]string, 0, len(lines))
	blank := false
	for _, line := range lines {
		line = strings.TrimRight(line, " \t")
		if strings.TrimSpace(line) == "" {
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
