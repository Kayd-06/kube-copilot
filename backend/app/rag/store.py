"""Qdrant-backed knowledge store with URL, path, and in-memory modes."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from app.config import Settings, get_settings
from app.rag.chunking import MarkdownChunk
from app.rag.embeddings import Embedder, build_embedder

logger = logging.getLogger(__name__)

KIND_RUNBOOK = "runbook"
KIND_INCIDENT = "incident"


class KnowledgeUnavailableError(Exception):
    """Raised when Qdrant cannot be reached or is not configured."""


class ScoredChunk:
    def __init__(
        self,
        *,
        title: str,
        source: str,
        section: str,
        text: str,
        score: float,
        kind: str,
        point_id: str | None = None,
    ) -> None:
        self.title = title
        self.source = source
        self.section = section
        self.text = text
        self.score = score
        self.kind = kind
        self.point_id = point_id


class KnowledgeStore:
    """Thin wrapper around QdrantClient.

    Connection is lazy. Callers should treat connection failures as
    ``KnowledgeUnavailableError`` and fall back rather than crashing.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        embedder: Embedder | None = None,
        client: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._embedder = embedder
        self._client = client
        self._failed: str | None = None
        self._ensured: set[str] = set()

    @property
    def runbooks_collection(self) -> str:
        return self._settings.qdrant_collection_runbooks

    @property
    def incidents_collection(self) -> str:
        return self._settings.qdrant_collection_incidents

    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = build_embedder(self._settings)
        return self._embedder

    def _mode(self) -> str:
        url = (self._settings.qdrant_url or "").strip()
        path = (self._settings.qdrant_path or "").strip()
        lowered = url.lower()
        if lowered in {":memory:", "memory", "mem"}:
            return "memory"
        if path and lowered in {"", "path", "local"}:
            return "path"
        if url:
            return "url"
        if path:
            return "path"
        return "memory"

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        if self._failed:
            raise KnowledgeUnavailableError(self._failed)
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            self._failed = "qdrant-client is not installed"
            raise KnowledgeUnavailableError(self._failed) from exc

        mode = self._mode()
        timeout = self._settings.qdrant_timeout
        try:
            if mode == "memory":
                logger.info("Using in-memory Qdrant")
                client = QdrantClient(location=":memory:")
            elif mode == "path":
                path = (self._settings.qdrant_path or "").strip()
                if not path:
                    raise KnowledgeUnavailableError("QDRANT_PATH is empty")
                logger.info("Using local Qdrant path store at %s", path)
                client = QdrantClient(path=path)
            else:
                url = self._settings.qdrant_url.strip()
                api_key = self._settings.qdrant_api_key or None
                logger.info("Connecting to Qdrant at %s", url)
                client = QdrantClient(url=url, api_key=api_key, timeout=timeout, check_compatibility=False)
                # Force a cheap RPC so we fail fast when the server is down.
                client.get_collections()
        except KnowledgeUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 — any transport / init failure
            self._failed = f"Could not connect to Qdrant ({mode}): {exc}"
            logger.warning(self._failed)
            raise KnowledgeUnavailableError(self._failed) from exc

        self._client = client
        return client

    def is_available(self) -> bool:
        if not self._settings.rag_enabled:
            return False
        try:
            self._connect()
            return True
        except KnowledgeUnavailableError:
            return False

    def _ensure_collection(self, name: str) -> None:
        if name in self._ensured:
            return
        client = self._connect()
        from qdrant_client.models import Distance, VectorParams

        size = self.embedder().dimension
        try:
            exists = client.collection_exists(name)
        except Exception as exc:  # noqa: BLE001
            raise KnowledgeUnavailableError(f"Could not list Qdrant collections: {exc}") from exc
        if not exists:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=size, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection %s (dim=%s)", name, size)
        self._ensured.add(name)

    def upsert_chunks(
        self,
        collection: str,
        chunks: Sequence[MarkdownChunk],
        *,
        kind: str = KIND_RUNBOOK,
        extra_payload: dict[str, Any] | None = None,
    ) -> list[str]:
        """Embed and upsert chunks. Returns the point ids."""
        if not chunks:
            return []
        self._ensure_collection(collection)
        client = self._connect()
        from qdrant_client.models import PointStruct

        texts = [chunk.text for chunk in chunks]
        vectors = self.embedder().embed(texts)
        points: list[PointStruct] = []
        ids: list[str] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            point_id = _point_id(kind, chunk.source, chunk.section, chunk.text)
            payload = {
                "title": chunk.title,
                "source": chunk.source,
                "section": chunk.section,
                "text": chunk.text,
                "kind": kind,
            }
            if extra_payload:
                payload.update(extra_payload)
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))
            ids.append(point_id)
        client.upsert(collection_name=collection, points=points)
        return ids

    def upsert_payload(
        self,
        collection: str,
        *,
        text: str,
        title: str,
        source: str,
        section: str,
        kind: str,
        extra: dict[str, Any] | None = None,
        point_id: str | None = None,
    ) -> str:
        chunk = MarkdownChunk(title=title, source=source, section=section, text=text)
        ids = self.upsert_chunks(
            collection,
            [chunk],
            kind=kind,
            extra_payload=extra,
        )
        return ids[0] if ids else (point_id or "")

    def search(
        self,
        collection: str,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[ScoredChunk]:
        if not query.strip() or top_k <= 0:
            return []
        try:
            self._ensure_collection(collection)
            client = self._connect()
        except KnowledgeUnavailableError:
            raise
        vector = self.embedder().embed([query])[0]
        try:
            hits = self._query(client, collection, vector, top_k)
        except Exception as exc:  # noqa: BLE001
            raise KnowledgeUnavailableError(f"Qdrant search failed: {exc}") from exc
        return [_hit_to_chunk(hit) for hit in hits]

    def _query(self, client: Any, collection: str, vector: list[float], top_k: int) -> list[Any]:
        # Prefer the newer query_points API; fall back to search for older clients.
        if hasattr(client, "query_points"):
            result = client.query_points(
                collection_name=collection,
                query=vector,
                limit=top_k,
                with_payload=True,
            )
            return list(getattr(result, "points", None) or [])
        return list(
            client.search(
                collection_name=collection,
                query_vector=vector,
                limit=top_k,
                with_payload=True,
            )
        )


def _point_id(kind: str, source: str, section: str, text: str) -> str:
    # Stable UUIDv5 so re-ingesting the same chunk updates rather than duplicates.
    key = f"{kind}::{source}::{section}::{text}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _hit_to_chunk(hit: Any) -> ScoredChunk:
    payload = getattr(hit, "payload", None) or {}
    score = float(getattr(hit, "score", 0.0) or 0.0)
    point_id = getattr(hit, "id", None)
    return ScoredChunk(
        title=str(payload.get("title") or ""),
        source=str(payload.get("source") or ""),
        section=str(payload.get("section") or ""),
        text=str(payload.get("text") or ""),
        score=score,
        kind=str(payload.get("kind") or KIND_RUNBOOK),
        point_id=str(point_id) if point_id is not None else None,
    )
