# OOMKilled

The container was killed because it exceeded its memory limit (or, less often, the node itself ran out of memory). Kubernetes reports the last terminated reason as `OOMKilled` with exit code `137`.

## Symptoms

- Container `lastState.terminated.reason` is `OOMKilled`.
- Restart count climbs; the pod may also show `CrashLoopBackOff` once the kubelet starts backing off.
- Events may include `OOMKilling` from the kubelet. Logs often stop abruptly, sometimes in the middle of a request or a large allocation.
- `kubectl describe pod` shows a memory `limit` that is close to, or below, what the process actually uses.

## Common causes

- The memory `limit` is too low for the workload (common with tiny demo limits such as `16Mi` or `32Mi`).
- A memory leak, an unbounded cache, or a single request that loads a large payload into memory.
- The JVM / Node / Python runtime is not configured to respect the cgroup limit (`-Xmx`, `NODE_OPTIONS=--max-old-space-size`, etc.).
- Sidecars or multiple processes in one container that together exceed the limit.
- Burst usage during startup (loading models, warming caches) that is higher than steady state.

## Diagnosis

1. Confirm the termination reason and the current limits:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[*].resources}'
   ```

2. Compare usage if metrics are available:

   ```bash
   kubectl top pod <pod> -n <ns> --containers
   ```

3. Read previous logs for allocation errors, heap dumps, or a sudden stop.
4. Check whether the node itself is under memory pressure (`kubectl describe node <node>` and look for evictions or `MemoryPressure`).

## Fixes

- Raise the memory `limit` (and usually the `request`) to a level measured from real usage, plus headroom.
- If the app is leaking or buffering too much, fix the application; raising the limit only delays the next kill.
- Align the language runtime's heap with the cgroup limit so the GC can stay inside the cap.
- Split heavy sidecars into their own containers with their own limits.
- After changing resources, roll the workload and watch that `OOMKilled` stops appearing in `lastState`.
