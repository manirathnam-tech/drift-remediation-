# DRIFT-28: Argo CD drift event collector

The collector accepts Argo CD `OutOfSync` notifications at an HTTPS API Gateway endpoint. It authenticates the `X-Drift-Webhook-Secret` header, restricts events to the Online Boutique resource allowlist, confirms the current application/resource state through Argo CD, and publishes normalized `DriftEvent v1` JSON to SNS. Duplicate payloads are suppressed in DynamoDB; authenticated processing failures are copied to the SQS review queue. The SNS topic is the downstream integration point for future remediation components.

The collector Lambda runs in private subnets. It calls the private EKS API endpoint, using an EKS access entry and a namespaced Role limited to `get` on the `argocd-server` service proxy. It then calls Argo CD with a dedicated API key account that can only `get` the `default/online-boutique` Application. Argo CD itself remains ClusterIP and has no public load balancer or ingress.

## Deployment order

The API URL and Argo API token are generated during deployment, so bootstrap in this order:

1. Apply the platform Terraform with `drift_collector_webhook_url` and `argocd_collector_api_token` empty. Keep `argocd_notification_webhook_secret` set to a random value of at least 32 characters. This configures the read-only Argo account and webhook secret.
2. Apply the root `infra` Terraform to create the collector, API Gateway endpoint, EKS access entry, SNS topic, and SQS queue.
3. Generate a token for `drift-collector` using the Argo CD CLI. Supply it as the sensitive Terraform input `argocd_collector_api_token` for the platform configuration, and set `drift_collector_webhook_url` to the root output `drift_collector_api_url`. Apply platform Terraform again. The token is stored in Secrets Manager and Terraform state; state access must remain restricted.
4. Confirm the ExternalSecret is Ready, the RoleBinding exists in `argocd`, and an Argo CD `OutOfSync` event reaches the collector SNS topic. Inspect quarantined failures in the SQS queue without printing secret values.

The API Gateway endpoint is internet reachable, but the only accepted route is `POST /events`, authenticated by the shared secret header and protected by API Gateway throttling. Rotate the webhook secret in Secrets Manager and Terraform together if it is exposed. Do not put either token or webhook secret in Git.

## Event timestamps

`reconciled_at` is copied from Argo CD's latest reconciliation timestamp and is used to reject stale application revisions. `t_detect` and `received_at` are collector receipt timestamps; the notification template does not provide a trusted per-resource detection timestamp. Detection-latency experiments use the first observed OutOfSync state and the injector's `t_inject`, recorded separately by the measurement harness.

The contract is in `infra/collector/src/drift-event-v1.schema.json`. Events are synthetic experiment telemetry, not production incident evidence.
