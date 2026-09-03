"""Lazy local FastEmbed and Qdrant vector index with safe degradation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol
from uuid import NAMESPACE_URL, uuid5

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DISTANCE = "cosine"
EMBEDDING_DIMENSION = 384
EMBEDDING_LICENSE = "apache-2.0"
EMBEDDING_PREFIXES = "none"


@dataclass(frozen=True, slots=True)
class VectorChunk:
    """One context chunk to persist in the vector index."""

    source: str
    kind: str
    chunk_index: int
    content: str


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    """One semantic search result returned by the vector index."""

    source: str
    kind: str
    chunk_index: int
    content: str
    score: float


class VectorIndex(Protocol):
    """Interface used by ContextStore without coupling tests to ONNX."""

    def replace_source(self, source: str, chunks: list[VectorChunk]) -> bool:
        """Replace all vector points for a source."""

    def has_source(self, source: str) -> bool:
        """Return whether at least one vector point exists for a source."""

    def search(
        self,
        query: str,
        *,
        limit: int,
        source: str | None = None,
    ) -> list[VectorSearchHit]:
        """Return semantic matches."""

    def status(self) -> dict[str, object]:
        """Return non-secret health and configuration metadata."""

    def close(self) -> None:
        """Release local vector index resources."""


def automatic_batch_size() -> int:
    """Choose a conservative embedding batch size from available system memory."""

    available = _available_memory_bytes()
    if available is None:
        return 16
    if available >= 16 * 1024**3:
        return 64
    if available >= 8 * 1024**3:
        return 32
    return 8


def _available_memory_bytes() -> int | None:
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.available_physical)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
    try:
        sysconf = vars(os).get("sysconf")
        if not callable(sysconf):
            return None
        page_size = sysconf("SC_PAGE_SIZE")
        available_pages = sysconf("SC_AVPHYS_PAGES")
        return int(page_size * available_pages)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


class FastEmbedQdrantIndex:
    """Persistent local vector index; model and database open on first use."""

    _registry_lock: ClassVar[threading.RLock] = threading.RLock()
    _registry: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(
        self,
        path: Path,
        *,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        cache_dir: Path | None = None,
        batch_size: int | None = None,
        enabled: bool = True,
    ) -> None:
        self.path = path.resolve()
        self.model_name = model_name.strip()
        self.cache_dir = (cache_dir or self.path.parent / "embedding-cache").resolve()
        self.batch_size = batch_size or automatic_batch_size()
        self.enabled = enabled
        try:
            fastembed_version = importlib.metadata.version("fastembed")
        except importlib.metadata.PackageNotFoundError:
            fastembed_version = "unavailable"
        self.embedding_signature = (
            f"{self.model_name}|fastembed={fastembed_version}|"
            f"distance={EMBEDDING_DISTANCE}|prefixes={EMBEDDING_PREFIXES}"
        )
        signature = hashlib.sha256(self.embedding_signature.encode()).hexdigest()[:16]
        self.collection_name = f"context_{signature}"
        self._model: Any | None = None
        self._client: Any | None = None
        self._models: Any | None = None
        self._lock = threading.RLock()
        self._operation_lock = threading.RLock()
        self._last_error: str | None = None
        self._ready = False
        self._registered = False
        self._registry_key = f"{self.path}|{self.model_name}"

    def _ensure_ready(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self._ready:
                return True
            type(self)._registry_lock.acquire()
            try:
                shared = type(self)._registry.get(self._registry_key)
                if shared is not None:
                    shared["references"] = int(shared["references"]) + 1
                    self._model = shared["model"]
                    self._client = shared["client"]
                    self._models = shared["models"]
                    self._operation_lock = shared["operation_lock"]
                    self._ready = True
                    self._registered = True
                    self._last_error = None
                    return True
                from fastembed import TextEmbedding
                from qdrant_client import QdrantClient, models

                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir),
                    lazy_load=True,
                    threads=max(1, min(os.cpu_count() or 1, 8)),
                )
                dimension = TextEmbedding.get_embedding_size(self.model_name)
                self._client = QdrantClient(path=str(self.path))
                self._models = models
                if not self._client.collection_exists(self.collection_name):
                    self._client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=models.VectorParams(
                            size=dimension,
                            distance=models.Distance.COSINE,
                        ),
                    )
                self._ready = True
                self._registered = True
                type(self)._registry[self._registry_key] = {
                    "model": self._model,
                    "client": self._client,
                    "models": self._models,
                    "operation_lock": self._operation_lock,
                    "references": 1,
                }
                self._last_error = None
                return True
            except Exception as exc:  # vector failure must never break lexical search
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._ready = False
                self._model = None
                if self._client is not None:
                    self._client.close()
                self._client = None
                self._models = None
                return False
            finally:
                type(self)._registry_lock.release()

    def replace_source(self, source: str, chunks: list[VectorChunk]) -> bool:
        """Replace one source without deleting the last usable vectors first."""

        if not self._ensure_ready():
            return False
        assert self._client is not None
        assert self._model is not None
        assert self._models is not None
        try:
            source_filter = self._models.Filter(
                must=[
                    self._models.FieldCondition(
                        key="source",
                        match=self._models.MatchValue(value=source),
                    )
                ]
            )
            with self._operation_lock:
                old_ids = self._source_point_ids(source_filter)
                new_ids: set[str] = set()
                start = 0
                active_batch_size = self.batch_size
                while start < len(chunks):
                    batch = chunks[start : start + active_batch_size]
                    try:
                        vectors = list(
                            self._model.passage_embed(
                                [chunk.content for chunk in batch],
                                batch_size=active_batch_size,
                            )
                        )
                    except Exception as exc:
                        if active_batch_size <= 1 or not _is_memory_error(exc):
                            raise
                        active_batch_size = max(1, active_batch_size // 2)
                        continue
                    points = [
                        self._models.PointStruct(
                            id=str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"{self.model_name}:{chunk.source}:"
                                    f"{chunk.chunk_index}",
                                )
                            ),
                            vector=vector.tolist(),
                            payload={
                                "source": chunk.source,
                                "kind": chunk.kind,
                                "chunk_index": chunk.chunk_index,
                                "content_sha256": hashlib.sha256(
                                    chunk.content.encode("utf-8")
                                ).hexdigest(),
                            },
                        )
                        for chunk, vector in zip(batch, vectors, strict=True)
                    ]
                    new_ids.update(str(point.id) for point in points)
                    if points:
                        self._client.upsert(
                            collection_name=self.collection_name,
                            points=points,
                            wait=True,
                        )
                    start += len(batch)
                stale_ids = sorted(old_ids - new_ids)
                if stale_ids:
                    self._client.delete(
                        collection_name=self.collection_name,
                        points_selector=self._models.PointIdsList(points=stale_ids),
                        wait=True,
                    )
                self.batch_size = active_batch_size
            self._last_error = None
            return True
        except Exception as exc:  # preserve the authoritative SQLite transaction
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False

    def _source_point_ids(self, source_filter: Any) -> set[str]:
        """Read bounded Qdrant pages for one source before replacing it."""

        assert self._client is not None
        point_ids: set[str] = set()
        offset: Any | None = None
        while True:
            records, offset = self._client.scroll(
                collection_name=self.collection_name,
                scroll_filter=source_filter,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            point_ids.update(str(record.id) for record in records)
            if offset is None:
                return point_ids

    def has_source(self, source: str) -> bool:
        """Check source coverage without loading document content."""

        if not self._ensure_ready():
            return False
        assert self._client is not None
        assert self._models is not None
        try:
            source_filter = self._models.Filter(
                must=[
                    self._models.FieldCondition(
                        key="source",
                        match=self._models.MatchValue(value=source),
                    )
                ]
            )
            with self._operation_lock:
                count = self._client.count(
                    collection_name=self.collection_name,
                    count_filter=source_filter,
                    exact=True,
                ).count
            return bool(count)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False

    def search(
        self,
        query: str,
        *,
        limit: int,
        source: str | None = None,
    ) -> list[VectorSearchHit]:
        """Return vector matches, or an empty list on any local vector failure."""

        if not self._ensure_ready():
            return []
        assert self._client is not None
        assert self._model is not None
        assert self._models is not None
        try:
            vector = next(iter(self._model.query_embed(query))).tolist()
            query_filter = None
            if source:
                query_filter = self._models.Filter(
                    must=[
                        self._models.FieldCondition(
                            key="source",
                            match=self._models.MatchValue(value=source),
                        )
                    ]
                )
            with self._operation_lock:
                points = self._client.query_points(
                    collection_name=self.collection_name,
                    query=vector,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                ).points
            hits: list[VectorSearchHit] = []
            for point in points:
                payload = point.payload or {}
                hits.append(
                    VectorSearchHit(
                        source=str(payload.get("source", "")),
                        kind=str(payload.get("kind", "document")),
                        chunk_index=int(payload.get("chunk_index", 0)),
                        # SQLite remains the single authoritative chunk body store.
                        content="",
                        score=float(point.score),
                    )
                )
            self._last_error = None
            return hits
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return []

    def status(self) -> dict[str, object]:
        """Return safe lazy-loader status without forcing a model download."""

        return {
            "enabled": self.enabled,
            "backend": "qdrant-local",
            "embedding_provider": "fastembed-onnx-cpu",
            "model": self.model_name,
            "model_dimension": EMBEDDING_DIMENSION,
            "model_license": EMBEDDING_LICENSE,
            "query_document_prefixes": EMBEDDING_PREFIXES,
            "signature": self.embedding_signature,
            "collection": self.collection_name,
            "batch_size": self.batch_size,
            "loaded": self._ready,
            "fallback": "sqlite-fts5-bm25",
            "last_error": self._last_error,
        }

    def close(self) -> None:
        """Close Qdrant if lazy initialization opened it."""

        with self._lock:
            if self._registered:
                with type(self)._registry_lock:
                    shared = type(self)._registry.get(self._registry_key)
                    if shared is not None:
                        references = int(shared["references"]) - 1
                        if references <= 0:
                            shared["client"].close()
                            type(self)._registry.pop(self._registry_key, None)
                        else:
                            shared["references"] = references
            self._client = None
            self._model = None
            self._models = None
            self._ready = False
            self._registered = False


def _is_memory_error(exc: BaseException) -> bool:
    """Recognize common ONNX allocation failures without hiding other errors."""

    if isinstance(exc, MemoryError):
        return True
    message = str(exc).casefold()
    return any(token in message for token in ("out of memory", "bad allocation", "oom"))
