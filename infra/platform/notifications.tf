variable "drift_collector_webhook_url" {
  description = "HTTPS endpoint for the DRIFT-28 collector API"
  type        = string

  validation {
    condition     = can(regex("^https://", var.drift_collector_webhook_url))
    error_message = "The drift collector webhook URL must use HTTPS."
  }
}

variable "argocd_notification_webhook_secret" {
  description = "Random shared secret used by Argo CD notifications to authenticate to the drift collector"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.argocd_notification_webhook_secret) >= 32
    error_message = "The Argo CD notification webhook secret must contain at least 32 characters."
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

resource "aws_secretsmanager_secret_version" "argocd_webhook" {
  secret_id     = aws_secretsmanager_secret.argocd_webhook.id
  secret_string = var.argocd_notification_webhook_secret
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
    aws_secretsmanager_secret_version.argocd_webhook
  ]
}
