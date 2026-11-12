# Evicted pods

The kubelet evicts a pod when the node is under resource pressure (memory, disk, or inodes) or when an eviction API / descheduler asks it to leave. Evicted pods stay in the API as `Failed` with reason `Evicted` until they are cleaned up; a controller such as a Deployment will usually create a replacement.

## Symptoms

- `kubectl get pods` shows `STATUS` as `Evicted` or `REASON` as `Evicted`.
- The pod message mentions memory, ephemeral-storage, or inodes (`The node was low on resource: memory...`).
- Node conditions include `MemoryPressure`, `DiskPressure`, or `PIDPressure`.
- Other pods on the same node may also have been evicted around the same time.

## Common causes

- The node ran out of memory. BestEffort pods are evicted first, then Burstable pods that have exceeded their requests.
- The node ran out of ephemeral storage (container logs, overlay, emptyDir) or inodes.
- A noisy neighbor without limits filled the node.
- Manual eviction (`kubectl drain`, `kubectl evict`) or a descheduler policy.
- Disk-backed emptyDir or unbounded log files eating node disk.

## Diagnosis

1. Read the eviction message on the pod and the node's conditions and events:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl describe node <node>
   kubectl get node <node> -o jsonpath='{.status.conditions}'
   kubectl get pods -A --field-selector spec.nodeName=<node>
   ```

2. Check whether a Deployment already replaced the pod, and whether evictions are repeating on the same node.
3. Look at `kubectl top node` and disk usage if you have metrics or node access (`df -h`, container log sizes).

## Fixes

- Free node pressure: delete evicted pods that are only clutter, clear disk, and move or cap greedy workloads.
- Set memory and ephemeral-storage requests/limits so the scheduler packs more realistically and the kubelet has a fair eviction order.
- Add node capacity, or cordon a bad node until disk / memory is recovered.
- Bound logs (`log rotation` / a logging agent) and avoid huge `emptyDir` volumes without an explicit size limit.
- If drain evicted the pod intentionally, uncordon the node when the maintenance is done so replacements can return.
