provider "aws" {
  region = var.aws_region
}

# The Helm provider is configured after the instance is up and kubeconfig
# has been fetched (see main.tf). terraform validate does not need a live
# cluster; do not terraform apply from CI.
provider "helm" {
  kubernetes {
    config_path = local.kubeconfig_path
  }
}
