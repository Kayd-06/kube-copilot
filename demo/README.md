# Demo cluster

These manifests exist so the copilot has something real to diagnose.

| Workload          | What breaks                         | Typical reason        |
|-------------------|-------------------------------------|-----------------------|
| `crashloop-app`   | Container prints a line and `exit 1`| `CrashLoopBackOff`    |
| `bad-image-app`   | Image name does not exist           | `ImagePullBackOff`    |
| `oom-app`         | Busybox grows a string past 16Mi    | `OOMKilled`           |

All three run in the `copilot-demo` namespace, use the small `busybox:1.36` image (except the bad-image case), a single replica, and tight CPU/memory limits so a laptop-sized kind cluster stays comfortable.

```bash
# from the repo root, after kind and kubectl are installed
make cluster-up
make demo-break
kubectl get pods -n copilot-demo
```
