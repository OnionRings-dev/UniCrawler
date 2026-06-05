package main

import (
	"bytes"
	"encoding/json"
	"net/url"
	"testing"

	"unicrawler/shared/contracts"
)

func TestNormalizeURL(t *testing.T) {
	got, err := normalizeURL("HTTPS://Example.com:443/a/../Page/?b=2#section")
	if err != nil {
		t.Fatal(err)
	}
	want := "https://example.com/Page/?b=2"
	if got.String() != want {
		t.Fatalf("normalizeURL() = %q, want %q", got.String(), want)
	}
}

func TestDomainForURL(t *testing.T) {
	u, err := url.Parse("https://docs.example.co.uk/path")
	if err != nil {
		t.Fatal(err)
	}
	got, err := domainForURL(u)
	if err != nil {
		t.Fatal(err)
	}
	if got != "example.co.uk" {
		t.Fatalf("domainForURL() = %q, want %q", got, "example.co.uk")
	}
}

func TestHashBytesStable(t *testing.T) {
	first := hashBytes("https://example.com/")
	second := hashBytes("https://example.com/")
	if !bytes.Equal(first, second) {
		t.Fatal("hashBytes is not stable")
	}
	if len(first) != 32 {
		t.Fatalf("hashBytes length = %d, want 32", len(first))
	}
}

func TestIsSkippedAssetURL(t *testing.T) {
	tests := []struct {
		raw  string
		want bool
	}{
		{raw: "https://example.com/wp-content/uploads/photo.jpg", want: true},
		{raw: "https://example.com/downloads/file.pdf?x=1", want: false},
		{raw: "https://example.com/articles/story", want: false},
		{raw: "https://example.com/articles/story.html", want: false},
	}

	for _, tt := range tests {
		t.Run(tt.raw, func(t *testing.T) {
			u, err := normalizeURL(tt.raw)
			if err != nil {
				t.Fatal(err)
			}
			if got := isSkippedAssetURL(u); got != tt.want {
				t.Fatalf("isSkippedAssetURL() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestIsPDFURL(t *testing.T) {
	u, err := normalizeURL("https://example.com/files/menu.PDF?download=1")
	if err != nil {
		t.Fatal(err)
	}
	if !isPDFURL(u) {
		t.Fatal("expected PDF URL")
	}
}

func TestContentChanged(t *testing.T) {
	current := hashBytes("body")
	if !contentChanged(nil, current) {
		t.Fatal("missing previous hash should be changed")
	}
	if contentChanged(current, current) {
		t.Fatal("equal hashes should be unchanged")
	}
	if !contentChanged(current, hashBytes("new body")) {
		t.Fatal("different hashes should be changed")
	}
}

func TestVectorizeRequestJSON(t *testing.T) {
	data, err := contracts.MarshalEnvelope(contracts.VectorizeRequestType, contracts.VectorizeRequest{
		DocumentID: 123,
		VersionID:  456,
	})
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["type"] != contracts.VectorizeRequestType {
		t.Fatalf("unexpected json payload: %s", data)
	}
	payload := decoded["payload"].(map[string]any)
	if payload["document_id"].(float64) != 123 || payload["version_id"].(float64) != 456 {
		t.Fatalf("unexpected json payload: %s", data)
	}
}
