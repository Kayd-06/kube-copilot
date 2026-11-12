# Demo cluster

These manifests exist so the copilot has something real to diagnose and so the
anomaly detector has a slowly worsening workload to flag.

| Workload          | What breaks                                      | Typical reason        |
|-------------------|--------------------------------------------------|-----------------------|
| `crashloop-app`   | Container prints a line and `exit 1`             | `CrashLoopBackOff`    |
| `bad-image-app`   | Image name does not exist                        | `ImagePullBackOff`    |
| `oom-app`         | Busybox grows a string past 16Mi instantly       | `OOMKilled`           |
| `leak-app`        | Writes 1Mi to `/dev/shm` every 5s, 64Mi limit    | Rising memory, then OOM |

All four run in the `copilot-demo` namespace, use the small `busybox:1.36` image
(except the bad-image case), a single replica, and tight CPU/memory limits so a
laptop-sized kind cluster stays comfortable. `leak-app` is the Phase 3 demo: it
grows slowly enough that the Isolation Forest can flag `mem_slope_mib_per_min`
before the container is killed.

A separate, optional stack in `demo/metrics/` installs a tiny Prometheus,
kube-state-metrics, and metrics-server in the `observability` namespace
(`make metrics-up`). Resource requests are kept small (Prometheus 128Mi).

```bash
# from the repo root, after kind and kubectl are installed
make cluster-up
make demo-break
make metrics-up
make metrics-forward    # leave this running; Prometheus at http://localhost:9090
kubectl get pods -n copilot-demo
kubectl get pods -n observability
```
