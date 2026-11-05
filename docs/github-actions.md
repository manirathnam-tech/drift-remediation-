# CI and local AWS publishing

GitHub Actions runs formatting, Terraform validation, TFLint, Ruff, unit tests,
and policy checks. It does not authenticate to AWS or publish images because
this account's AWS Organizations policy denies creating a GitHub OIDC provider.
Do not add long-lived AWS access keys to GitHub.

After Terraform has created the ECR repositories, publish images from the
workstation with a valid AWS CLI profile:

```bash
export AWS_PROFILE=thesis-project
export AWS_REGION=ap-southeast-2
bash scripts/publish-images.sh
```

The script uses the current commit's short SHA as an immutable image tag and
publishes injector, collector, treatment-worker, and baseline images. Update
the image references in the Kubernetes runtime manifests to the printed tag
before applying them. Publishing does not start the treatment worker or the
baseline consumer.

Golden manifests can also be uploaded locally. Render a commit from the
`drift-gitops` repository, create `SHA256SUMS` and `metadata.json`, then upload
the directory to `s3://<golden-bucket>/golden/<commit-sha>/` with the user's
AWS profile. The GitOps repository's GitHub workflow cannot assume an AWS role
while the organization policy blocks OIDC provider creation.

The ECS treatment worker remains at desired count zero outside an authorized
trial. The baseline Deployment also starts with zero replicas. The collector
Deployment polls its SQS queue; the API Gateway Lambda only places webhook
requests onto that queue. The injector runs as a Kubernetes Job using its
Pod Identity role.
