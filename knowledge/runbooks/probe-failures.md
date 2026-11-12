# Liveness and readiness probe failures

Probes tell Kubernetes whether a container is alive (liveness) and whether it should receive traffic (readiness). A failing readiness probe takes the pod out of Service endpoints. A failing liveness probe kills and restarts the container, which can look like a crash loop even when the application code is "fine".

## Symptoms

- Events include `Unhealthy` with a message such as `Liveness probe failed` or `Readiness probe failed`.
- For readiness: the pod may be `Running` and `1/1` not ready (`READY 0/1`). It will not appear in the Service's endpoints.
- For liveness: restart count climbs, often after a consistent delay (`initialDelaySeconds` plus a few failures). Previous logs may show the app still starting or blocked on a dependency.
- Startup probes, if present, failing in the same way can prevent the other probes from ever being evaluated.

## Common causes

- `initialDelaySeconds` is shorter than the application's real startup time.
- The probe path, port, or command is wrong (HTTP 404 on `/healthz`, checking the app port when the server is on another port).
- The handler is too strict (requires a warm cache or a downstream dependency that is degraded).
- Timeouts are too low for a slow endpoint, or the probe itself is expensive enough to stall the process.
- A readiness probe depends on an external system, so a database blip removes every replica from the Service at once.

## Diagnosis

1. Inspect the probe definitions and recent probe events:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[*].livenessProbe}'
   kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[*].readinessProbe}'
   kubectl get endpoints <svc> -n <ns>
   ```

2. Exec into a running container (if it lives long enough) and call the same path or command the probe uses.
3. Read current and previous logs around the time of each restart. If the process is killed mid-startup, the liveness probe is a prime suspect.
4. Distinguish "app is crashing on its own" (see CrashLoopBackOff) from "kubelet is killing a healthy-but-slow process".

## Fixes

- Increase `initialDelaySeconds`, `periodSeconds`, `timeoutSeconds`, or `failureThreshold` so slow starts can finish.
- Add or fix a `startupProbe` for apps that take a long time to boot, and keep liveness conservative.
- Point HTTP probes at a cheap, local endpoint that does not depend on downstream systems.
- Fix a wrong port or path rather than disabling the probe entirely.
- After changing probes, roll the deployment and confirm `READY` stays `1/1` and events stop showing `Unhealthy`.
