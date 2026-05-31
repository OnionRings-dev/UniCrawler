from vectorizer.qdrant import collection_name
from vectorizer.qdrant import QdrantSink


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
