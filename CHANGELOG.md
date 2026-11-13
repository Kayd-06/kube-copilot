# Changelog

All notable changes to Kube Copilot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **LLM evaluation harness** (`ml/llm_eval`, `make eval`). 38 incidents reproduced on kind with hand labels written from the injected fault: category, acceptable safe-actions tool, matching runbooks and difficulty. 11 are hard. They are frozen as JSON fixtures (`ml/data/llm_eval/cases`) holding exactly what Diagnose and the proposer receive. The harness reports deterministic root-cause and remediation scoring, a JSON schema check, RAG hit@k/MRR, a kubectl hallucination check, p50/p95 latency, tokens and cost, model-free baselines, Wilson intervals, `--variants no_rag,no_signals` ablations, rate-limit pacing with `--resume`, and `--rescore`. An optional LLM judge (`make eval-judge`) has a fixed rubric and is compared against hand checks (`ml/data/llm_eval/hand_checks.json`). `make eval-capture` re-creates the incidents in throwaway `kc-eval-NN` namespaces.
- First results (`ml/reports/llm_eval/README.md`). On `openai/gpt-oss-120b`: root cause 30/38 hand-checked (27/38 by regex), remediation 30/38, schema 97.4%, no hallucinated resources, Diagnose p50 1.8 s. On `openai/gpt-oss-20b`: 31/38 hand-checked (27/38 by regex), remediation 29/38. The regex matcher agreed with the hand check on 34/37 and 33/37 diagnoses, the judge (`qwen/qwen3.8-27b`) on 33/37 and 35/37.
- Offline CI tests for the fixtures and scorers (`backend/tests/test_llm_eval.py`), and a manual `LLM eval` workflow that uses a `GROQ_API_KEY` secret.
- `LLMExplainer.explain_detailed()` returns the raw text, parse status and token usage. `ProposalResult` carries token usage.

### Known issues found by the eval

- The safe-actions proposer only sees image and command for the current and previous revision, so rollouts that break env, probes, resources or nodeSelector look identical and are not rolled back (7 of 11 rollout cases).
- Diagnose ignores pod-kill lifecycle signals (0/3), is misled by false-positive metric anomalies on healthy pods, and has no log-size cap: one prompt exceeded Groq's free-tier 8K TPM.

### Added (earlier in this cycle)

- **Safe actions (human-in-the-loop remediation)**, off by default (`ACTIONS_ENABLED=false`). `POST /pods/{ns}/{pod}/actions/propose` asks the LLM via function calling for one of four actions: `rollback_deployment`, `restart_deployment`, `scale_deployment` (bounded) or `delete_pod` (crashlooping, Deployment-owned only), or `no_action`. Policy in code (`backend/app/actions/policy.py`): tool allowlist, exact parameters, namespace allow/deny list with `kube-system` always refused, incident scope, scale bounds, live target checks. Every proposal is dry-run on the API server (`dryRun=All`) and shown as a diff. Approve/reject endpoints record the approver; approval re-checks policy on fresh state and enforces a global kill switch, proposal TTL, one action in flight per workload, a per-workload cooldown and an hourly cap. After execution the backend verifies recovery (replicas, bad pods, restarts, lifecycle signals) and records time to recovery; scale and rollback can be reverted through a new proposal.
- SQLite audit log of every proposal, block, decision, execution and verification: `GET /audit`, `GET /actions`, `GET /actions/{id}`.
- Dashboard: a Safe actions panel on the diagnosis view (proposal cards with dry-run diff, Approve/Reject, verification status, timeline, revert) and an `/audit` page.
- Helm `actions.*` values. With `actions.enabled=true` the chart adds one namespaced Role per `actions.namespaces` entry (patch deployments, get/patch deployments/scale, delete pods), mounts `/data` for the audit log (emptyDir or PVC), and refuses to render with denied namespaces, no namespaces, or more than one backend replica. CI renders both cases.
- `scripts/e2e_safe_actions.py`, `make actions-demo-up`, `make backend-dev-actions`, `make actions-e2e`, and the kind results in `docs/reports/safe_actions_e2e.md`: 9/9 approved rollbacks resolved across three faulty-rollout scenarios (full-outage case healthy 3.3 to 11.7 s after approval); the model declined (`no_action`) on a stale-config crashloop 3/3 times.
- `docs/SAFE_ACTIONS.md` with the flow, guardrails and limits.

- Pod-disruption detector from Kubernetes events and lifecycle state (`ml/lifecycle.py`). It flags pod replacements and disappearances that no scale-up or rollout explains, available < desired replicas, container restarts, and disruption events (`Killing`, `BackOff`, `OOMKilling`, `Evicted`, `NodeNotReady`, ...) on pods and ReplicaSets. Events about Chaos Mesh objects are ignored.
- `/anomalies` merges it with the Isolation Forest: `is_anomaly` is now the union, with new `metric_anomaly`, `detectors`, `lifecycle_signals`, workload `disruptions` and `lifecycle_unavailable` fields. The new `GET /disruptions` endpoint reads the Kubernetes API only. Diagnose passes lifecycle signals to the LLM prompt. The dashboard shows workload disruptions and per-pod signals.
- `make collect` now records Kubernetes events (`events.jsonl.gz`) and kube-state-metrics lifecycle series (`lifecycle.csv.gz`) during a run.
- Held-out run `ml/data/real/run-box-03` (63 min, 3 pod-kill windows plus CPU, memory, leak and restart faults), and `make eval-lifecycle` with its report at `ml/reports/lifecycle_metrics.md`. On app pods, pod-kill recall went from 0.037 (forest) to 0.851, and union P/R/F1 is 0.570 / 0.824 / 0.674, against 0.555 / 0.330 / 0.413 for the forest alone.

### Changed

- Default Groq model is now `openai/gpt-oss-120b` (`LLM_MODEL`, Helm `llm.model`, Terraform `llm_model`). The previous default, `llama-3.3-70b-versatile`, is no longer listed for typical Groq keys. Override with `LLM_MODEL` as before.
- The Helm ClusterRole can also get/list/watch `deployments` and `replicasets`. Without that permission, lifecycle signals still work from events and pods.
- `ml.train_real.evaluate_predictions` scores predictions from any detector with the same report format.

## [0.4.0] — 2026-11-12

Phase 4 — DevOps polish.

### Added

- Helm chart at `deploy/helm/kube-copilot` with backend and frontend Deployments, optional Ingress, ConfigMap/Secret (LLM key via `llm.existingSecret`), a least-privilege ClusterRole (get/list/watch pods, logs, and events), optional in-chart Qdrant, probes, and resource limits. `values-kind.yaml` is sized for a single-node kind cluster.
- `make helm-install` / `make helm-uninstall` / `make images` / `make kind-load`. The backend runs with in-cluster config, which is the supported way past kind advertising the API server as `127.0.0.1`.
- GitHub Actions CI: ruff + pytest, frontend lint/build, Helm lint, optional hadolint, Terraform fmt/validate, and a kind e2e job that installs the chart and smoke-tests `/health`.
- Image workflow that builds and pushes to GHCR on `main` and version tags, then scans with Trivy.
- Dependabot for pip, npm, GitHub Actions, Docker, and Terraform.
- Terraform module at `deploy/terraform` that provisions a small AWS EC2 VM, installs k3s, and deploys the chart with `helm_release`. Not applied in CI.
- Grafana dashboard (pod CPU, memory, restarts, anomaly scores, diagnose latency, LLM calls) provisioned by `make grafana-up`.
- Backend `/metrics` via prometheus-fastapi-instrumentator, plus custom metrics for anomaly scores, diagnose latency, and LLM calls.
- The Next.js app proxies same-origin `/api` to `BACKEND_URL`, so the dashboard works when only the frontend Service is exposed.
- `CONTRIBUTING.md`, issue and pull request templates, `docs/DEMO.md`, and `docs/RESUME.md`.

### Changed

- Backend version is now `0.4.0`.
- README architecture diagram and layout cover the Helm / CI / Grafana / Terraform packaging.
- Docker Compose frontend talks to the backend through `/api` instead of a baked-in `localhost:8000` URL.

## [0.3.0] — 2026-11-12

Phase 3 — ML anomaly detection.

- Isolation Forest trained on synthetic pod metrics, tracked with MLflow, served from the API.
- Lightweight Prometheus + kube-state-metrics + metrics-server stack for kind.
- Anomalies panel on the dashboard and a leaking `leak-app` demo pod.
- Evaluation numbers are reported on a held-out synthetic test set only.

## [0.2.0] — 2026-11-12

Phase 2 — RAG with Qdrant.

- 12 runbooks, FastEmbed on CPU, Qdrant (Docker, on-disk, or in-memory).
- Diagnose cites retrieved chunks and can persist resolved incidents.
- Streaming answers on the dashboard.

## [0.1.0] — 2026-11-12

Phase 1 — working core.

- FastAPI backend that lists unhealthy pods and asks an LLM (Groq by default) to explain them.
- Next.js dashboard, kind demo cluster, and intentionally broken workloads.
