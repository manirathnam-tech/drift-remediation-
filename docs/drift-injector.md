# DRIFT injector

Synthetic drift is injected by a short-lived Kubernetes Job in the
`online-boutique` namespace. The Job uses the injector Pod Identity role and
the frozen corpus allowlist; it accepts a corpus record ID rather than an
arbitrary patch. Successful changes record `t_inject`, trial ID, labels,
mutation, and trigger source in DynamoDB. A Lambda function and VPC attachment
are not part of this runtime.

Build and publish the injector image after Terraform creates its ECR
repository:

```bash
export AWS_PROFILE=thesis-project
export AWS_REGION=ap-southeast-2
bash scripts/publish-images.sh
```

Use the printed immutable tag in your injector Job manifest. Before a trial,
confirm `kubectl get application online-boutique -n argocd` reports `Synced`
and `Healthy`, use a unique trial ID, then monitor and reset the drift after
capturing its evidence. The complete controlled-trial commands are in
`harness/README.md`.

The optional EventBridge-to-Lambda schedule was removed. Launching each
injection explicitly keeps trial timing under operator control.
