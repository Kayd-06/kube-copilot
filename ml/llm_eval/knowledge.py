"""An in-memory runbook index built with the same chunker, embedder and retriever as the API."""

from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_RETRIEVER = None


def get_retriever():
    """Retriever over knowledge/runbooks in an in-memory Qdrant (FastEmbed bge-small by default)."""
    global _RETRIEVER
    with _LOCK:
        if _RETRIEVER is None:
            os.environ.setdefault("QDRANT_URL", ":memory:")
            from app.config import Settings
            from app.ingest import default_knowledge_dir, ingest_runbooks
            from app.rag.embeddings import build_embedder
            from app.rag.retriever import Retriever
            from app.rag.store import KnowledgeStore

            settings = Settings(qdrant_url=":memory:", qdrant_path=None)
            store = KnowledgeStore(settings=settings, embedder=build_embedder(settings))
            ingest_runbooks(default_knowledge_dir(settings), store=store, settings=settings)
            _RETRIEVER = Retriever(store=store, settings=settings)
        return _RETRIEVER


def retrieve(pod, events, logs, usage=None):
    retriever = get_retriever()
    with _LOCK:  # the in-memory client is not thread-safe
        return retriever.retrieve_for_pod(pod, events, logs, usage=usage)
