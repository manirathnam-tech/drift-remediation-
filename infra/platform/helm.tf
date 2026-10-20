resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "oci://ghcr.io/argoproj/argo-helm"
  chart            = "argo-cd"
  version          = "10.10.3"
  namespace        = "argocd"
  create_namespace = true

  values = [
    yamlencode({
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
