data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "tls_private_key" "ssh" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "this" {
  key_name   = "${var.name}-k3s"
  public_key = tls_private_key.ssh.public_key_openssh
}

resource "local_sensitive_file" "ssh_key" {
  filename        = "${path.module}/${var.name}-ssh.pem"
  content         = tls_private_key.ssh.private_key_openssh
  file_permission = "0600"
}

resource "aws_security_group" "k3s" {
  name        = "${var.name}-k3s"
  description = "SSH, k3s API, and HTTP(S) for a single-node Kube Copilot demo."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  ingress {
    description = "Kubernetes API (k3s)"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.name}-k3s"
  }
}

locals {
  kubeconfig_path = "${path.module}/${var.name}.kubeconfig"
  k3s_install     = var.k3s_version == "" ? "curl -sfL https://get.k3s.io | sh -" : "curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=${var.k3s_version} sh -"
  user_data       = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    apt-get update
    apt-get install -y curl
    ${local.k3s_install}
    # Make kubeconfig readable for the ubuntu user so Terraform can scp it.
    mkdir -p /home/ubuntu/.kube
    cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
    chown -R ubuntu:ubuntu /home/ubuntu/.kube
    chmod 600 /home/ubuntu/.kube/config
  EOT
}

resource "aws_instance" "k3s" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  key_name                    = aws_key_pair.this.key_name
  subnet_id                   = data.aws_subnets.default.ids[0]
  vpc_security_group_ids      = [aws_security_group.k3s.id]
  associate_public_ip_address = true
  user_data                   = local.user_data

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = {
    Name = "${var.name}-k3s"
  }
}

# Wait until k3s is up and copy kubeconfig, rewriting the server to the public IP.
resource "null_resource" "kubeconfig" {
  triggers = {
    instance_id = aws_instance.k3s.id
    public_ip   = aws_instance.k3s.public_ip
  }

  connection {
    type        = "ssh"
    host        = aws_instance.k3s.public_ip
    user        = "ubuntu"
    private_key = tls_private_key.ssh.private_key_openssh
  }

  provisioner "remote-exec" {
    inline = [
      "timeout 180 bash -c 'until [ -f /etc/rancher/k3s/k3s.yaml ] && sudo k3s kubectl get nodes >/dev/null 2>&1; do sleep 5; done'",
      "sudo cp /etc/rancher/k3s/k3s.yaml /tmp/k3s.yaml && sudo chmod 644 /tmp/k3s.yaml",
    ]
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ${local_sensitive_file.ssh_key.filename} ubuntu@${aws_instance.k3s.public_ip}:/tmp/k3s.yaml ${local.kubeconfig_path}
      sed -i.bak 's#https://127.0.0.1:6443#https://${aws_instance.k3s.public_ip}:6443#' ${local.kubeconfig_path}
      rm -f ${local.kubeconfig_path}.bak
    EOT
  }
}

resource "helm_release" "kube_copilot" {
  depends_on = [null_resource.kubeconfig]

  name             = "kube-copilot"
  chart            = var.helm_chart_path
  namespace        = var.helm_namespace
  create_namespace = true
  wait             = true
  timeout          = 600

  set {
    name  = "backend.image.repository"
    value = var.image_repository_backend
  }
  set {
    name  = "backend.image.tag"
    value = var.image_tag
  }
  set {
    name  = "backend.image.pullPolicy"
    value = "IfNotPresent"
  }
  set {
    name  = "frontend.image.repository"
    value = var.image_repository_frontend
  }
  set {
    name  = "frontend.image.tag"
    value = var.image_tag
  }
  set {
    name  = "llm.baseUrl"
    value = var.llm_base_url
  }
  set {
    name  = "llm.model"
    value = var.llm_model
  }

  set_sensitive {
    name  = "llm.apiKey"
    value = var.llm_api_key
  }
}
