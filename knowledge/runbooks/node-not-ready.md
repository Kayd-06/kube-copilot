# Node NotReady

A node in `NotReady` is no longer considered healthy by the control plane. Pods that were running there may be marked `NotReady`, get stuck terminating, or (after `pod-eviction-timeout`) be rescheduled elsewhere. New pods will not be scheduled onto that node.

## Symptoms

- `kubectl get nodes` shows `STATUS` as `NotReady` or `Unknown`.
- `kubectl describe node` has a `Ready` condition `False` or `Unknown`, often with reasons such as `KubeletNotReady`, `PLEG is not healthy`, or `NodeStatusUnknown` (the control plane has lost contact with the kubelet).
- Workloads have many unready pods, and events mention the node. `kubectl get pods -o wide` shows several pods pinned to that node.

## Common causes

- The kubelet process stopped, hung, or cannot reach the API server (network, DNS, or expired certificates).
- The node has disk, memory, or PID pressure severe enough for the kubelet to report not ready.
- The container runtime (containerd / CRI-O / Docker) is down.
- The node VM or machine lost power, or kube-proxy / CNI is failing so the node never becomes `NetworkReady`.
- Clock skew or a failed node problem detector.

## Diagnosis

1. Inspect the node conditions, taints, and capacity:

   ```bash
   kubectl get nodes -o wide
   kubectl describe node <node>
   kubectl get pods -A -o wide --field-selector spec.nodeName=<node>
   ```

2. If you have node access, check kubelet and runtime logs (`systemctl status kubelet`, `journalctl -u kubelet`, `crictl info`).
3. Confirm the control plane can reach the node IP and that certificates have not expired.
4. Distinguish a single bad node from a cluster-wide outage (all nodes `NotReady` usually means API / CNI / cloud credentials).

## Fixes

- Restart the kubelet and the container runtime on that node once you know they are stuck, and fix disk or memory pressure first if that is what they are reporting.
- Restore node networking and API connectivity; a node in `Unknown` will not recover until heartbeats resume.
- If the machine is gone, delete the node object (after confirming it will not come back) so evicted pods can reschedule:

  ```bash
  kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
  kubectl delete node <node>
  ```

- For kind or local clusters, restarting the kind node container often brings the kubelet back: `docker restart <kind-node-container>`.
- After the node is `Ready` again, uncordon it if it was cordoned and watch that pods become Ready.
