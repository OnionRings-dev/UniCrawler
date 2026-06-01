from vectorizer.qdrant import collection_name
from vectorizer.qdrant import QdrantSink
from vectorizer.models import Chunk, DocumentVersion
from datetime import datetime, timezone
from qdrant_client import models


class DummyEmbedder:
    model = "dummy"

    def dimension(self) -> int:
        return 3

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_collection_name_is_qdrant_safe() -> None:
    assert collection_name("unicrawler", "www.Example.com/path?a=1") == (
        "unicrawler_www_example_com_path_a_1"
    )


def test_collection_name_is_bounded() -> None:
    name = collection_name("unicrawler", "x" * 300)

    assert len(name) <= 180
    assert name.startswith("unicrawler_")


def test_ensure_collection_with_in_memory_qdrant() -> None:
    sink = QdrantSink(
        url=":memory:",
        api_key=None,
        timeout=5,
        collection_prefix="unicrawler",
        collection_scope="domain",
        embedder=DummyEmbedder(),  # type: ignore[arg-type]
        batch_size=2,
    )

    sink.ensure_collection("unicrawler_example_com")

    assert sink.client.collection_exists("unicrawler_example_com")


def test_upsert_removes_surplus_chunks_for_same_version() -> None:
    sink = QdrantSink(
        url=":memory:",
        api_key=None,
        timeout=5,
        collection_prefix="unicrawler",
        collection_scope="domain",
        embedder=DummyEmbedder(),  # type: ignore[arg-type]
        batch_size=2,
    )
    document = DocumentVersion(
        document_id=10,
        version_id=20,
        url="https://example.com/a",
        domain="example.com",
        title=None,
        language=None,
        markdown="",
        content_hash="abc123",
        status_code=200,
        content_type="text/html",
        final_url=None,
        document_type="html",
        source_url=None,
        parsed_at=datetime.now(timezone.utc),
    )

    sink.upsert_document(document, [chunk(index) for index in range(4)])
    sink.upsert_document(document, [chunk(index) for index in range(2)])

    result = sink.client.count(
        collection_name="unicrawler_example_com",
        count_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=10),
                )
            ]
        ),
        exact=True,
    )
    assert result.count == 2


def chunk(index: int) -> Chunk:
    return Chunk(
        index=index,
        text=f"chunk {index}",
        token_count=2,
        start_token=index * 2,
        end_token=index * 2 + 2,
        headings=(),
        content_hash=f"hash-{index}",
    )
