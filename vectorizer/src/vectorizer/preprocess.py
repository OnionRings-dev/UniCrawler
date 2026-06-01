from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from vectorizer.chunking import Block, join_blocks, split_markdown_blocks

SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class DedupResult:
    markdown: str
    original_blocks: int
    kept_blocks: int
    removed_blocks: int
    original_chars: int
    deduped_chars: int


def deduplicate_markdown(markdown: str) -> DedupResult:
    blocks = split_markdown_blocks(markdown)
    seen: set[str] = set()
    kept: list[Block] = []

    for block in blocks:
        key = block_key(block.text)
        if key in seen:
            continue
        seen.add(key)
        kept.append(block)

    deduped = join_blocks(kept)
    return DedupResult(
        markdown=deduped,
        original_blocks=len(blocks),
        kept_blocks=len(kept),
        removed_blocks=len(blocks) - len(kept),
        original_chars=len(markdown),
        deduped_chars=len(deduped),
    )


def block_key(text: str) -> str:
    normalized = SPACE_RE.sub(" ", text).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

