from __future__ import annotations

from dataclasses import dataclass

from rag_demo.agent import (
    ToolCallSummary,
    format_tool_calls,
    keyword_score,
    merge_deduped_hits,
    normalize_scores,
    rank_hits,
    sources_from_hits,
    strip_model_sources,
)
from rag_demo.retriever import RetrievalHit


def hit(
    *,
    url: str,
    title: str,
    text: str,
    score: float = 0.5,
    point_id: str = "point",
    collection: str = "collection",
    headings: list[str] | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        collection=collection,
        point_id=point_id,
        score=score,
        payload={
            "url": url,
            "title": title,
            "text": text,
            "headings": headings or [],
            "chunk_id": 0,
        },
    )


@dataclass
class MockReranker:
    scores: list[float]
    provider: str = "mock"
    model: str = "mock-model"

    def score(self, question: str, hits: list[RetrievalHit]) -> list[float]:
        return self.scores[: len(hits)]


def test_merge_deduped_hits_keeps_best_score_per_url() -> None:
    hits_by_source: dict[tuple[str, str], RetrievalHit] = {}
    low = hit(url="https://example.com/a", title="A", text="", score=0.2, point_id="low")
    high = hit(url="https://example.com/a", title="A", text="", score=0.9, point_id="high")

    merge_deduped_hits(hits_by_source, [low, high])

    assert len(hits_by_source) == 1
    assert next(iter(hits_by_source.values())).point_id == "high"


def test_keyword_score_boosts_title_headings_and_text_matches() -> None:
    relevant = hit(
        url="https://example.com/iscrizioni",
        title="Iscrizioni online",
        headings=["Portale ministeriale"],
        text="Accesso con SPID CIE eIDAS per iscrizione scuola.",
    )
    generic = hit(
        url="https://example.com/ptof",
        title="PTOF",
        text="Documento generale della scuola.",
    )

    assert keyword_score("Come faccio ad iscrivermi a scuola?", relevant) > keyword_score(
        "Come faccio ad iscrivermi a scuola?",
        generic,
    )


def test_rank_hits_uses_normalized_reranker_scores() -> None:
    first = hit(url="https://example.com/first", title="Generic", text="", score=0.8)
    second = hit(url="https://example.com/second", title="Exact", text="", score=0.1)
    ranked = rank_hits(
        "question",
        [first, second],
        reranker=MockReranker([0.0, 10.0]),
        keyword_boost=False,
    )

    assert ranked[0].hit is second
    assert normalize_scores([0.0, 10.0]) == [0.0, 1.0]


def test_sources_from_hits_respects_limit_and_dedups_urls() -> None:
    hits = [
        hit(url="https://example.com/a", title="A", text="", point_id="1"),
        hit(url="https://example.com/a", title="A duplicate", text="", point_id="2"),
        hit(url="https://example.com/b", title="B", text="", point_id="3"),
    ]

    sources = sources_from_hits(hits, limit=1)

    assert len(sources) == 1
    assert sources[0].url == "https://example.com/a"


def test_strip_model_sources_removes_model_generated_section() -> None:
    answer = "Risposta utile.\n\nFonti:\n- fonte inventata"

    assert strip_model_sources(answer) == "Risposta utile."


def test_format_tool_calls_shows_rerank_minimally() -> None:
    rendered = format_tool_calls(
        [
            ToolCallSummary(
                name="rerank",
                args={"candidates": 20, "provider": "local", "model": "cross-encoder/model"},
            )
        ]
    )

    assert "`rerank`" in rendered
    assert "candidates=20" in rendered
    assert "provider='local'" in rendered
