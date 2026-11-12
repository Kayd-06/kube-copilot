"""Retrieval-augmented generation helpers: chunking, embeddings, Qdrant, retrieval."""

from app.rag.chunking import MarkdownChunk, chunk_markdown
from app.rag.embeddings import Embedder, HashEmbedder, build_embedder
from app.rag.retriever import RetrievedChunk, Retriever, RetrieveResult, build_retrieval_query
from app.rag.store import KnowledgeStore, KnowledgeUnavailableError

__all__ = [
    "Embedder",
    "HashEmbedder",
    "KnowledgeStore",
    "KnowledgeUnavailableError",
    "MarkdownChunk",
    "RetrievedChunk",
    "Retriever",
    "RetrieveResult",
    "build_embedder",
    "build_retrieval_query",
    "chunk_markdown",
]
