"""FastAPI dependencies. Tests override these to inject mocks."""

from functools import lru_cache

from app.config import Settings, get_settings
from app.anomaly.lifecycle import LifecycleDetector
from app.anomaly.scorer import AnomalyScorer
from app.k8s_client import K8sClient
from app.llm_explainer import LLMExplainer
from app.metrics.prometheus import PrometheusClient
from app.rag.embeddings import Embedder, build_embedder
from app.rag.retriever import Retriever
from app.rag.store import KnowledgeStore


@lru_cache
def get_k8s_client() -> K8sClient:
    return K8sClient(settings=get_settings())


@lru_cache
def get_llm_explainer() -> LLMExplainer:
    return LLMExplainer(settings=get_settings())


@lru_cache
def get_embedder() -> Embedder:
    return build_embedder(get_settings())


@lru_cache
def get_knowledge_store() -> KnowledgeStore:
    return KnowledgeStore(settings=get_settings(), embedder=get_embedder())


@lru_cache
def get_retriever() -> Retriever:
    return Retriever(store=get_knowledge_store(), settings=get_settings())


@lru_cache
def get_prometheus_client() -> PrometheusClient:
    return PrometheusClient(settings=get_settings())


@lru_cache
def get_anomaly_scorer() -> AnomalyScorer:
    return AnomalyScorer(settings=get_settings())


@lru_cache
def get_lifecycle_detector() -> LifecycleDetector:
    return LifecycleDetector(get_k8s_client(), settings=get_settings())


def clear_caches() -> None:
    """Drop cached settings and clients. Useful in tests."""
    get_settings.cache_clear()
    get_k8s_client.cache_clear()
    get_llm_explainer.cache_clear()
    get_embedder.cache_clear()
    get_knowledge_store.cache_clear()
    get_retriever.cache_clear()
    get_prometheus_client.cache_clear()
    get_anomaly_scorer.cache_clear()
    get_lifecycle_detector.cache_clear()


def settings() -> Settings:
    return get_settings()
