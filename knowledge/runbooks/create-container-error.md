# CreateContainerError

The kubelet tried to create the container and the runtime rejected it. Unlike `CreateContainerConfigError` (missing config objects), `CreateContainerError` usually means the runtime call itself failed: a bad device, an invalid security context, a full disk, or an image that is present but cannot be instantiated.

## Symptoms

- Pod is `Waiting` with reason `CreateContainerError`.
- Events include `Failed` from the kubelet with a runtime error (device mapper, `no space left on device`, seccomp / AppArmor / SELinux denial, invalid capability, or a missing device plugin).
- The image has often already been pulled (`ImagePullBackOff` is not the current reason).
- Application logs are typically unavailable because the container never started.

## Common causes

- The node disk is full, so the runtime cannot create the container's writable layer.
- A security context asks for a capability, seccomp profile, AppArmor profile, or user that the node or policy will not allow.
- A device plugin (GPU, FPGA) is required but not healthy, or the requested resource is gone.
- An invalid `command` / working directory / symlink inside the image, or a corrupted image snapshot.
- Runtime bugs or leftover half-created containers after a kubelet crash.

## Diagnosis

1. Capture the exact kubelet message first:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl get events -n <ns> --field-selector involvedObject.name=<pod>
   ```

2. Check node disk and runtime health:

   ```bash
   kubectl describe node <node>
   ```

   On the node, `df -h`, `crictl images`, and kubelet logs usually show "no space" or a security policy denial.

3. Inspect the pod's `securityContext`, resource requests for extended devices, and the image's entrypoint.

## Fixes

- Free disk on the node (unused images, evicted pods, container logs) or move the pod off a full node.
- Adjust the security context so it matches cluster policy, or update the policy if the capability is truly required.
- Repair or restart the device plugin and confirm the node still advertises the extended resource.
- Retag or rebuild a corrupted image, then delete the pod so it is recreated with a clean snapshot.
- If only one node is affected, cordon it, drain what you can, and fix the runtime before uncordoning.
