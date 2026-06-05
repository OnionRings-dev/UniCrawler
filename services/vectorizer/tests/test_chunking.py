from vectorizer.chunking import MarkdownChunker


def test_markdown_chunker_preserves_heading_context() -> None:
    markdown = """
# Product

Intro paragraph with enough words to make this chunk meaningful for retrieval.

## Pricing

The basic plan includes crawling, parsing, and vectorization for a small site.

The pro plan adds more concurrency, retries, and operational support.
""".strip()
    chunker = MarkdownChunker(chunk_tokens=35, overlap_tokens=8, min_chunk_tokens=5)

    chunks = chunker.split(markdown)

    assert len(chunks) >= 2
    assert any("Pricing" in chunk.text for chunk in chunks)
    assert all(chunk.content_hash for chunk in chunks)


def test_large_paragraph_is_split_with_overlap() -> None:
    markdown = " ".join(f"token-{index}" for index in range(220))
    chunker = MarkdownChunker(chunk_tokens=60, overlap_tokens=10, min_chunk_tokens=5)

    chunks = chunker.split(markdown)

    assert len(chunks) > 1
    assert chunks[0].index == 0
    assert chunks[1].start_token < chunks[0].end_token
