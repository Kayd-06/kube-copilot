# Resume bullets

Honest phrasing you can paste into a resume or internship application. The anomaly-detector numbers are from a held-out **synthetic** test set, not from production cluster traffic — keep that qualifier if you cite them.

- Built **Kube Copilot**, an open-source AI incident copilot for Kubernetes: a FastAPI service collects pod status, events, and logs, retrieves official-style runbooks from Qdrant (FastEmbed, CPU-only), and streams a Groq LLM diagnosis with citations to a Next.js dashboard.

- Designed and served an Isolation Forest anomaly detector over Prometheus CPU, memory, and restart metrics so the copilot can flag issues such as a slow memory leak before a pod is OOMKilled. Reported precision 0.896, recall 0.889, and F1 0.893 on a held-out synthetic test set (not yet evaluated on live cluster traffic).

- Packaged the stack for real clusters with a least-privilege Helm chart (in-cluster Kubernetes client, optional Qdrant, probes, and resource limits), GitHub Actions CI (pytest, Helm lint, a kind smoke test, GHCR publish, Trivy scans), a Terraform module for a single-node k3s VM, and Grafana dashboards for pod resources and anomaly scores.

- Wrote 80+ backend tests with mocked Kubernetes, LLM, and Prometheus clients, plus a one-command kind demo that reproduces CrashLoopBackOff, ImagePullBackOff, OOMKilled, and a leaking process.
