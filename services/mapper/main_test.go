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

func TestReplayDomainKey(t *testing.T) {
	tests := []struct {
		name string
		raw  string
		mode string
		want string
	}{
		{name: "url registrable", raw: "https://ui.shadcn.com/docs", mode: "registrable", want: "shadcn.com"},
		{name: "host registrable", raw: "ui.shadcn.com", mode: "registrable", want: "shadcn.com"},
		{name: "host mode", raw: "ui.shadcn.com", mode: "host", want: "ui.shadcn.com"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := replayDomainKey(tt.raw, tt.mode)
			if err != nil {
				t.Fatal(err)
			}
			if got != tt.want {
				t.Fatalf("replayDomainKey() = %q, want %q", got, tt.want)
			}
		})
	}
}
