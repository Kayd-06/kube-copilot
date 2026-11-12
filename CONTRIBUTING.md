# Contributing to Kube Copilot

Thanks for taking the time. This project is built in layers so a small change should stay small.

## Local setup

You will need Python 3.11+, Node 20+, Docker, [kind](https://kind.sigs.k8s.io/), kubectl, and (for packaging work) Helm 3 and Terraform 1.6+. A single-node kind cluster is enough — the demo is sized for a low-spec laptop.

```bash
git clone https://github.com/Kayd-06/kube-copilot.git
cd kube-copilot
cp .env.example .env          # add a Groq key as LLM_API_KEY
cp .env backend/.env

# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ..
make test                     # or: cd backend && python -m pytest -q

# Frontend
cd frontend
npm install
npm run lint                  # tsc --noEmit
npm run dev
```

The host workflow (`make cluster-up`, `make demo-break`, `make qdrant-up`, `make ingest`, `make backend-dev`, `make frontend-dev`) is documented in the [README](README.md#quickstart). To run the same stack *inside* the kind cluster instead of on the host, use `make helm-install` — that is the supported fix for kind advertising the API server as `127.0.0.1`.

## Checks we run in CI

GitHub Actions runs these on every pull request. Please run the ones that match what you changed before you push:

| Area | Command |
|------|---------|
| Backend lint | `ruff check backend ml` |
| Backend tests | `cd backend && python -m pytest -q` |
| Frontend | `cd frontend && npm run lint && npm run build` |
| Helm | `make helm-lint` (or `helm lint deploy/helm/kube-copilot` and `helm template …`) |
| Terraform | `terraform -chdir=deploy/terraform fmt -check` and `terraform -chdir=deploy/terraform validate` (after `terraform init -backend=false`) |
| Images | Dockerfiles are linted with hadolint (non-blocking). An e2e job builds the images, loads them into kind, installs the chart, and curls `/health`. |

Do not `terraform apply` the example module in CI or against a shared account without the owner asking for it.

## Pull requests

1. Branch off `main`. Name the branch after the work (`phase-4-devops`, `fix/probe-timeout`, …).
2. Keep commits focused and written in the imperative mood.
3. Fill in the pull request template: what changed, how you tested it.
4. Wait for CI to go green. If the kind smoke test fails, the "Collect diagnostics on failure" step dumps pod status and logs.

## Code layout

- `backend/app` — FastAPI API. Tests live in `backend/tests` and mock the Kubernetes, LLM, and Prometheus clients.
- `frontend` — Next.js App Router UI. Same-origin `/api` is proxied to `BACKEND_URL`.
- `ml` — training and evaluation; the served Isolation Forest artifact is committed under `backend/models`.
- `deploy/helm/kube-copilot` — the installable chart.
- `deploy/terraform` — optional single-node k3s on AWS; validate only unless you intend to spend money.
- `demo/metrics` — the tiny Prometheus / Grafana stack used on kind.

Please do not commit `.env`, kubeconfigs, Terraform state, or SSH keys.
