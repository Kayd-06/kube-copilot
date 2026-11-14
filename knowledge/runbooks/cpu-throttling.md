# CPU throttling

The container hits its CPU `limit` and the kernel's CFS quota pauses it for the rest of each 100 ms period. Nothing crashes and the pod stays `Running` and `Ready`, but work runs slowly: latency goes up, batches take longer, and probes with short timeouts may start to fail.

## Symptoms

- The pod looks healthy: no restarts, no warning events, no errors in the logs.
- CPU usage sits at (or just under) the container's CPU limit, for example `20m` used of a `20m` limit.
- A large share of CFS periods are throttled: `container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total` above roughly 25%.
- Logs show work taking longer than usual (slow batches, slow requests, timeouts to callers), not failing outright.
- Liveness or readiness probes with tight `timeoutSeconds` may fail intermittently under load.

## Common causes

- The CPU `limit` is too low for the workload (tiny demo limits such as `10m` to `50m` on a CPU-bound process).
- A rollout lowered `resources.limits.cpu`, or added a limit where there was none.
- Traffic or batch size grew while the limit stayed the same.
- Multi-threaded runtimes (JVM, Go, Node worker pools) that size their thread pools from the node's cores, not the cgroup quota, burn the quota in a few milliseconds.

## Diagnosis

1. Read the requests and limits:

   ```bash
   kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[*].resources}'
   ```

2. Compare usage with the limit:

   ```bash
   kubectl top pod <pod> -n <ns> --containers
   ```

3. Check throttling in Prometheus:

   ```promql
   sum by (container) (rate(container_cpu_cfs_throttled_periods_total{namespace="<ns>", pod="<pod>"}[5m]))
     / sum by (container) (rate(container_cpu_cfs_periods_total{namespace="<ns>", pod="<pod>"}[5m]))
   ```

4. If a rollout changed the resources, compare revisions with `kubectl rollout history deploy/<name> -n <ns> --revision=<n>`.

## Fixes

- Raise the CPU `limit` (or remove it and rely on `requests` for scheduling) to a value measured from real usage plus headroom.
- If a recent rollout lowered the limit, roll back to the previous revision, then fix the manifest.
- Scale out (more replicas) when the work can be spread across pods; this does not make a single slow request faster.
- Size runtime thread pools to the cgroup quota (`GOMAXPROCS`, `-XX:ActiveProcessorCount`, worker counts).
- Raise probe `timeoutSeconds` if probes fail only because the container is throttled.
