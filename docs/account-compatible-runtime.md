# Account-compatible runtime

The current AWS Organizations policy denies EC2 network-interface actions to
Lambda execution roles and denies creating IAM OIDC providers. The default
runtime therefore avoids VPC-attached Lambdas and GitHub-to-AWS OIDC:

```text
Argo CD webhook -> API Gateway -> non-VPC relay Lambda -> collector SQS
  -> collector EKS pod -> Argo CD API / DynamoDB / SNS

Approved injector request -> in-cluster Kubernetes Job -> DynamoDB
Baseline SQS -> opt-in baseline EKS pod -> Kubernetes API / audit DynamoDB
Treatment SQS -> EventBridge Pipes -> workflow Lambda -> Step Functions / ECS
```

The relay Lambda only queues the original request; the collector pod continues
to verify the shared secret, validate the allowlist, enrich from Argo CD,
deduplicate, audit, and publish. The baseline worker uses a Kubernetes service
account token with namespaced RBAC, while AWS access is supplied separately by
EKS Pod Identity. Both queue consumers use SQS redrive policies.

Runtime container publishing is a workstation action through the AWS CLI
profile. Pull-request CI remains AWS-free. Golden-manifest upload is also a
local action until GitHub OIDC is available. The baseline Deployment and ECS
treatment service default to zero running workers.

## Apply guardrails

Do not apply the earlier Terraform plan that created Lambda ENIs. Reinitialize
and create a fresh plan from this branch, then inspect all resource actions
before applying. Expected removals include the VPC-attached injector and
baseline Lambdas and the GitHub OIDC publisher roles/provider; the collector
Lambda remains as a non-VPC SQS relay. Confirm the plan does not delete the
managed Bedrock knowledge base or its data source. A plan that still contains
those deletions must not be applied.

This Terraform change does not undo the already-created EKS cluster, node
group, NAT gateway, or other partial stack resources. Those remain live until
Terraform successfully reconciles the full configuration or the operator
intentionally tears them down.
