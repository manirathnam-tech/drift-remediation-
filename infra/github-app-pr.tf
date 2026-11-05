variable "github_app_id" {
  description = "GitHub App ID used only for approved draft-PR creation. Configure locally; do not commit real account values."
  type        = string
  default     = "5590367"
}

variable "github_app_installation_id" {
  description = "Installation ID for the GitOps repository."
  type        = string
  default     = "178069848"
}

variable "github_app_private_key_secret_arn" {
  description = "Optional Secrets Manager ARN containing the GitHub App private key; never place the key in Terraform variables."
  type        = string
  default     = ""
}


output "github_app_pr_configuration_ready" {
  description = "Whether the optional GitHub App draft-PR configuration is present."
  value       = var.github_app_id != "" && var.github_app_installation_id != "" && var.github_app_private_key_secret_arn != ""
}
