variable "aws_region" {
  description = "AWS region for the single-node k3s VM."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for AWS resources."
  type        = string
  default     = "kube-copilot"
}

variable "instance_type" {
  description = "EC2 instance type. t3.small (2 GiB) is the comfortable minimum for k3s plus the chart; t3.micro is free-tier eligible but tight."
  type        = string
  default     = "t3.small"
}

variable "ssh_cidr" {
  description = "CIDR allowed to SSH and to reach the Kubernetes API. Lock this to your IP before applying."
  type        = string
  default     = "0.0.0.0/0"
}

variable "k3s_version" {
  description = "k3s version channel or tag passed to the installer (empty = stable)."
  type        = string
  default     = ""
}

variable "llm_api_key" {
  description = "Optional Groq/OpenAI key installed into the cluster as a Helm value. Leave empty to install without Diagnose."
  type        = string
  sensitive   = true
  default     = ""
}

variable "llm_base_url" {
  type    = string
  default = "https://api.groq.com/openai/v1"
}

variable "llm_model" {
  type    = string
  default = "llama-3.3-70b-versatile"
}

variable "helm_chart_path" {
  description = "Path to the kube-copilot Helm chart, relative to this module or absolute."
  type        = string
  default     = "../helm/kube-copilot"
}

variable "helm_namespace" {
  type    = string
  default = "kube-copilot"
}

variable "image_repository_backend" {
  type    = string
  default = "ghcr.io/kayd-06/kube-copilot-backend"
}

variable "image_repository_frontend" {
  type    = string
  default = "ghcr.io/kayd-06/kube-copilot-frontend"
}

variable "image_tag" {
  type    = string
  default = "0.4.0"
}
