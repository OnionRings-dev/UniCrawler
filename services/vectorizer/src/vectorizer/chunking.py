from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import tiktoken

from vectorizer.models import Chunk

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class Block:
    text: str
    headings: tuple[str, ...]


class MarkdownChunker:
    def __init__(
        self,
        chunk_tokens: int,
        overlap_tokens: int,
        min_chunk_tokens: int,
        encoding_name: str = "cl100k_base",
    ) -> None:
        self.chunk_tokens = chunk_tokens
        self.overlap_tokens = overlap_tokens
        self.min_chunk_tokens = min_chunk_tokens
        self.encoding = tiktoken.get_encoding(encoding_name)

    def split(self, markdown: str) -> list[Chunk]:
        blocks = split_markdown_blocks(markdown)
        windows: list[tuple[str, tuple[str, ...]]] = []
        current: list[Block] = []
        current_tokens = 0

        for block in blocks:
            tokens = self.count_tokens(block.text)
            if tokens > self.chunk_tokens:
                if current:
                    windows.append((join_blocks(current), current[-1].headings))
                    current = []
                    current_tokens = 0
                for part in self.split_large_block(block):
                    windows.append((part.text, part.headings))
                continue

            if current and current_tokens + tokens > self.chunk_tokens:
                windows.append((join_blocks(current), current[-1].headings))
                current, current_tokens = self.overlap_tail(current)

            current.append(block)
            current_tokens += tokens

        if current:
            windows.append((join_blocks(current), current[-1].headings))

        return self.to_chunks(windows)

    def split_large_block(self, block: Block) -> list[Block]:
        tokens = self.encoding.encode(block.text)
        out: list[Block] = []
        step = max(1, self.chunk_tokens - self.overlap_tokens)
        for start in range(0, len(tokens), step):
            piece = self.encoding.decode(tokens[start : start + self.chunk_tokens]).strip()
            if piece:
                out.append(Block(piece, block.headings))
        return out

    def overlap_tail(self, blocks: list[Block]) -> tuple[list[Block], int]:
        if self.overlap_tokens <= 0:
            return [], 0
        selected: list[Block] = []
        total = 0
        for block in reversed(blocks):
            tokens = self.count_tokens(block.text)
            if selected and total + tokens > self.overlap_tokens:
                break
            selected.append(block)
            total += tokens
        selected.reverse()
        return selected, total

    def to_chunks(self, windows: list[tuple[str, tuple[str, ...]]]) -> list[Chunk]:
        chunks: list[Chunk] = []
        cursor = 0
        for text, headings in windows:
            text = with_heading_context(text.strip(), headings)
            token_count = self.count_tokens(text)
            if token_count < self.min_chunk_tokens and chunks:
                previous = chunks[-1]
                merged = f"{previous.text}\n\n{text}".strip()
                chunks[-1] = Chunk(
                    index=previous.index,
                    text=merged,
                    token_count=self.count_tokens(merged),
                    start_token=previous.start_token,
                    end_token=cursor + token_count,
                    headings=previous.headings,
                    content_hash=hash_text(merged),
                )
            else:
                chunks.append(
                    Chunk(
                        index=len(chunks),
                        text=text,
                        token_count=token_count,
                        start_token=cursor,
                        end_token=cursor + token_count,
                        headings=headings,
                        content_hash=hash_text(text),
                    )
                )
            cursor += max(1, token_count - self.overlap_tokens)
        return chunks

    def count_tokens(self, text: str) -> int:
        return len(self.encoding.encode(text))


def split_markdown_blocks(markdown: str) -> list[Block]:
    blocks: list[Block] = []
    headings: list[str] = []
    buffer: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            blocks.append(Block(text=text, headings=tuple(headings)))
        buffer.clear()

    for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            buffer.append(line)
            continue

        if not in_fence:
            match = HEADING_RE.match(line)
            if match:
                flush()
                level = len(match.group(1))
                title = match.group(2).strip()
                headings = headings[: level - 1] + [title]
                buffer.append(line)
                continue
            if not line.strip():
                flush()
                continue

        buffer.append(line)

    flush()
    return blocks


def join_blocks(blocks: list[Block]) -> str:
    return "\n\n".join(block.text for block in blocks).strip()


def with_heading_context(text: str, headings: tuple[str, ...]) -> str:
    if not headings:
        return text
    present = [heading for heading in headings if heading in text[:300]]
    missing = [heading for heading in headings if heading not in present]
    if not missing:
        return text
    prefix = " > ".join(missing)
    return f"Context: {prefix}\n\n{text}"


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

