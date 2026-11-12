# Pending and unschedulable pods

A pod stays `Pending` when the scheduler cannot place it on any node, or when it is still waiting for a condition that must be true before scheduling or starting (most often volumes, but also init containers or resource availability).

## Symptoms

- `kubectl get pods` shows `STATUS` as `Pending` for longer than a few seconds.
- `kubectl describe pod` has a `PodScheduled` condition set to `False` with reason `Unschedulable`.
- Events include `FailedScheduling` with a message such as `0/N nodes are available: ...`.
- The pod has no `nodeName` yet (unless it is stuck later in volume attach or image pull).

## Common causes

- Requests for CPU or memory are larger than the allocatable resources on any node.
- A `nodeSelector`, `nodeAffinity`, `taint`/`toleration`, or topology constraint matches no node.
- PersistentVolumeClaim is unbound, so the pod cannot schedule (`pvc-pending` runbook).
- The only candidate nodes are `NotReady`, cordoned (`SchedulingDisabled`), or out of disk / memory / PID pressure.
- A required port (`hostPort`) or an extended resource (GPU) is already taken.

## Diagnosis

1. Read the `FailedScheduling` message first — it usually lists every reason a node was skipped:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl get events -n <ns> --field-selector reason=FailedScheduling
   ```

2. Compare requests to allocatable resources:

   ```bash
   kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[*].resources.requests}'
   kubectl get nodes -o custom-columns=NAME:.metadata.name,STATUS:.status.conditions[-1],CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory
   kubectl describe node <node>
   ```

3. Check taints, affinity, and selectors on the pod against labels and taints on the nodes.
4. If the message mentions a PVC or volume, inspect the claim (see the PVC pending runbook).

## Fixes

- Lower resource requests, or add node capacity, so the pod fits.
- Fix or remove an overly strict `nodeSelector` / affinity rule, and add any missing tolerations for tainted nodes.
- Uncordon nodes (`kubectl uncordon <node>`) if they were drained and forgotten.
- Bind or provision the PVC before expecting the pod to schedule.
- For `hostPort` or GPU conflicts, reschedule, free the resource, or change the port.
