# DRIFT project completion checklist

Status snapshot: 2026-11-10. Demo infrastructure is currently torn down. Research
data and GitHub deployment credentials are being retained through Friday.

## Completed and merged

- DRIFT 35: versioned golden manifests.
- DRIFT 47: Step Functions treatment workflow.
- DRIFT 48: approved GitHub draft-PR workflow.
- DRIFT 49: severity-aware GitOps PR checks and merge gates.
- DRIFT 50: deterministic baseline remediation arm.
- DRIFT 51: managed Bedrock Knowledge Base.
- DRIFT 52: account-compatible runtime.
- DRIFT 53: collector concurrency adjustment.
- DRIFT 54: injector image configuration.

The live demo exercised Argo CD notification, collector ingestion, workflow
ingestion, and golden-manifest verification. The latest treatment execution
ended safely with `ESCALATE` because Bedrock Knowledge Base retrieval failed;
it did not produce a treatment proposal or a GitOps PR.

## Required to claim a successful end-to-end treatment

1. **Resolve Bedrock authorization.** The treatment role's
   `bedrock:Retrieve` evaluation returned `explicitDeny` with
   `AllowedByOrganizations: false`. Ask the organization management account
   owner or AWS Support to identify and resolve the applicable policy. Recheck
   the role after that change.
2. **Confirm model inference availability.** Verify the selected model's
   agreement, authorization, regional availability, and usable quota with a
   small direct request. Titan Text Embeddings V2 was directly invoked and
   returned 1024 dimensions; this does not prove Knowledge Base retrieval works.
3. **Review the unmerged Titan Knowledge Base configuration.** Validate the
   custom embedding configuration and produce a fresh Terraform plan before
   rebuilding. The Knowledge Base must be recreated and its corpus re-ingested
   before retrieval can be demonstrated.
4. **Rebuild only for an approved demonstration window.** Root and platform
   Terraform states currently have no managed resources. Recreate the runtime,
   publish immutable images, and apply the current GitOps manifests only after
   reviewing the cost and resource plan.
5. **Run one controlled end-to-end event.** Verify successful evidence
   retrieval, classification, proposal validation, audit records, and draft PR
   creation. Confirm the PR checks and human review gate; do not merge or apply
   a patch automatically.
6. **Report the outcome precisely.** A Step Functions `SUCCEEDED` status can
   contain an `ESCALATE` classification. Record the final classification and
   proposal status rather than treating workflow completion as model success.

## Offline work to finish before rebuilding

- Review and split the uncommitted local changes into focused branches/PRs.
  They include Titan Knowledge Base configuration, baseline evaluation and
  rules, collector hardening, GitHub App PR handling, teardown inventory, and
  runbook updates. Do not commit generated ZIPs, credentials, local variable
  files, or unrelated cost-log edits.
- Review the baseline's synthetic development results: 60/60 records were
  classified; class accuracy was 0.500 and severity accuracy was 0.400. These
  are diagnostics on synthetic development fixtures, not real-world results.
  Keep the held-out split out of rule tuning and keep the baseline worker
  disabled unless running an approved experiment.
- [x] Remove the unused EKS endpoint and EKS API CA from the collector runtime;
  it uses in-cluster Argo CD DNS and its own TLS certificate.
- [x] Keep the treatment event pipe stopped during bootstrap and enable it only
  after the cluster, GitOps app, and collector are ready.
- [x] Check the treatment role's `bedrock:Retrieve` IAM and Organizations
  decisions before enabling the event pipe; startup fails closed if either
  check is not allowed.
- [x] Refresh the WSL public IP allowlist when resuming a partially completed
  startup, since that address can change between attempts.
- Resolve image and secret ARN placeholders from current infrastructure outputs
  during a future rebuild.
- Re-run the repository CI checks for each focused PR before merging.

## Teardown and retained assets

- Root Terraform destroy completed with 136 resources destroyed; root state has
  no managed resources. Platform state has no managed resources.
- EKS and the empty treatment ECS cluster are gone. Five EKS KMS keys are
  pending deletion.
- Retained through Friday: the corpus S3 bucket, the experiment ledger table,
  and three Secrets Manager entries for GitHub deployment credentials.
- Backups are under `../../drift-thesis-teardown-backup-2026-11-09/` and
  `../../drift-thesis-teardown-backup-2026-11-10/`.
- Keep the Terraform state bucket. Do not delete retained data or credentials
  until the owner decides they are no longer needed.
