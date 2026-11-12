# ImagePullBackOff and ErrImagePull

The kubelet could not pull the container image, so the pod never starts. `ErrImagePull` is the immediate failure; `ImagePullBackOff` is the retry/backoff state that follows.

## Symptoms

- Pod stays `Pending` or shows `STATUS` as `ImagePullBackOff` / `ErrImagePull`.
- Events include `Failed` from `kubelet` with messages such as `Failed to pull image`, `not found`, `unauthorized`, `denied`, or `invalid image name`.
- There are no application logs because the container has not started.

## Common causes

- The image name or tag is wrong (typo, tag that was never pushed, or `latest` that does not exist).
- The image is in a private registry and the pod has no `imagePullSecrets`, or the Secret is in the wrong namespace.
- The registry is unreachable (DNS, network policy, or a corporate proxy).
- `imagePullPolicy: Always` combined with an unstable or deleted tag.
- An invalid reference such as a missing host, a bad digest, or uppercase characters where the registry does not allow them.

## Diagnosis

1. Read the event message — it usually names the exact reference and the registry error:

   ```bash
   kubectl describe pod <pod> -n <ns>
   kubectl get events -n <ns> --field-selector involvedObject.name=<pod>
   ```

2. Confirm the image the pod is actually asking for:

   ```bash
   kubectl get pod <pod> -n <ns> -o jsonpath='{range .spec.containers[*]}{.name}{" "}{.image}{"\n"}{end}'
   ```

3. If it is a private image, check that a pull Secret exists in the same namespace and is referenced on the ServiceAccount or pod:

   ```bash
   kubectl get sa default -n <ns> -o yaml
   kubectl get secret -n <ns>
   ```

4. From a node or a debug pod, try `crictl pull` / `docker pull` with the same reference to distinguish "does not exist" from "auth" and "network".

## Fixes

- Correct the image repository, tag, or digest in the Deployment / Pod spec and roll it out.
- For private registries, create a `kubernetes.io/dockerconfigjson` Secret in the workload namespace and set `imagePullSecrets`.
- If the registry is only reachable from certain nodes, fix node networking or pin the pod with a selector that matches those nodes.
- Avoid depending on a floating `latest` tag in production; pin a tag or digest so pulls are reproducible.
- If the name is syntactically invalid (`InvalidImageName`), fix the reference — retries will never succeed until it is a valid image name.
