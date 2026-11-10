# DRIFT-28: Argo CD drift event collector

The collector accepts Argo CD `OutOfSync` notifications at an HTTPS API Gateway endpoint. A non-VPC relay Lambda places the original request on SQS. The in-cluster collector pod then authenticates the `X-Drift-Webhook-Secret` header, restricts events to the Online Boutique resource allowlist, confirms the current application/resource state through Argo CD, and publishes normalized `DriftEvent v1` JSON to SNS. Duplicate payloads are suppressed in DynamoDB; repeated queue failures go to the SQS dead-letter queue. The SNS topic is the downstream integration point for remediation components.

The collector worker runs in the `argocd` namespace and uses EKS Pod Identity for AWS access. It calls Argo CD with a dedicated API key account that can only `get` the `default/online-boutique` Application. Argo CD itself remains ClusterIP and has no public load balancer or ingress. The API Gateway Lambda has no VPC attachment and only has permission to send messages to the collector queue.

The collector reaches Argo CD through its in-cluster service DNS name and uses the Argo CD server certificate mounted from the `drift-argocd-server-ca` ConfigMap. It does not call the EKS API directly and does not need the EKS endpoint or cluster CA in its environment.

## Deployment order

The API URL and Argo API token are generated during deployment, so bootstrap in this order:

1. Apply the platform Terraform with `drift_collector_webhook_url` and `argocd_collector_api_token` empty. Keep `argocd_notification_webhook_secret` set to a random value of at least 32 characters. This configures the read-only Argo account and webhook secret.
2. Apply the root `infra` Terraform to create the collector relay, API Gateway endpoint, collector Pod Identity, SNS topic, and SQS queues.
3. Generate a token for `drift-collector` using the Argo CD CLI. Supply it as the sensitive Terraform input `argocd_collector_api_token` for the platform configuration, and set `drift_collector_webhook_url` to the root output `drift_collector_api_url`. Apply platform Terraform again. The token is stored in Secrets Manager and Terraform state; state access must remain restricted.
4. Publish and apply the collector image, confirm the ExternalSecret is Ready and the Argo account is configured, then send a test notification. Verify it reaches the collector SNS topic. Inspect queue and DLQ counts without printing secret values.

The API Gateway endpoint is internet reachable, but the only accepted route is `POST /events`, authenticated by the shared secret header and protected by API Gateway throttling. The in-cluster HTTP receiver also caps request bodies at 128 KiB and rejects requests without a valid `Content-Length`. The SQS consumer restarts its polling loop after unexpected AWS or worker errors. Rotate the webhook secret in Secrets Manager and Terraform together if it is exposed. Do not put either token or webhook secret in Git.

## Event timestamps

`reconciled_at` is copied from Argo CD's latest reconciliation timestamp and is used to reject stale application revisions. `t_detect` and `received_at` are collector receipt timestamps; the notification template does not provide a trusted per-resource detection timestamp. Detection-latency experiments use the first observed OutOfSync state and the injector's `t_inject`, recorded separately by the measurement harness.

The contract is in `infra/collector/src/drift-event-v1.schema.json`. Events are synthetic experiment telemetry, not production incident evidence.
