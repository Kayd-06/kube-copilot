# Changelog

All notable changes to Kube Copilot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
