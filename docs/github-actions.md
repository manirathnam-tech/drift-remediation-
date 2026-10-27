# GitHub Actions CI and ECR publishing

The `CI` workflow builds the Terraform injector artifacts, checks Terraform formatting, validation and TFLint, then runs Ruff and pytest. On pushes to `main`, it also builds the injector container and pushes it to the existing `drift-thesis-injector` ECR repository with the commit SHA as its tag.

## Configure AWS OIDC once

The Terraform in `infra/github_oidc.tf` creates the GitHub Actions OIDC provider and a role that trusts only `manirathnam-tech/drift-remediation-` on the `main` branch. Its IAM policy permits image upload to the injector repository and grants no EKS or Terraform-state access. GitHub's OIDC flow uses a short-lived role session; the workflow does not use static AWS access keys. The job requests `id-token: write`, which allows requesting the OIDC token for AWS role exchange. [GitHub's AWS OIDC documentation](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)

After reviewing and applying the main infrastructure Terraform configuration, read the output:

```bash
terraform -chdir=infra output -raw github_actions_ecr_publisher_role_arn
```

In the GitHub repository settings, add these Actions variables:

- `AWS_ECR_PUBLISH_ROLE_ARN`: the Terraform output above.

No AWS credentials should be added as GitHub secrets for this workflow. The IAM trust policy pins both the repository and branch, as recommended for AWS OIDC trust policies. [AWS IAM OIDC guidance](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)

## Run and verify

Open a pull request to run CI. The publish job runs only after a push to `main` and after both CI jobs pass. Confirm the workflow run is green, then check ECR for an image tagged with the corresponding commit SHA. No AWS resources are changed by a pull request workflow.
