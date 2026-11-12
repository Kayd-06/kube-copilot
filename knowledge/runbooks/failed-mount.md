# FailedMount and volume mount errors

The pod was scheduled, but the kubelet could not mount one of its volumes. The container will not start until every required volume is available. Messages often appear as `FailedMount` or `FailedAttachVolume`.

## Symptoms

- Pod stays `Pending` or `ContainerCreating` for a long time.
- Events include `FailedMount`, `FailedAttachVolume`, or `FailedMapVolume`, citing a PVC, Secret, ConfigMap, or projected volume.
- `kubectl logs` is usually unavailable because the container has not been created.
- Timeout messages such as `MountVolume.SetUp failed` or `timed out waiting for the condition` are common.

## Common causes

- The PersistentVolume is not attached (CSI attach failure, wrong zone, volume already attached to another node in RWO mode).
- A Secret, ConfigMap, or projected service-account token referenced as a volume is missing (closely related to CreateContainerConfigError).
- `subPath` points at a path that does not exist, or the volume is not ready yet.
- NFS / CSI driver pods are down, or a kubelet plugin socket is missing on the node.
- SELinux, permissions, or a read-only mount conflict with what the process needs.

## Diagnosis

1. Read the mount events and the volume list:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl get pvc -n <ns>
   kubectl get volumeattachment
   kubectl get pods -n kube-system | grep -Ei 'csi|nfs'
   ```

2. If the volume is a PVC, confirm it is `Bound` and that the PV's access mode and node affinity match the scheduled node (see also the PVC pending runbook).
3. If the volume is a Secret or ConfigMap, confirm the object exists in the namespace and that the `items` keys are present.

## Fixes

- Wait or reschedule if a RWO volume is still attached to a previous node; deleting a stuck kubelet or VolumeAttachment may be required after a hard node failure.
- Restore the CSI / NFS driver DaemonSet and check its logs on that node.
- Create the missing Secret or ConfigMap, or remove the volume reference.
- Fix `subPath`, access modes, and zone selectors so the volume can attach where the pod landed.
- Only as a last resort, delete the stuck pod so the controller starts a replacement on a node that can attach the volume.
