package contracts

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

const (
	CrawlRequestType     = "crawl.request.v1"
	ParseRequestType     = "parse.request.v1"
	VectorizeRequestType = "vectorize.request.v1"
	DeadLetterType       = "dead_letter.v1"
)

type Envelope struct {
	Type    string          `json:"type"`
	Version int             `json:"version"`
	Payload json.RawMessage `json:"payload"`
}

type CrawlRequest struct {
	SeedURL string `json:"seed_url"`
}

type ParseRequest struct {
	URLID      int64 `json:"url_id"`
	DomainID   int64 `json:"domain_id"`
	CrawlRunID int64 `json:"crawl_run_id"`
}

type VectorizeRequest struct {
	DocumentID int64 `json:"document_id"`
	VersionID  int64 `json:"version_id"`
}

type DeadLetter struct {
	Original json.RawMessage `json:"original"`
	Service  string          `json:"service"`
	Error    string          `json:"error"`
	Attempt  int             `json:"attempt"`
	FailedAt time.Time       `json:"failed_at"`
}

func MarshalEnvelope(messageType string, payload any) ([]byte, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	return json.Marshal(Envelope{Type: messageType, Version: 1, Payload: body})
}

func ParseCrawlRequest(raw string) (CrawlRequest, error) {
	var env Envelope
	if err := json.Unmarshal([]byte(raw), &env); err != nil {
		return CrawlRequest{}, err
	}
	if env.Type != CrawlRequestType || env.Version != 1 {
		return CrawlRequest{}, fmt.Errorf("unexpected message %q version %d", env.Type, env.Version)
	}
	var payload CrawlRequest
	if err := json.Unmarshal(env.Payload, &payload); err != nil {
		return CrawlRequest{}, err
	}
	if payload.SeedURL == "" {
		return CrawlRequest{}, errors.New("missing seed_url")
	}
	return payload, nil
}

func ParseParseRequest(raw string) (ParseRequest, error) {
	var env Envelope
	if err := json.Unmarshal([]byte(raw), &env); err != nil {
		return ParseRequest{}, err
	}
	if env.Type != ParseRequestType || env.Version != 1 {
		return ParseRequest{}, fmt.Errorf("unexpected message %q version %d", env.Type, env.Version)
	}
	var payload ParseRequest
	if err := json.Unmarshal(env.Payload, &payload); err != nil {
		return ParseRequest{}, err
	}
	if payload.URLID <= 0 || payload.DomainID <= 0 {
		return ParseRequest{}, errors.New("missing url_id or domain_id")
	}
	return payload, nil
}
