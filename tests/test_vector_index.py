"""Unit tests for local vector-index resilience without model downloads."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_agent.vector_index import FastEmbedQdrantIndex, VectorChunk


class _Vector:
    def tolist(self) -> list[float]:
        return [0.1, 0.2]


class _MemoryConstrainedModel:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def passage_embed(self, texts: list[str], *, batch_size: int) -> list[_Vector]:
        self.batch_sizes.append(batch_size)
        if batch_size > 1:
            raise MemoryError("simulated ONNX allocation failure")
        return [_Vector() for _text in texts]


class _Client:
    def __init__(self) -> None:
        self.upserted: list[Any] = []

    def scroll(self, **_kwargs: Any) -> tuple[list[Any], None]:
        return [], None

    def upsert(self, *, points: list[Any], **_kwargs: Any) -> None:
        self.upserted.extend(points)

    def delete(self, **_kwargs: Any) -> None:
        raise AssertionError("No stale point should be deleted in this fixture")


class _Models:
    class MatchValue:
        def __init__(self, **values: Any) -> None:
            self.values = values

    class FieldCondition:
        def __init__(self, **values: Any) -> None:
            self.values = values

    class Filter:
        def __init__(self, **values: Any) -> None:
            self.values = values

    class PointStruct(SimpleNamespace):
        pass

    class PointIdsList(SimpleNamespace):
        pass


def test_embedding_batch_reduces_after_memory_error(tmp_path: Path) -> None:
    index = FastEmbedQdrantIndex(tmp_path / "qdrant", batch_size=4)
    model = _MemoryConstrainedModel()
    client = _Client()
    index._ready = True
    index._model = model
    index._client = client
    index._models = _Models

    result = index.replace_source(
        "file://fixture.txt",
        [
            VectorChunk(
                source="file://fixture.txt",
                kind="file",
                chunk_index=chunk_index,
                content=f"chunk {chunk_index}",
            )
            for chunk_index in range(3)
        ],
    )

    assert result is True
    assert model.batch_sizes[:3] == [4, 2, 1]
    assert index.batch_size == 1
    assert len(client.upserted) == 3
    assert all("content" not in point.payload for point in client.upserted)
    assert all("content_sha256" in point.payload for point in client.upserted)
