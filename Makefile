# Kube Copilot — common local-dev targets.
# Designed for a low-spec laptop: a single-node kind cluster and tiny demo images.

.PHONY: cluster-up cluster-down demo-break demo-clean backend-dev frontend-dev test \
	ingest qdrant-up metrics-up metrics-forward train mlflow-ui \
	images kind-load helm-lint helm-install helm-uninstall \
	grafana-up grafana-forward chaos-up chaos-run chaos-stop chaos-down \
	collect collect-dry-run train-real eval-lifecycle \
	actions-demo-up backend-dev-actions actions-e2e \
	eval eval-ablations eval-dry-run eval-capture eval-judge eval-heldout eval-capture-heldout eval-compare help

KIND_CLUSTER ?= kube-copilot
IMAGE_TAG ?= dev
HELM_RELEASE ?= kube-copilot
HELM_NAMESPACE ?= kube-copilot
CHART := deploy/helm/kube-copilot
BACKEND_IMAGE ?= ghcr.io/kayd-06/kube-copilot-backend
FRONTEND_IMAGE ?= ghcr.io/kayd-06/kube-copilot-frontend
CHAOS_MESH_VERSION ?= 2.8.8
SCENARIO ?= cpu_stress
# Extra flags for ml.collect, e.g. COLLECT_ARGS="--skip network_delay --baseline-minutes 10"
COLLECT_ARGS ?=
# Repo-root targets (train, collect, ...) use the backend venv when it exists.
ROOT_PY := $(shell if [ -x backend/.venv/bin/python ]; then echo backend/.venv/bin/python; else echo python3; fi)

help:
	@echo "Targets:"
	@echo "  cluster-up       Create a single-node kind cluster named $(KIND_CLUSTER)"
	@echo "  cluster-down     Delete the kind cluster"
	@echo "  demo-break       Apply intentionally broken demo workloads"
	@echo "  demo-clean       Remove the demo workloads"
	@echo "  qdrant-up        Start a local Qdrant container (docker compose)"
	@echo "  ingest           Embed runbooks and upsert them into Qdrant"
	@echo "  metrics-up       Install a tiny Prometheus + kube-state-metrics + metrics-server"
	@echo "  metrics-forward  Port-forward Prometheus to http://localhost:9090"
	@echo "  grafana-up       Install Grafana (and the metrics stack) with the Kube Copilot dashboard"
	@echo "  grafana-forward  Port-forward Grafana to http://localhost:3001 (admin/admin, anonymous viewer)"
	@echo "  chaos-up         Install a minimal Chaos Mesh + the chaos-lab target workloads"
	@echo "  chaos-run        Start one experiment: make chaos-run SCENARIO=cpu_stress"
	@echo "                   (pod_kill cpu_stress memory_stress memory_leak network_delay restart_storm)"
	@echo "  chaos-stop       Stop every running experiment"
	@echo "  chaos-down       Uninstall Chaos Mesh and delete the chaos-lab namespace"
	@echo "  collect          Run the ~60 min chaos timeline and write labelled metrics to ml/data/real/"
	@echo "  collect-dry-run  Print the collection timeline without touching the cluster"
	@echo "  train            Train the Isolation Forest on synthetic data (MLflow + report)"
	@echo "  train-real       Train/evaluate on the newest real run (time split, MLflow + report)"
	@echo "  eval-lifecycle   Score the event/lifecycle detector + Isolation Forest on run-box-03/04"
	@echo "  mlflow-ui        Open the local MLflow tracking UI on :5000"
	@echo "  images           Build backend and frontend Docker images tagged $(IMAGE_TAG)"
	@echo "  kind-load        Build images and load them into the kind cluster"
	@echo "  helm-lint        helm lint + helm template the chart"
	@echo "  helm-install     Load images into kind and install/upgrade the Helm chart"
	@echo "  helm-uninstall   Uninstall the Helm release"
	@echo "  backend-dev      Run the FastAPI app with reload on :8000"
	@echo "  frontend-dev     Run the Next.js UI on :3000"
	@echo "  test             Run backend unit tests"
	@echo "  actions-demo-up  Deploy the healthy copilot-demo/web target for safe actions"
	@echo "  backend-dev-actions  Run the API on :8000 with safe actions enabled for copilot-demo"
	@echo "  actions-e2e      Inject faults, propose, approve and verify via the API (needs backend-dev-actions)"
	@echo "  eval             Score Diagnose + the action proposer on the frozen incidents (LLM_MODEL=..., needs a key)"
	@echo "  eval-ablations   Same, plus without-RAG and without-lifecycle-signals variants"
	@echo "  eval-dry-run     No API calls: check fixtures, prompt sizes and model-free baselines"
	@echo "  eval-capture     Re-create the incidents on kind and refresh ml/data/llm_eval/cases (ONLY=id1,id2)"
	@echo "  eval-judge       Optional LLM judge + agreement with the hand checks (RESULTS=..., JUDGE_MODEL=...)"
	@echo "  eval-heldout     Score the 12 held-out incidents (ml/data/llm_eval/heldout)"
	@echo "  eval-capture-heldout  Re-create the held-out incidents on kind"
	@echo "  eval-compare     Before/after table for the default model (v1 vs v2 reports + hand checks)"

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

actions-demo-up:
	kubectl apply -f demo/actions/web.yaml
	kubectl -n copilot-demo rollout status deploy/web --timeout=120s

backend-dev-actions:
	cd backend && \
	if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi && \
	ACTIONS_ENABLED=true ACTIONS_ALLOWED_NAMESPACES=copilot-demo \
	$$PY -m uvicorn app.main:app --host 0.0.0.0 --port 8000

ACTIONS_API ?= http://localhost:8000
actions-e2e: actions-demo-up
	$(ROOT_PY) scripts/e2e_safe_actions.py --api $(ACTIONS_API) --out docs/reports/safe_actions_e2e.json

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
	kubectl apply -f demo/metrics/namespace.yaml
	kubectl apply -f demo/metrics/rbac.yaml
	kubectl apply -f demo/metrics/metrics-server.yaml
	kubectl apply -f demo/metrics/kube-state-metrics.yaml
	kubectl apply -f demo/metrics/prometheus.yaml
	@echo "Waiting for Prometheus to become ready..."
	kubectl -n observability wait --for=condition=available deploy/prometheus --timeout=180s
	kubectl -n observability wait --for=condition=available deploy/kube-state-metrics --timeout=180s || true
	@echo
	@echo "Prometheus is in-cluster. Expose it on the host with:"
	@echo "  make metrics-forward          # http://localhost:9090  (leave this running)"
	@echo "Then set PROMETHEUS_URL=http://localhost:9090 in .env (the default)."
	@echo "When the API runs in-cluster (make helm-install) it talks to"
	@echo "http://prometheus.observability.svc.cluster.local:9090 instead."

metrics-forward:
	@echo "Forwarding observability/prometheus to http://localhost:9090 (Ctrl-C to stop)"
	kubectl -n observability port-forward svc/prometheus 9090:9090

grafana-up: metrics-up
	kubectl apply -f demo/metrics/grafana
	@echo "Waiting for Grafana to become ready..."
	kubectl -n observability wait --for=condition=available deploy/grafana --timeout=180s
	@echo
	@echo "Grafana is in-cluster. Expose it on the host with:"
	@echo "  make grafana-forward          # http://localhost:3001  (admin/admin, or anonymous Viewer)"

grafana-forward:
	@echo "Forwarding observability/grafana to http://localhost:3001 (Ctrl-C to stop)"
	kubectl -n observability port-forward svc/grafana 3001:3000

# ROOT_PY is the backend venv when present, so training deps live next to the API deps.
train:
	PYTHONPATH=. MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ALLOW_FILE_STORE=true $(ROOT_PY) -m ml.train

# Train on real collected metrics (see ml/data/real/README.md). Writes
# ml/reports/real_metrics.{json,md} and backend/models/isolation_forest_real.joblib.
train-real:
	PYTHONPATH=. MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ALLOW_FILE_STORE=true $(ROOT_PY) -m ml.train_real

# Pod-disruption detector from events / lifecycle state, next to the forest.
# Needs runs collected with events (run-box-03 and later, see ml/data/real/README.md).
eval-lifecycle:
	PYTHONPATH=. MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ALLOW_FILE_STORE=true $(ROOT_PY) -m ml.eval_lifecycle

mlflow-ui:
	MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ALLOW_FILE_STORE=true $(ROOT_PY) -m mlflow ui --backend-store-uri ./mlruns --host 127.0.0.1 --port 5000

images:
	docker build -t $(BACKEND_IMAGE):$(IMAGE_TAG) -f backend/Dockerfile .
	docker build -t $(FRONTEND_IMAGE):$(IMAGE_TAG) \
		--build-arg NEXT_PUBLIC_API_URL=/api \
		-f frontend/Dockerfile frontend
	@echo "Built $(BACKEND_IMAGE):$(IMAGE_TAG) and $(FRONTEND_IMAGE):$(IMAGE_TAG)"

kind-load: images
	kind load docker-image $(BACKEND_IMAGE):$(IMAGE_TAG) --name $(KIND_CLUSTER)
	kind load docker-image $(FRONTEND_IMAGE):$(IMAGE_TAG) --name $(KIND_CLUSTER)

helm-lint:
	helm lint $(CHART)
	helm template $(HELM_RELEASE) $(CHART) -f $(CHART)/values-kind.yaml --namespace $(HELM_NAMESPACE) >/dev/null
	@echo "helm lint and helm template: ok"

# Create (or reuse) a secret from LLM_API_KEY in .env, then install the chart.
# The backend runs in-cluster so it uses the pod ServiceAccount — no kubeconfig
# mount, and no 127.0.0.1-from-a-container problem.
helm-install: kind-load
	kubectl create namespace $(HELM_NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	@SECRET_ARGS=""; \
	if [ -f .env ]; then \
	  KEY=$$(awk -F= '/^LLM_API_KEY=/{sub(/^LLM_API_KEY=/,""); print; exit}' .env); \
	  if [ -n "$$KEY" ] && [ "$$KEY" != "gsk_your_groq_api_key_here" ]; then \
	    kubectl -n $(HELM_NAMESPACE) create secret generic kube-copilot-llm \
	      --from-literal=LLM_API_KEY="$$KEY" \
	      --dry-run=client -o yaml | kubectl apply -f -; \
	    SECRET_ARGS="--set llm.existingSecret=kube-copilot-llm"; \
	  fi; \
	fi; \
	helm upgrade --install $(HELM_RELEASE) $(CHART) \
	  --namespace $(HELM_NAMESPACE) --create-namespace \
	  -f $(CHART)/values-kind.yaml \
	  --set backend.image.repository=$(BACKEND_IMAGE) \
	  --set backend.image.tag=$(IMAGE_TAG) \
	  --set frontend.image.repository=$(FRONTEND_IMAGE) \
	  --set frontend.image.tag=$(IMAGE_TAG) \
	  $$SECRET_ARGS \
	  --wait --timeout 5m
	@echo
	@echo "Installed $(HELM_RELEASE) in $(HELM_NAMESPACE)."
	@echo "  kubectl -n $(HELM_NAMESPACE) port-forward svc/$(HELM_RELEASE)-frontend 3000:3000"
	@echo "  open http://localhost:3000   (the UI proxies /api to the in-cluster backend)"
	@echo "Optional — ingest runbooks (downloads a small CPU embedder the first time):"
	@echo "  kubectl -n $(HELM_NAMESPACE) exec deploy/$(HELM_RELEASE)-backend -- python -m app.ingest"

helm-uninstall:
	helm uninstall $(HELM_RELEASE) -n $(HELM_NAMESPACE) || true
	kubectl -n $(HELM_NAMESPACE) delete secret kube-copilot-llm --ignore-not-found

# --- Chaos experiments + real data collection --------------------------------
# Chaos Mesh with one controller replica, no dashboard and no DNS server.
chaos-up:
	helm repo add chaos-mesh https://charts.chaos-mesh.org >/dev/null 2>&1 || true
	helm repo update chaos-mesh >/dev/null
	helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh --version $(CHAOS_MESH_VERSION) \
	  --namespace chaos-mesh --create-namespace \
	  -f demo/chaos/values-kind.yaml --wait --timeout 5m
	kubectl apply -f demo/chaos/workloads.yaml
	kubectl apply -f demo/chaos/experiments/memory-leak.yaml
	kubectl -n chaos-lab rollout status deploy --timeout=180s
	@echo "Chaos Mesh is ready. Try: make chaos-run SCENARIO=cpu_stress, then make chaos-stop"

chaos-run:
	@case "$(SCENARIO)" in \
	  memory_leak) kubectl -n chaos-lab scale deploy/victim-leak --replicas=2 ;; \
	  pod_kill|cpu_stress|memory_stress|network_delay|restart_storm) \
	    kubectl apply -f demo/chaos/experiments/$$(echo $(SCENARIO) | tr _ -).yaml ;; \
	  *) echo "unknown SCENARIO=$(SCENARIO)"; exit 2 ;; \
	esac
	@echo "Started $(SCENARIO). Stop it with: make chaos-stop"

chaos-stop:
	kubectl -n chaos-lab scale deploy/victim-leak --replicas=0 2>/dev/null || true
	for f in demo/chaos/experiments/*.yaml; do \
	  case $$f in *memory-leak.yaml) continue ;; esac; \
	  kubectl delete -f $$f --ignore-not-found --wait=false; \
	done

chaos-down: chaos-stop
	kubectl delete namespace chaos-lab --ignore-not-found
	helm uninstall chaos-mesh -n chaos-mesh || true

collect-dry-run:
	PYTHONPATH=. $(ROOT_PY) -m ml.collect --dry-run $(COLLECT_ARGS)

# Needs: make cluster-up metrics-up chaos-up. Port-forwards Prometheus itself
# unless PROMETHEUS_URL is set.
collect:
	PYTHONPATH=. $(ROOT_PY) -m ml.collect $(COLLECT_ARGS)

# --- LLM evaluation (see ml/llm_eval and ml/data/llm_eval/README.md) ---------
# LLM_MODEL overrides the model; LLM_API_KEY or GROQ_API_KEY must be set.
EVAL_ARGS ?=
EVAL_MODEL_ARG := $(if $(LLM_MODEL),--model $(LLM_MODEL),)
eval:
	PYTHONPATH=.:backend $(ROOT_PY) -m ml.llm_eval.run $(EVAL_MODEL_ARG) $(EVAL_ARGS)

eval-ablations:
	PYTHONPATH=.:backend $(ROOT_PY) -m ml.llm_eval.run $(EVAL_MODEL_ARG) --variants full,no_rag,no_signals $(EVAL_ARGS)

eval-dry-run:
	PYTHONPATH=.:backend $(ROOT_PY) -m ml.llm_eval.run --dry-run

# Needs: make cluster-up metrics-up chaos-up and Prometheus on :9090 (make metrics-forward).
ONLY ?=
eval-capture:
	PYTHONPATH=.:backend $(ROOT_PY) -m ml.llm_eval.capture $(if $(ONLY),--only $(ONLY),)

eval-heldout:
	PYTHONPATH=.:backend $(ROOT_PY) -m ml.llm_eval.run $(EVAL_MODEL_ARG) --fixtures ml/data/llm_eval/heldout --out-dir ml/reports/llm_eval/heldout $(EVAL_ARGS)

eval-capture-heldout:
	PYTHONPATH=.:backend $(ROOT_PY) -m ml.llm_eval.capture --set heldout

eval-compare:
	PYTHONPATH=.:backend $(ROOT_PY) -m ml.llm_eval.compare \
		--before ml/reports/llm_eval/openai_gpt-oss-120b.json --before-hand ml/data/llm_eval/hand_checks.json \
		--after ml/reports/llm_eval/v2/openai_gpt-oss-120b.json --after-hand ml/data/llm_eval/hand_checks_v2.json

RESULTS ?= ml/reports/llm_eval/openai_gpt-oss-120b.json
JUDGE_MODEL ?= openai/gpt-oss-20b
eval-judge:
	PYTHONPATH=.:backend $(ROOT_PY) -m ml.llm_eval.judge --results $(RESULTS) --judge-model $(JUDGE_MODEL)
