output "public_ip" {
  description = "Public IPv4 of the k3s VM."
  value       = aws_instance.k3s.public_ip
}

output "ssh_command" {
  description = "SSH into the VM. The private key is written next to this module and is gitignored."
  value       = "ssh -i ${local_sensitive_file.ssh_key.filename} ubuntu@${aws_instance.k3s.public_ip}"
}

output "kubeconfig_path" {
  description = "Kubeconfig rewritten to use the VM's public IP."
  value       = local.kubeconfig_path
}

output "helm_release" {
  value = helm_release.kube_copilot.name
}

output "next_steps" {
  value = <<-EOT
    export KUBECONFIG=${local.kubeconfig_path}
    kubectl get nodes
    kubectl -n ${var.helm_namespace} port-forward svc/kube-copilot-frontend 3000:3000
    # then open http://localhost:3000
  EOT
}
