resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "oci://ghcr.io/argoproj/argo-helm"
  chart            = "argo-cd"
  version          = "10.10.3"
  namespace        = "argocd"
  create_namespace = true

  values = [
    yamlencode({
      configs = {
        cm = {
          "timeout.reconciliation"        = "30s"
          "timeout.reconciliation.jitter" = "0s"
          "accounts.drift-collector"      = "apiKey"
        }
        rbac = {
          policy = {
            csv = "p, role:drift-collector, applications, get, default/online-boutique, allow\ng, drift-collector, role:drift-collector"
          }
        }
      }

      notifications = {
        enabled = true
        secret = {
          create = true
          name   = "argocd-notifications-secret"
        }
        notifiers = var.drift_collector_webhook_url == "" ? {} : {
          "service.webhook.drift-collector" = <<-EOT
            url: ${var.drift_collector_webhook_url}
            headers:
              - name: Content-Type
                value: application/json
              - name: X-Drift-Webhook-Secret
                value: $drift-collector-webhook-secret
            retryMax: 3
          EOT
        }
        templates = {
          "template.drift-out-of-sync" = <<-EOT
            webhook:
              drift-collector:
                method: POST
                body: |
                  {
                    "event_type": "argocd.application.out_of_sync",
                    "application": "{{ .app.metadata.name }}",
                    "namespace": "{{ .app.spec.destination.namespace }}",
                    "revision": "{{ .app.status.sync.revision }}",
                    "reconciled_at": "{{ .app.status.reconciledAt }}",
                    "sync_status": "{{ .app.status.sync.status }}",
                    "resources": [
                      {{- $first := true -}}
                      {{- range .app.status.resources -}}
                      {{- if eq .status "OutOfSync" -}}
                      {{- if not $first }},{{ end -}}
                      {{ toJson . }}
                      {{- $first = false -}}
                      {{- end -}}
                      {{- end -}}
                    ]
                  }
          EOT
        }
        triggers = {
          "trigger.on-drift-out-of-sync" = <<-EOT
            - description: Application has one or more resources out of sync
              send:
                - drift-out-of-sync
              when: app.status.sync.status == 'OutOfSync'
          EOT
        }
      }

      server = {
        ingress = {
          enabled = false
        }

        service = {
          type = "ClusterIP"
        }
      }
    })
  ]
}

resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  repository       = "oci://ghcr.io/external-secrets/charts"
  chart            = "external-secrets"
  version          = "2.13.0"
  namespace        = "external-secrets"
  create_namespace = true

  depends_on = [
    aws_eks_pod_identity_association.external_secrets
  ]
}
