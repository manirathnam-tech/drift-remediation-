# Deterministic baseline arm

The baseline classifier uses the reviewed rules in `src/rules.py`, records
classification and sync-request evidence in the treatment audit table, and
asks Argo CD to sync only the resources named in the validated event. It does
not use a model. The consumer is an EKS Deployment with replicas set to zero by
default; scale it up only for an authorized experiment, then scale it back to
zero. Its separate SQS queue is not attached to the shared SNS fanout.

The pod uses its Kubernetes service-account token to call the in-cluster API
and an EKS Pod Identity association for DynamoDB and SQS. RBAC is limited to
get/patch on the `online-boutique` Application in `argocd`. No Lambda VPC
attachment is needed.

Build and publish after Terraform creates the ECR repository:

```bash
bash scripts/publish-images.sh
```

Set the resulting immutable tag in
`infra/platform/baseline-runtime.yaml`. Keep replicas at zero in the checked-in
manifest. For a trial, apply the RBAC and runtime manifests, then scale the
Deployment to one replica; return it to zero when the queue is drained.

The classifier preserves detection, receipt, and reconciliation timestamps.
Unknown changed fields receive severity 2, and missing evidence does not
become benign. Messages are retried by SQS and sent to the DLQ after repeated
failures. DynamoDB audit records are deterministic per event and stage.
