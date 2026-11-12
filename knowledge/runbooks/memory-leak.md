# Memory leak (rising working set)

A pod whose working set climbs steadily toward its memory limit, often for minutes, before an `OOMKilled` event. Unlike the tight-limit `oom-app` demo, this is a *trend*: memory-to-limit ratio and MiB/minute slope go up while the container is still Running.

## Symptoms

- `container_memory_working_set_bytes` increases roughly linearly.
- Memory-to-limit ratio climbs toward 1.0.
- The anomaly detector flags `mem_slope_mib_per_min` and `mem_zscore`.
- Eventually `Last State: OOMKilled` and a restart, sometimes a CrashLoop.

## Common causes

- A process that allocates (buffers, caches, a growing list) and never releases.
- A limit that is far too low for a legitimate working set (looks like a leak in the short window).
- A sidecar or debug tool writing into a tmpfs (`/dev/shm`, emptyDir memory).

## Checks

```bash
kubectl top pod <pod> -n <ns>
kubectl describe pod <pod> -n <ns>
kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[*].resources}'
```

In Prometheus, look at `container_memory_working_set_bytes` over 5–15 minutes, not a single sample.

## Fixes

- Find the allocator (heap profile, or temporarily raise the limit and watch).
- If the working set is legitimate, raise the memory request/limit.
- If a tmpfs is growing, cap its `sizeLimit` or stop writing to it.
