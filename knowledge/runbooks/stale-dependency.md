# App stuck after its dependency recovered (stale startup state)

The app checked or connected to a dependency (a database, an upstream API, a
cache) when it started. The dependency was down at that moment and has since
recovered, but the app never checks again. It keeps a failed connection, a
cached error, or a "degraded mode" flag from startup. Kubernetes shows the
symptom: the pod stays `Running` but not `Ready`, or it serves errors. Nothing
in the pod spec is wrong.

## Symptoms

- The pod is `Running` with **no restarts**, but it is not `Ready`, or it returns errors.
- Readiness probe failures (`Unhealthy` events) that started when the pod started and never cleared.
- Early in the logs: an error like "upstream unreachable", "connection refused" or "failed to connect" at startup, sometimes followed by "continuing in degraded mode". After that there are no new connection attempts.
- The dependency is healthy **now**: its Service has Ready endpoints, often Ready since a time **after** this pod started. Other clients reach it fine.

## Common causes

- The connection or health check happens only at startup, with no retry loop.
- A connection pool is created once and never re-established after the first failure.
- A readiness signal (a ready file, a flag) is set only after a successful startup check.
- Startup ordering: the app and its dependency were deployed together and the app started first.

## Diagnosis

1. Confirm the pod has not restarted since the failure: `kubectl get pod <pod> -n <ns>` (RESTARTS 0, READY 0/1).
2. Confirm the dependency is serving now:

   ```bash
   kubectl get endpoints <dependency-svc> -n <ns>
   kubectl get pods -n <ns> -l <dependency selector> -o wide
   ```

3. Compare times: when did the pod start, and when did the dependency's pods become Ready? If the dependency recovered after the pod started, this runbook applies.
4. Check that the probe itself is right: it fails because the app never became ready, not because of a wrong path or port.

## Fixes

- **Restart the workload** so it reconnects: `kubectl rollout restart deploy/<name> -n <ns>`. For one stuck replica among healthy ones, delete that pod.
- Do **not** change or remove the readiness probe. It is correctly reporting that the app is not ready.
- Do not roll back. The pod spec is not the problem.
- Long term: retry the dependency with backoff instead of only checking at startup, re-create connections on failure, and make readiness reflect the current state of the dependency.
