# CrashLoopBackOff

A pod is in `CrashLoopBackOff` when Kubernetes has started the container repeatedly and it keeps exiting. The kubelet backs off between restarts, so the pod can sit in this state for a long time while the application itself only lives for a few seconds at a time.

## Symptoms

- `kubectl get pods` shows `STATUS` as `CrashLoopBackOff` or `Error` with a climbing restart count.
- The pod phase may still be `Running` because a container is mid-backoff, not because the app is healthy.
- Current logs are often empty or just a short "waiting" message. The useful signal is usually in the **previous** container logs (`kubectl logs --previous`).
- Events include `BackOff`, `Failed`, or `Unhealthy` from the kubelet.

## Common causes

- The container command or entrypoint exits immediately (`exit 1`, a missing binary, or a typo in `args`).
- The process crashes on startup because a required env var, ConfigMap key, or Secret is missing or malformed.
- The application cannot reach a dependency (database, API, cache) and treats that as fatal.
- A failed liveness probe kills a slow-starting process, which then crash-loops (see the probe-failures runbook).
- The process is being OOM-killed; that often surfaces as `OOMKilled` rather than a generic crash, but both can appear together. Check the last terminated state.

## Diagnosis

1. Read the last termination reason and exit code:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl get pod <pod> -n <ns> -o jsonpath='{range .status.containerStatuses[*]}{.name}{" "}{.state}{" "}{.lastState.terminated}{"\n"}{end}'
   ```

2. Always fetch previous logs if `restartCount > 0`:

   ```bash
   kubectl logs <pod> -n <ns> --previous --tail=200
   kubectl logs <pod> -n <ns> --tail=200
   ```

3. Confirm the command that actually ran (`command` / `args` in the pod spec) and whether the image entrypoint expects different arguments.
4. If logs mention a missing file, env var, or connection refused, inspect the related ConfigMap, Secret, and Service.

## Fixes

- If the command is wrong (for example a demo container that ends in `exit 1`), correct `command`/`args` or the image entrypoint so the main process stays in the foreground.
- If a config value is missing, add the env var or volume and roll the workload.
- If a dependency is down, fix that service or make startup retry instead of exiting.
- If a liveness probe is too aggressive, increase `initialDelaySeconds` / `failureThreshold` (see probe-failures).
- After a fix, roll the deployment and confirm the restart count stops climbing:

  ```bash
  kubectl rollout status deploy/<name> -n <ns>
  kubectl get pod <pod> -n <ns> --watch
  ```
