from fastapi.testclient import TestClient

from app import __version__
from app.main import app


def test_health_does_not_need_kubernetes_or_llm():
    # Fresh client with no overrides — /health must never touch the cluster.
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "kube-copilot"
    assert body["version"] == __version__
