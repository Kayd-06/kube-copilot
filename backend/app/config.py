"""Application settings loaded from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # OpenAI-compatible LLM endpoint. Defaults to Groq (fast, free-tier friendly).
    # Groq:    https://api.groq.com/openai/v1   model: llama-3.3-70b-versatile
    # OpenAI:  https://api.openai.com/v1        model: gpt-4o-mini
    # Ollama:  http://localhost:11434/v1       model: llama3.2  (set LLM_API_KEY=ollama)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"

    # Kubernetes access. Leave kubeconfig unset to use the default (~/.kube/config).
    k8s_in_cluster: bool = False
    k8s_kubeconfig: str | None = None
    log_tail_lines: int = 200

    # Comma-separated list of allowed CORS origins for the Next.js UI.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- RAG / Qdrant ---
    # QDRANT_URL:
    #   http://localhost:6333  — Docker Compose service (default)
    #   :memory:               — in-process store, good for tests
    # Leave empty and set QDRANT_PATH for an embedded on-disk store (no container).
    qdrant_url: str = "http://localhost:6333"
    qdrant_path: str | None = None
    qdrant_api_key: str = ""
    qdrant_timeout: float = 3.0
    qdrant_collection_runbooks: str = "runbooks"
    qdrant_collection_incidents: str = "incidents"
    # Embedding backend: "fastembed" (default, CPU, BAAI/bge-small-en-v1.5) or "fake".
    embedding_backend: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    rag_top_k: int = 5
    rag_enabled: bool = True
    # Directory of markdown runbooks. Empty means "discover knowledge/runbooks".
    knowledge_dir: str = ""

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def is_ollama(self) -> bool:
        url = self.llm_base_url.lower()
        return "11434" in url or "ollama" in url

    def resolved_knowledge_dir(self) -> Path:
        if self.knowledge_dir:
            return Path(self.knowledge_dir).expanduser().resolve()
        here = Path(__file__).resolve()
        return here.parents[2] / "knowledge" / "runbooks"


@lru_cache
def get_settings() -> Settings:
    return Settings()
