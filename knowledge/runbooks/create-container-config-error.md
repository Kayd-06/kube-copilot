# CreateContainerConfigError

The kubelet refused to create the container because its configuration is invalid. This happens before the image even starts, so there are no application logs. The most common triggers are missing Secrets, ConfigMaps, or projected keys that the pod spec references.

## Symptoms

- Pod is `Pending` or `Waiting` with reason `CreateContainerConfigError`.
- Events include `Failed` with a message such as `Error: secret "..." not found`, `couldn't find key ... in ConfigMap ...`, or `configmap "..." not found`.
- Restart count is usually zero. `kubectl logs` will fail or return an "unavailable" message.

## Common causes

- A Secret or ConfigMap is referenced as `envFrom`, `secretKeyRef`, `configMapKeyRef`, or a volume, but it does not exist in the pod's namespace.
- The object exists but the key name in the pod spec does not match a key in the Secret or ConfigMap.
- The referenced object was deleted after the workload was applied, or it was created in a different namespace.
- Optional vs required: if `optional: false` (the default for most refs), a missing object blocks container creation.
- Less often, an invalid environment variable name or a conflict between `command` and a read-only filesystem setting.

## Diagnosis

1. The event message almost always names the missing object and key:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl get events -n <ns> --field-selector involvedObject.name=<pod>
   ```

2. Confirm the object and keys in the same namespace:

   ```bash
   kubectl get secret,configmap -n <ns>
   kubectl get secret <name> -n <ns> -o jsonpath='{.data}'
   kubectl get configmap <name> -n <ns> -o yaml
   ```

3. Compare those keys with the pod spec (`env`, `envFrom`, `volumes`, `volumeMounts`).

## Fixes

- Create the missing Secret or ConfigMap in the correct namespace, with the keys the pod expects.
- Or update the pod spec to use the real object and key names, then roll the workload.
- If the value is genuinely optional, set `optional: true` on the env or volume reference so the container can start without it.
- For GitOps or Helm releases, make sure the Secret is sealed / generated in the same environment the pod runs in, not only in a local overlay.
