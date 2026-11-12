# DNS issues inside the cluster

Pods resolve in-cluster names through CoreDNS (or a similar cluster DNS). When DNS is broken, applications fail to connect to Services using names such as `my-svc.my-ns.svc.cluster.local`, even though the destination pods are healthy.

## Symptoms

- Application logs show `unknown host`, `Name or service not known`, `no such host`, `i/o timeout` talking to a Service name, or repeated reconnects to a hostname that should exist.
- `nslookup` / `getent hosts` / `dig` from a debug container fail for `kubernetes.default` or for the target Service.
- CoreDNS pods in `kube-system` may be crash-looping, pending, or not ready. Nodes may show `NetworkUnavailable`.
- Traffic to raw ClusterIPs or pod IPs still works, which is a strong hint the problem is name resolution, not the network path itself.

## Common causes

- CoreDNS is down, not scheduled, or its Service / endpoints are empty.
- The pod's `dnsPolicy` or `dnsConfig` points at the wrong nameserver, or `ndots` / search domains are mis-set.
- A NetworkPolicy or firewall blocks UDP/TCP 53 from the workload namespace to `kube-system`.
- The client is using the wrong name (missing namespace, typo, or using an external FQDN for an in-cluster Service).
- `kube-dns` / `kubelet` cluster DNS flags are wrong, so `/etc/resolv.conf` in the pod has a bad nameserver.

## Diagnosis

1. Confirm the Service and endpoints exist, then test DNS from a throwaway pod in the same namespace:

   ```bash
   kubectl get svc,endpoints <svc> -n <ns>
   kubectl get pods -n kube-system -l k8s-app=kube-dns
   kubectl -n <ns> run dns-test --rm -it --restart=Never --image=busybox:1.36 -- nslookup kubernetes.default
   kubectl -n <ns> run dns-test --rm -it --restart=Never --image=busybox:1.36 -- nslookup <svc>.<ns>.svc.cluster.local
   ```

2. Inspect the failing pod's resolv config and DNS policy:

   ```bash
   kubectl exec <pod> -n <ns> -- cat /etc/resolv.conf
   kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.dnsPolicy} {.spec.dnsConfig}'
   ```

3. Check CoreDNS logs and NetworkPolicies that might block port 53.

## Fixes

- Restore CoreDNS (fix its CrashLoop, image pull, or resource issues) and confirm its Service has endpoints.
- Correct the hostname the app uses: short name `my-svc` only works when the search domain includes that namespace; the safest in-cluster form is `<svc>.<ns>.svc.cluster.local`.
- Loosen or add a NetworkPolicy that allows DNS to `kube-system` on port 53.
- Reset a custom `dnsConfig` / `dnsPolicy` unless you really need it (`ClusterFirst` is the right default for in-cluster workloads).
- If only one node is broken, check kubelet DNS flags and the node's own `/etc/resolv.conf`.
