# Argo CD drift notifications

The pinned Argo CD Helm chart (`argo-cd` 10.10.3) configures the `on-drift-out-of-sync` trigger. It posts the application name, destination namespace, source revision, sync status and only the OutOfSync resource entries to the DRIFT-28 collector over HTTPS. The sample request is in `harness/fixtures/argocd-out-of-sync.json`.

The shared authentication token is held in AWS Secrets Manager as `drift-thesis-argocd-webhook`. The pinned Helm chart creates `argocd/argocd-notifications-secret`; External Secrets merges the token key into that Secret, and Argo CD reads it through a secret reference in the webhook header. The external-secrets role is allowed to read only this secret and the existing GitOps deploy key.

Terraform state contains the sensitive secret input and is stored in the encrypted, access-controlled S3 state backend. Generate a fresh value in the shell without printing it, then pass it through the environment when applying the platform stack:

```bash
export TF_VAR_argocd_notification_webhook_secret="$(openssl rand -hex 32)"
export TF_VAR_drift_collector_webhook_url="https://<DRIFT-28-api-id>.execute-api.ap-southeast-2.amazonaws.com/events"
terraform -chdir=infra/platform plan
terraform -chdir=infra/platform apply
unset TF_VAR_argocd_notification_webhook_secret TF_VAR_drift_collector_webhook_url
```

The collector URL is required and must be HTTPS. Apply this after DRIFT-28 creates the API endpoint. The CI plan validates the configuration without contacting AWS or the Kubernetes API; live delivery requires the cluster, External Secrets CRDs and collector endpoint to be available.
