locals {
  drift_collector_rbac_documents = split("---", file("${path.module}/collector-rbac.yaml"))
}

resource "kubernetes_manifest" "drift_collector_argocd_proxy_role" {
  manifest = yamldecode(local.drift_collector_rbac_documents[0])

  depends_on = [helm_release.argocd]
}

resource "kubernetes_manifest" "drift_collector_argocd_proxy_role_binding" {
  manifest = yamldecode(local.drift_collector_rbac_documents[1])

  depends_on = [kubernetes_manifest.drift_collector_argocd_proxy_role]
}
