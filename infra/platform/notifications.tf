variable "drift_collector_webhook_url" {
  description = "HTTPS endpoint for the DRIFT-28 collector API"
  type        = string
  default     = ""

  validation {
    condition     = var.drift_collector_webhook_url == "" || can(regex("^https?://", var.drift_collector_webhook_url))
    error_message = "Set the drift collector webhook URL to HTTPS or leave it empty until the collector exists."
  }
}

resource "aws_secretsmanager_secret" "argocd_webhook" {
  name                    = "drift-thesis-argocd-webhook"
  description             = "Shared authentication secret for Argo CD drift notifications"
  recovery_window_in_days = 7

  tags = {
    project = "drift-thesis"
  }
}

resource "aws_secretsmanager_secret" "argocd_collector_api_token" {
  name                    = "drift-thesis-argocd-collector-api-token"
  description             = "Read-only Argo CD API token for the drift collector"
  recovery_window_in_days = 7

  tags = {
    project = "drift-thesis"
  }
}

resource "kubernetes_manifest" "argocd_webhook_external_secret" {
  manifest = {
    apiVersion = "external-secrets.io/v1"
    kind       = "ExternalSecret"
    metadata = {
      name      = "argocd-notifications-webhook"
      namespace = "argocd"
    }
    spec = {
      refreshInterval = "1h"
      secretStoreRef = {
        name = "aws-secrets-manager"
        kind = "ClusterSecretStore"
      }
      target = {
        name           = "argocd-notifications-secret"
        creationPolicy = "Merge"
        template = {
          type = "Opaque"
          data = {
            "drift-collector-webhook-secret" = "{{ .webhookSecret }}"
          }
        }
      }
      data = [{
        secretKey = "webhookSecret"
        remoteRef = {
          key = aws_secretsmanager_secret.argocd_webhook.name
        }
      }]
    }
  }

  depends_on = [
    helm_release.argocd,
    helm_release.external_secrets,
    aws_iam_role_policy.external_secrets,
    aws_secretsmanager_secret.argocd_webhook
  ]
}
