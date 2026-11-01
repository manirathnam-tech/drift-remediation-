# GitHub Actions CI and ECR publishing

The `CI` workflow builds the Terraform injector artifacts, checks Terraform formatting, validation and TFLint, then runs Ruff and pytest. On pushes to `main`, it builds the injector, collector, and treatment worker containers and pushes them to their ECR repositories with the commit SHA as the immutable tag. The treatment worker ECS service defaults to desired count zero; publishing its image does not start it.

## Configure AWS OIDC once

The Terraform in `infra/github_oidc.tf` creates the GitHub Actions OIDC provider and a role that trusts only `manirathnam-tech/drift-remediation-` on the `main` branch. Its IAM policy permits image upload only to the three runtime ECR repositories and grants no EKS or Terraform-state access. GitHub's OIDC flow uses a short-lived role session; the workflow does not use static AWS access keys. The job requests `id-token: write`, which allows requesting the OIDC token for AWS role exchange. [GitHub's AWS OIDC documentation](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)

After reviewing and applying the main infrastructure Terraform configuration, read the output:

```bash
terraform -chdir=infra output -raw github_actions_ecr_publisher_role_arn
```

In the GitHub repository settings, add these Actions variables:

- `AWS_ECR_PUBLISH_ROLE_ARN`: the Terraform output above.

No AWS credentials should be added as GitHub secrets for this workflow. The IAM trust policy pins both the repository and branch, as recommended for AWS OIDC trust policies. [AWS IAM OIDC guidance](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)

## Publish golden manifests from drift-gitops

`infra/golden-manifests.tf` provisions a private, encrypted, versioned S3 bucket
and a separate publisher role. The role trusts only
`manirathnam-tech/drift-gitops` on `main` and can write under `golden/*` only.
The `drift-gitops` workflow renders `apps/online-boutique` at the pushed commit
and uploads `rendered.yaml`, `source.tar.gz`, `SHA256SUMS`, and `metadata.json` to
`s3://<bucket>/golden/<commit-sha>/`. The source snapshot supports patch checks
without GitHub credentials; the rendered manifest supports retrieval of the
exact desired state. Current object versions are retained; noncurrent versions
expire after 90 days and incomplete multipart uploads are aborted after 7 days.

After AWS Organizations permits creation of the GitHub OIDC provider and the
main Terraform stack has been applied, configure these Actions variables in
the `drift-gitops` repository:

- `AWS_GOLDEN_PUBLISH_ROLE_ARN`: output `github_actions_golden_publisher_role_arn`.
- `GOLDEN_MANIFESTS_BUCKET`: output `golden_manifests_bucket_name`.

The account's current Organizations policy explicitly denies creating the OIDC
provider, so the role and workflow cannot be activated until that organization
policy is addressed. Do not replace OIDC with long-lived AWS access keys.

The ECS treatment worker is intentionally configured with `desired_count = 0`.
It uses Amazon Nova Lite in Sydney and retrieves project guidance from a
Bedrock Knowledge Base backed by S3 Vectors. Ingest the S3 data source after
Terraform creates it and again after corpus changes. Automated validation is
local to the exact GitOps snapshot; the worker has no EKS identity. A supervisor
may run server-side dry-run separately with workstation credentials.

## Run and verify

Open a pull request to run CI. The publish job runs only after a push to `main` and after both CI jobs pass. Confirm the workflow run is green, then check ECR for an image tagged with the corresponding commit SHA. No AWS resources are changed by a pull request workflow.
