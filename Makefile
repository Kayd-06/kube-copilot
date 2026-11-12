# Kube Copilot — common local-dev targets.
# Designed for a low-spec laptop: a single-node kind cluster and tiny demo images.

.PHONY: cluster-up cluster-down demo-break demo-clean backend-dev frontend-dev test ingest qdrant-up metrics-up metrics-forward train mlflow-ui help

KIND_CLUSTER ?= kube-copilot

help:
	@echo "Targets:"
	@echo "  cluster-up     Create a single-node kind cluster named $(KIND_CLUSTER)"
	@echo "  cluster-down   Delete the kind cluster"
	@echo "  demo-break     Apply intentionally broken demo workloads"
	@echo "  demo-clean     Remove the demo workloads"
	@echo "  qdrant-up      Start a local Qdrant container (docker compose)"
	@echo "  ingest         Embed runbooks and upsert them into Qdrant"
	@echo "  metrics-up     Install a tiny Prometheus + kube-state-metrics + metrics-server"
	@echo "  metrics-forward  Port-forward Prometheus to http://localhost:9090"
	@echo "  train          Train the Isolation Forest on synthetic data (MLflow + report)"
	@echo "  mlflow-ui      Open the local MLflow tracking UI on :5000"
	@echo "  backend-dev    Run the FastAPI app with reload on :8000"
	@echo "  frontend-dev   Run the Next.js UI on :3000"
	@echo "  test           Run backend unit tests"

cluster-up:
	kind create cluster --config demo/kind-config.yaml
	kubectl cluster-info --context kind-$(KIND_CLUSTER)

cluster-down:
	kind delete cluster --name $(KIND_CLUSTER)

demo-break:
	kubectl apply -f demo/manifests
	@echo "Waiting a few seconds for pods to settle into a failed state..."
	@sleep 5
	kubectl get pods -n copilot-demo -o wide || true

demo-clean:
	kubectl delete -f demo/manifests --ignore-not-found

backend-dev:
	cd backend && \
	if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi && \
	$$PY -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend-dev:
	cd frontend && npm run dev

test:
	cd backend && \
	if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi && \
	$$PY -m pytest -v

qdrant-up:
	docker compose up -d qdrant
	@echo "Qdrant is at http://localhost:6333 — next: make ingest"

ingest:
	cd backend && \
	if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi && \
	$$PY -m app.ingest

metrics-up:
	kubectl apply -f demo/metrics
	@echo "Waiting for Prometheus to become ready..."
	kubectl -n observability wait --for=condition=available deploy/prometheus --timeout=180s
	kubectl -n observability wait --for=condition=available deploy/kube-state-metrics --timeout=180s || true
	@echo
	@echo "Prometheus is in-cluster. Expose it on the host with:"
	@echo "  make metrics-forward          # http://localhost:9090  (leave this running)"
	@echo "Then set PROMETHEUS_URL=http://localhost:9090 in .env (the default)."

metrics-forward:
	@echo "Forwarding observability/prometheus to http://localhost:9090 (Ctrl-C to stop)"
	kubectl -n observability port-forward svc/prometheus 9090:9090

# PY is the backend venv when present, so training deps live next to the API deps.
train:
	cd backend && \
	if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi && \
	cd .. && \
	PYTHONPATH=. MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ALLOW_FILE_STORE=true $$PY -m ml.train

mlflow-ui:
	cd backend && \
	if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi && \
	cd .. && \
	MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ALLOW_FILE_STORE=true $$PY -m mlflow ui --backend-store-uri ./mlruns --host 127.0.0.1 --port 5000
