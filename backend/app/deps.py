"""FastAPI dependencies. Tests override these to inject mocks."""

from functools import lru_cache

from app.config import Settings, get_settings
from app.k8s_client import K8sClient
from app.llm_explainer import LLMExplainer


@lru_cache
def get_k8s_client() -> K8sClient:
    return K8sClient(settings=get_settings())


@lru_cache
def get_llm_explainer() -> LLMExplainer:
    return LLMExplainer(settings=get_settings())


def settings() -> Settings:
    return get_settings()
