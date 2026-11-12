# Resume bullets

Honest phrasing you can paste into a resume or internship application. The real-data numbers come from about 1.9 hours of chaos experiments on a single-node kind cluster, scored on a separately collected held-out run. That is not production traffic, so keep the qualifier if you cite them.

- Built **Kube Copilot**, an open-source AI incident copilot for Kubernetes: a FastAPI service collects pod status, events, and logs, retrieves official-style runbooks from Qdrant (FastEmbed, CPU-only), and streams a Groq LLM diagnosis with citations to a Next.js dashboard.

- Built a chaos-engineering data pipeline (Chaos Mesh on kind plus the Prometheus range API) that labels per-pod metrics during CPU stress, memory stress, memory-leak, restart-storm and pod-kill experiments, and used it to retrain an Isolation Forest anomaly detector on real cluster data with a time-based, leakage-free split and MLflow tracking.

- Found that the synthetic-trained model flagged nearly every real pod (specificity 0.001), then added an out-of-range guard and real-data training. On a held-out run, the detector reached precision 0.85, recall 0.59 and F1 0.70 on application pods, up from F1 0.19 for the synthetic model on the same pods. CPU-stress and restart-storm recall was 1.0, while pod-kill recall stayed low (0.04), and I documented why.

- Packaged the stack for real clusters with a least-privilege Helm chart (in-cluster Kubernetes client, optional Qdrant, probes, and resource limits), GitHub Actions CI (pytest, Helm lint, a kind smoke test, GHCR publish, Trivy scans), a Terraform module for a single-node k3s VM, and Grafana dashboards for pod resources and anomaly scores.

- Wrote 100+ backend tests with mocked Kubernetes, LLM, and Prometheus clients, plus a one-command kind demo that reproduces CrashLoopBackOff, ImagePullBackOff, OOMKilled, and a leaking process.
