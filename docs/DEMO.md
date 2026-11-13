# Demo recording script

A 2–3 minute walkthrough you can record for a README, a blog post, or an application. Practice once with the Groq key in `.env` so Diagnose is not the first time the model is called.

## Setup (before you hit record)

Do this on a quiet desktop, with the terminal font large enough to read.

```bash
# Host workflow — slightly faster to iterate on than a full Helm install.
cp .env.example .env          # LLM_API_KEY already filled in
cp .env backend/.env
make cluster-up
make demo-break
make qdrant-up && make ingest
make metrics-up
# In separate terminals:
make metrics-forward          # leave running
make grafana-up && make grafana-forward
make backend-dev
# another terminal
cd frontend && npm run dev
```

Wait about a minute after `metrics-up` so Prometheus has a few scrapes, then confirm:

- [http://localhost:3000](http://localhost:3000) shows unhealthy pods and an Anomalies panel.
- [http://localhost:8000/health](http://localhost:8000/health) returns `"status": "ok"`.
- [http://localhost:3001](http://localhost:3001) is Grafana (anonymous Viewer, or admin/admin).

If you would rather demo the in-cluster path: `make cluster-up && make demo-break && make metrics-up && make helm-install`, then port-forward the frontend as the Makefile prints.

## Script

1. **The problem (15s).** Open a terminal and run `kubectl get pods -n copilot-demo`. Point at `CrashLoopBackOff`, `ImagePullBackOff`, `OOMKilled`, and `leak-app` still running. Say that the evidence is normally scattered across describe, events, and previous logs, and that a leak is visible in metrics *before* the pod dies.

2. **The dashboard (20s).** Switch to http://localhost:3000. Show the Anomalies panel and the unhealthy-pod list. Mention that scores come from an Isolation Forest trained on synthetic CPU / memory / restart windows.

3. **Diagnose a crash (45s).** Click **Diagnose** on the crash-loop pod. Watch the answer stream in. Scroll to the citations (runbook title + section) and the anomaly breakdown if it is present. Read the suggested `kubectl` commands out loud but do not run them all.

4. **Catch a leak (30s).** Go back and diagnose `leak-app` (or open it from Anomalies). Point at the memory slope or `mem_to_limit_ratio` in the contributing features. Say clearly that the published precision / recall / F1 were measured on synthetic data, and that this live score is a demo on a toy workload.

5. **Grafana (20s).** Open http://localhost:3001 and the **Kube Copilot** dashboard. Show pod memory climbing on `leak-app`, restarts on the crash-loop pod, and (if you already diagnosed something) diagnose latency / LLM calls.

6. **How it is packaged (20s).** Flip to the repo: Helm chart under `deploy/helm`, CI badge on the README, Terraform under `deploy/terraform` (say that you have not applied it against a paid account in this recording). If there is time, show `GET /metrics` on the API.

7. **Safe actions (60s, optional).** Before recording: `make actions-demo-up` and run the API with `make backend-dev-actions` instead of `make backend-dev`. Break the rollout live: `kubectl -n copilot-demo set image deploy/web web=nginx:1.27-alpine-typo`. Diagnose the `ErrImagePull` pod, press **Propose remediation**, and show the rollback card: the LLM's reason, the server-side dry-run diff (image `...-typo` back to `nginx:1.27-alpine`) and the Approve / Reject buttons. Say that the LLM can only pick from four actions, that the policy is checked in code, and that `kube-system` is refused outright. Type your name, press **Approve & run**, and watch the status go from verifying to resolved with the time to recovery. Open **Audit log** to show the timeline. If you approve something else on `web` within two minutes, the cooldown returns 429; that is expected.

8. **Close (10s).** One sentence on what you would do next: evaluate the detector on real cluster metrics, or add a signed GitOps path for the chart.

## If something is red

- Empty Anomalies panel: Prometheus needs about a minute, and `make metrics-forward` must be running for the host workflow.
- Diagnose returns 502: the Groq key is missing or rate-limited. Restart the backend after fixing `.env`.
- Citations empty: Qdrant is down or `make ingest` was skipped. Diagnose still works; just say so.
- Propose remediation says "read-only mode": the API was started without `ACTIONS_ENABLED=true`. Use `make backend-dev-actions`.
- Propose returns 502: the LLM rejected the tool call or the model is not available for your key. Set `LLM_MODEL` to a model your provider lists. The project default is Groq `openai/gpt-oss-120b`.
- Grafana has no anomaly series: the API has not scored any pods yet, or Prometheus cannot scrape the API (in the host workflow the API is not in-cluster — scores still appear on the dashboard itself).
