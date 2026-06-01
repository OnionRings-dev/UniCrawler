from vectorizer.chunking import MarkdownChunker
from vectorizer.config import Config
from vectorizer.hierarchy import prepare_chunks
from vectorizer.preprocess import deduplicate_markdown


def config(max_chunks: int = 5) -> Config:
    return Config(
        redis_addr="redis:6379",
        redis_password="",
        redis_db=0,
        input_queue="parser:out",
        processing_queue="vectorizer:processing",
        failed_queue="vectorizer:failed",
        oversized_queue="vectorizer:oversized",
        queue_block_time=5,
        redis_socket_timeout=15,
        postgres_dsn="postgres://example",
        qdrant_url=":memory:",
        qdrant_api_key=None,
        qdrant_timeout=30,
        collection_prefix="unicrawler",
        collection_scope="domain",
        embedding_provider="fastembed",
        embedding_model="dummy",
        openai_api_key=None,
        batch_size=2,
        chunk_tokens=35,
        chunk_overlap_tokens=5,
        min_chunk_tokens=5,
        max_document_chars=0,
        max_chunks_per_document=max_chunks,
        summary_group_chunks=3,
        max_summary_chunks=10,
        summary_tokens=80,
        max_retries=3,
        retry_backoff_seconds=1.0,
    )


def test_deduplicate_markdown_removes_repeated_blocks() -> None:
    markdown = """
# Staff

Repeated biography block with enough text to count as a meaningful paragraph.

Repeated biography block with enough text to count as a meaningful paragraph.

Unique biography block with enough text to survive the deduplication pass.
""".strip()

    result = deduplicate_markdown(markdown)

    assert result.removed_blocks == 1
    assert "Unique biography" in result.markdown


def test_prepare_chunks_uses_hierarchy_for_oversized_documents() -> None:
    markdown = "\n\n".join(
        f"## Person {index}\n\nBiography paragraph {index} with enough detail "
        "about classes, projects, responsibilities, and school activities."
        for index in range(40)
    )
    chunker = MarkdownChunker(chunk_tokens=35, overlap_tokens=5, min_chunk_tokens=5)

    prepared = prepare_chunks(markdown, chunker, config(max_chunks=6))

    assert prepared.oversized
    assert prepared.original_chunk_count > 6
    assert prepared.indexed_content_chunks == 6
    assert prepared.indexed_summary_chunks > 0
    assert any(chunk.kind == "document_summary" for chunk in prepared.chunks)
    assert any(chunk.kind == "section_summary" for chunk in prepared.chunks)
    assert all(chunk.source_chunk_start is not None for chunk in prepared.chunks)
