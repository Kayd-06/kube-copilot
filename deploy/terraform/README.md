# Terraform: single-node k3s on a cheap AWS VM

Provisions a Ubuntu 24.04 EC2 instance, installs [k3s](https://k3s.io/), fetches a kubeconfig, and deploys the `kube-copilot` Helm chart with `helm_release`.

**Do not apply this from CI.** Review the plan, lock `ssh_cidr` to your IP, and expect a small AWS bill (a `t3.small` is recommended; `t3.micro` is free-tier eligible but tight for k3s plus the app).

## Usage

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars  # edit ssh_cidr and llm_api_key
terraform init
terraform fmt
terraform validate
terraform plan
# terraform apply    # only when you intend to spend money
```

When you are done: `terraform destroy`.

The SSH private key and kubeconfig are written next to this module and are gitignored. Images default to `ghcr.io/kayd-06/kube-copilot-{backend,frontend}:0.4.0`, which are published by the GitHub Actions workflow on `main` and on version tags.
