from __future__ import annotations

import hashlib
from dataclasses import dataclass

from vectorizer.chunking import MarkdownChunker
from vectorizer.config import Config
from vectorizer.models import Chunk
from vectorizer.preprocess import DedupResult, deduplicate_markdown


@dataclass(frozen=True)
class PreparedChunks:
    chunks: list[Chunk]
    oversized: bool
    original_chunk_count: int
    indexed_content_chunks: int
    indexed_summary_chunks: int
    dedup: DedupResult


def prepare_chunks(markdown: str, chunker: MarkdownChunker, cfg: Config) -> PreparedChunks:
    dedup = deduplicate_markdown(markdown)
    source_markdown = dedup.markdown
    if cfg.max_document_chars > 0 and len(source_markdown) > cfg.max_document_chars:
        source_markdown = source_markdown[: cfg.max_document_chars]

    chunks = chunker.split(source_markdown)
    if len(chunks) <= cfg.max_chunks_per_document:
        return PreparedChunks(
            chunks=renumber_content_chunks(chunks),
            oversized=False,
            original_chunk_count=len(chunks),
            indexed_content_chunks=len(chunks),
            indexed_summary_chunks=0,
            dedup=dedup,
        )

    content_chunks = select_representative_chunks(chunks, cfg.max_chunks_per_document)
    summary_chunks = build_summary_chunks(chunks, chunker, cfg)
    indexed = renumber(content_chunks + summary_chunks)
    return PreparedChunks(
        chunks=indexed,
        oversized=True,
        original_chunk_count=len(chunks),
        indexed_content_chunks=len(content_chunks),
        indexed_summary_chunks=len(summary_chunks),
        dedup=dedup,
    )


def select_representative_chunks(chunks: list[Chunk], limit: int) -> list[Chunk]:
    if limit <= 0:
        return []
    if len(chunks) <= limit:
        return renumber_content_chunks(chunks)
    if limit == 1:
        return [as_content_chunk(chunks[0])]

    selected: list[Chunk] = []
    last_index = len(chunks) - 1
    for slot in range(limit):
        source_index = round(slot * last_index / (limit - 1))
        selected.append(as_content_chunk(chunks[source_index]))
    return selected


def build_summary_chunks(chunks: list[Chunk], chunker: MarkdownChunker, cfg: Config) -> list[Chunk]:
    group_size = max(1, cfg.summary_group_chunks)
    groups = [chunks[start : start + group_size] for start in range(0, len(chunks), group_size)]
    if cfg.max_summary_chunks > 0 and len(groups) > cfg.max_summary_chunks:
        groups = select_representative_groups(groups, cfg.max_summary_chunks)

    summaries: list[Chunk] = [build_document_summary(chunks, chunker, cfg.summary_tokens)]
    for group in groups:
        summaries.append(build_group_summary(group, chunker, cfg.summary_tokens))
    return summaries


def select_representative_groups(groups: list[list[Chunk]], limit: int) -> list[list[Chunk]]:
    if len(groups) <= limit:
        return groups
    if limit == 1:
        return [groups[0]]
    last_index = len(groups) - 1
    return [groups[round(slot * last_index / (limit - 1))] for slot in range(limit)]


def build_document_summary(chunks: list[Chunk], chunker: MarkdownChunker, max_tokens: int) -> Chunk:
    headings = tuple(dict.fromkeys(heading for chunk in chunks for heading in chunk.headings))
    sample = select_representative_chunks(chunks, min(12, len(chunks)))
    body = summary_text(
        title=f"Document summary covering {len(chunks)} source chunks",
        headings=headings,
        chunks=sample,
        chunker=chunker,
        max_tokens=max_tokens,
    )
    return make_summary_chunk(
        text=body,
        headings=headings,
        token_count=chunker.count_tokens(body),
        kind="document_summary",
        source_start=0,
        source_end=len(chunks) - 1,
    )


def build_group_summary(group: list[Chunk], chunker: MarkdownChunker, max_tokens: int) -> Chunk:
    headings = tuple(dict.fromkeys(heading for chunk in group for heading in chunk.headings))
    body = summary_text(
        title=f"Section summary covering source chunks {group[0].index}-{group[-1].index}",
        headings=headings,
        chunks=group,
        chunker=chunker,
        max_tokens=max_tokens,
    )
    return make_summary_chunk(
        text=body,
        headings=headings,
        token_count=chunker.count_tokens(body),
        kind="section_summary",
        source_start=group[0].index,
        source_end=group[-1].index,
    )


def summary_text(
    title: str,
    headings: tuple[str, ...],
    chunks: list[Chunk],
    chunker: MarkdownChunker,
    max_tokens: int,
) -> str:
    lines = [title]
    if headings:
        lines.append("Headings: " + " > ".join(headings[:12]))
    lines.append("Key passages:")

    seen: set[str] = set()
    for chunk in chunks:
        for passage in candidate_passages(chunk.text):
            key = passage.lower()
            if key in seen:
                continue
            seen.add(key)
            candidate = "\n".join([*lines, f"- {passage}"])
            if chunker.count_tokens(candidate) > max_tokens:
                return "\n".join(lines).strip()
            lines.append(f"- {passage}")
    return "\n".join(lines).strip()


def candidate_passages(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip(" -\t")
        if not line or line.startswith("Context:"):
            continue
        if len(line) < 30:
            continue
        out.append(line[:500])
        if len(out) >= 3:
            break
    if out:
        return out
    compact = " ".join(text.split())
    return [compact[:500]] if compact else []


def renumber_content_chunks(chunks: list[Chunk]) -> list[Chunk]:
    return renumber([as_content_chunk(chunk) for chunk in chunks])


def renumber(chunks: list[Chunk]) -> list[Chunk]:
    return [
        Chunk(
            index=index,
            text=chunk.text,
            token_count=chunk.token_count,
            start_token=chunk.start_token,
            end_token=chunk.end_token,
            headings=chunk.headings,
            content_hash=chunk.content_hash,
            kind=chunk.kind,
            source_chunk_start=chunk.source_chunk_start,
            source_chunk_end=chunk.source_chunk_end,
        )
        for index, chunk in enumerate(chunks)
    ]


def as_content_chunk(chunk: Chunk) -> Chunk:
    return Chunk(
        index=chunk.index,
        text=chunk.text,
        token_count=chunk.token_count,
        start_token=chunk.start_token,
        end_token=chunk.end_token,
        headings=chunk.headings,
        content_hash=chunk.content_hash,
        kind="content",
        source_chunk_start=chunk.index,
        source_chunk_end=chunk.index,
    )


def make_summary_chunk(
    text: str,
    headings: tuple[str, ...],
    token_count: int,
    kind: str,
    source_start: int,
    source_end: int,
) -> Chunk:
    return Chunk(
        index=0,
        text=text,
        token_count=token_count,
        start_token=0,
        end_token=token_count,
        headings=headings,
        content_hash=hash_text(f"{kind}:{source_start}:{source_end}:{text}"),
        kind=kind,
        source_chunk_start=source_start,
        source_chunk_end=source_end,
    )


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

