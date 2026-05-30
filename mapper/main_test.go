package main

import (
	"net/url"
	"reflect"
	"strings"
	"testing"
)

func TestNormalizeURL(t *testing.T) {
	base, err := url.Parse("https://Example.com/dir/page.html")
	if err != nil {
		t.Fatal(err)
	}

	got, err := normalizeURL("../Next/?b=2#section", base)
	if err != nil {
		t.Fatal(err)
	}

	want := "https://example.com/Next/?b=2"
	if got.String() != want {
		t.Fatalf("normalizeURL() = %q, want %q", got.String(), want)
	}
}

func TestExtractLinksIgnoresAssets(t *testing.T) {
	html := `<html><body>
		<a href="/one">one</a>
		<img src="/asset.png">
		<script src="/app.js"></script>
		<area href="/two">
	</body></html>`

	got := extractLinks(strings.NewReader(html))
	want := []string{"/one", "/two"}

	if !reflect.DeepEqual(got, want) {
		t.Fatalf("extractLinks() = %#v, want %#v", got, want)
	}
}
