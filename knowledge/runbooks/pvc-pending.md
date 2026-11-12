# PersistentVolumeClaim pending

A PVC that stays `Pending` never binds to a PersistentVolume. Pods that mount that claim usually stay `Pending` as well, with scheduling or mount events that mention the unbound volume.

## Symptoms

- `kubectl get pvc` shows `STATUS` as `Pending`.
- Pod events include `FailedScheduling` (`persistentvolumeclaim "..." not found` or `unbound immediate PersistentVolumeClaims`) or later `FailedMount`.
- `kubectl describe pvc` explains why the provisioner or binder did not satisfy the claim.

## Common causes

- No StorageClass exists for the requested `storageClassName` (or the default class is missing when the field is omitted).
- The cluster has no dynamic provisioner, and no static PV matches the access mode, size, labels, or `volumeName`.
- The access mode is impossible on that class (for example `ReadWriteMany` on a provisioner that only supports `ReadWriteOnce`).
- The provisioner failed (cloud quota, missing IAM permissions, CSI driver not running).
- A `selector` or `volumeName` on the PVC points at a PV that does not exist or is already bound.

## Diagnosis

1. Describe the claim and list classes and volumes:

   ```bash
   kubectl get pvc -n <ns>
   kubectl describe pvc <pvc> -n <ns>
   kubectl get storageclass
   kubectl get pv
   kubectl get pods -n kube-system | grep -i csi
   ```

2. Read the PVC event list for `ProvisioningFailed`, `FailedBinding`, or `ExternalProvisioning`.
3. If a pod is stuck, confirm it references the intended claim name and namespace.

   ```bash
   kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.volumes}'
   ```

## Fixes

- Set `storageClassName` to a class that actually exists and has a working provisioner, then recreate or edit the PVC as allowed.
- For clusters without dynamic provisioning (some kind / lab setups), create a matching PersistentVolume (or use `hostPath` / `local` only in non-production) and bind it.
- Request an access mode the provisioner supports, or pick a class that offers RWX if multiple writers are required.
- Repair the CSI driver, cloud permissions, or quota if provisioning is failing after a correct class is set.
- Do not expect the pod to schedule until the PVC is `Bound`.
