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
    # Groq:    https://api.groq.com/openai/v1   model: openai/gpt-oss-120b
    # OpenAI:  https://api.openai.com/v1        model: gpt-4o-mini
    # Ollama:  http://localhost:11434/v1       model: llama3.2  (set LLM_API_KEY=ollama)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"

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

    # --- Prometheus / anomaly detection ---
    prometheus_url: str = "http://localhost:9090"
    prometheus_timeout: float = 3.0
    # Empty means backend/models/isolation_forest.joblib (next to this package).
    anomaly_model_path: str = ""
    anomaly_poller_enabled: bool = False
    anomaly_poll_interval_seconds: int = 30
    anomaly_window_seconds: int = 180
    anomaly_step_seconds: int = 15
    # When set, overrides the threshold stored in the model artifact.
    anomaly_score_threshold: float | None = None

    # --- Safe actions (human-in-the-loop remediation) ---
    # Off by default: the API stays read-only until ACTIONS_ENABLED=true.
    actions_enabled: bool = False
    # Global kill switch: when true nothing is ever executed (proposals still work).
    actions_kill_switch: bool = False
    # Namespaces the agent may act in. Empty means none.
    actions_allowed_namespaces: str = "copilot-demo"
    # Always refused, even if also listed as allowed. kube-system is refused in code too.
    actions_denied_namespaces: str = "kube-system,kube-public,kube-node-lease,chaos-mesh,observability,kube-copilot"
    actions_scale_min: int = 1
    actions_scale_max: int = 5
    # Proposals older than this cannot be approved.
    actions_proposal_ttl_seconds: int = 900
    # After an execution, the same workload cannot be acted on again for this long.
    actions_cooldown_seconds: int = 120
    actions_max_per_hour: int = 10
    actions_verify_window_seconds: int = 180
    actions_verify_interval_seconds: float = 5.0
    # Healthy this long in a row counts as recovered.
    actions_verify_stable_seconds: float = 20.0
    # SQLite audit log. Empty means backend/data/actions.sqlite.
    actions_audit_path: str = ""

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

    def resolved_anomaly_model_path(self) -> Path:
        if self.anomaly_model_path:
            return Path(self.anomaly_model_path).expanduser().resolve()
        # backend/models/isolation_forest.joblib
        return Path(__file__).resolve().parents[1] / "models" / "isolation_forest.joblib"

    def resolved_actions_audit_path(self) -> Path:
        if self.actions_audit_path:
            return Path(self.actions_audit_path).expanduser().resolve()
        return Path(__file__).resolve().parents[1] / "data" / "actions.sqlite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
