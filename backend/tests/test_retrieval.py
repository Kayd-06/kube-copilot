import pytest

from app.config import Settings
from app.ingest import ingest_runbooks
from app.rag.embeddings import HashEmbedder
from app.rag.retriever import Retriever, build_retrieval_query
from app.rag.store import KnowledgeStore, KnowledgeUnavailableError


@pytest.fixture
def loaded_retriever(tmp_path):
    (tmp_path / "crashloopbackoff.md").write_text(
        "# CrashLoopBackOff\n\n## Common causes\n"
        "The container command ends with exit 1 and Kubernetes restarts it.\n",
        encoding="utf-8",
    )
    (tmp_path / "oomkilled.md").write_text(
        "# OOMKilled\n\n## Common causes\nThe container exceeded its memory limit.\n",
        encoding="utf-8",
    )
    settings = Settings(qdrant_url=":memory:", embedding_backend="fake", rag_top_k=3)
    store = KnowledgeStore(settings=settings, embedder=HashEmbedder())
    ingest_runbooks(tmp_path, store=store, settings=settings)
    return Retriever(store=store, settings=settings), store, settings


def test_build_retrieval_query_includes_status_events_and_logs(sample_pod, sample_events, sample_logs):
    query = build_retrieval_query(sample_pod, sample_events, sample_logs)
    assert "CrashLoopBackOff" in query
    assert "Back-off restarting" in query
    assert "I am about to crash" in query
    assert "Running" in query


def test_retrieve_for_pod_returns_matching_runbook(loaded_retriever, sample_pod, sample_events, sample_logs):
    retriever, _, _ = loaded_retriever
    result = retriever.retrieve_for_pod(sample_pod, sample_events, sample_logs, top_k=3)
    assert result.knowledge_unavailable is False
    assert result.chunks
    sources = {chunk.source for chunk in result.chunks}
    assert "crashloopbackoff.md" in sources
    top = result.chunks[0]
    assert top.title
    assert top.section
    assert top.score >= result.chunks[-1].score


def test_incident_memory_is_searchable(loaded_retriever):
    retriever, _, _ = loaded_retriever
    point_id = retriever.save_incident(
        pod_name="payments-api-1",
        namespace="prod",
        reason="OOMKilled",
        root_cause="memory limit of 64Mi was too small for the JVM heap",
        fix="Raised the memory limit to 512Mi and set -Xmx384m",
    )
    assert point_id
    result = retriever.search("OOMKilled JVM heap memory limit too small", top_k=5)
    kinds = {chunk.kind for chunk in result.chunks}
    sources = {chunk.source for chunk in result.chunks}
    assert "incident" in kinds
    assert "incident-memory" in sources


def test_retriever_falls_back_when_store_is_down(sample_pod, sample_events, sample_logs):
    settings = Settings(qdrant_url="http://127.0.0.1:1", qdrant_timeout=0.2, embedding_backend="fake")
    store = KnowledgeStore(settings=settings, embedder=HashEmbedder())
    retriever = Retriever(store=store, settings=settings)
    result = retriever.retrieve_for_pod(sample_pod, sample_events, sample_logs)
    assert result.knowledge_unavailable is True
    assert result.chunks == []
    assert result.warning
    with pytest.raises(KnowledgeUnavailableError):
        retriever.save_incident(
            pod_name="x",
            namespace="ns",
            reason=None,
            root_cause="r",
            fix="f",
        )
