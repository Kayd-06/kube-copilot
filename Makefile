# Kube Copilot — common local-dev targets.
# Designed for a low-spec laptop: a single-node kind cluster and tiny demo images.

.PHONY: cluster-up cluster-down demo-break demo-clean backend-dev frontend-dev test ingest qdrant-up help

KIND_CLUSTER ?= kube-copilot

help:
	@echo "Targets:"
	@echo "  cluster-up     Create a single-node kind cluster named $(KIND_CLUSTER)"
	@echo "  cluster-down   Delete the kind cluster"
	@echo "  demo-break     Apply intentionally broken demo workloads"
	@echo "  demo-clean     Remove the demo workloads"
	@echo "  qdrant-up      Start a local Qdrant container (docker compose)"
	@echo "  ingest         Embed runbooks and upsert them into Qdrant"
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
