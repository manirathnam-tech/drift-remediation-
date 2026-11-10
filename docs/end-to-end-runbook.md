# DRIFT end-to-end runbook

This runbook is for a controlled thesis demonstration in the `ap-southeast-2`
project account. It distinguishes infrastructure health from a successful
remediation decision: a Step Functions execution can finish `SUCCEEDED` after
recording an `ESCALATE` result.

## Current demonstrated state

The latest live execution before teardown reached classification and returned:

```json
{"status":"ESCALATE","reason":"evidence lookup failed: EvidenceRetrievalError"}
```

The treatment task role's `bedrock:Retrieve` simulation returns
`explicitDeny` with `AllowedByOrganizations: false`. This is an organization
policy gate. Do not describe the run as a completed Bedrock classification or
remediation. No proposal was produced and no GitOps patch was applied. The
deterministic baseline consumer is a separate, disabled-by-default queue and
does not receive the collector's SNS events automatically.

## Current infrastructure status

As of 2026-11-10, the demo infrastructure has been torn down. The root Terraform
destroy completed with 136 resources destroyed; the root state has no managed
resources. The EKS cluster and the empty treatment ECS cluster are gone. The
platform Terraform state has no managed resources. Its remaining data-source
state entries do not represent deployed infrastructure. Five EKS KMS keys are
pending deletion.

Research data and deployment credentials were retained for a possible rebuild:

- The corpus S3 bucket and `drift-thesis-experiment-ledger` DynamoDB table were
  backed up under `../../drift-thesis-teardown-backup-2026-11-10/`.
- Golden manifests and the Knowledge Base decision-rules document were backed
  up under `../../drift-thesis-teardown-backup-2026-11-09/` before the managed
  bucket and Knowledge Base were removed.
- Secrets Manager entries for the GitOps deploy keys and GitHub App private
  key remain. Secret values must not be printed or copied into this runbook.
- The Terraform state bucket is retained because it stores the project state.

The commands in “Before a demonstration” and later sections are for a future
rebuild. They will fail until infrastructure is deliberately provisioned again.

## Future teardown and stored data

The golden-manifests bucket is configured with Terraform `force_destroy`. A
future `make down` permanently deletes all objects in that bucket, including
golden manifests, Knowledge Base source files, and workflow inputs and state.
Before teardown, back up the current bucket contents outside AWS and verify the
backup checksums. A successful Terraform apply after setting `force_destroy` is
required before a later destroy will use the setting. Recheck the backup after
each demonstration because new workflow data may have been written since the
previous archive.

## Before a demonstration

Use WSL for AWS, Terraform, and Kubernetes commands. A renewed AWS login does
not necessarily refresh the EKS public endpoint allowlist; the WSL public IP
can change. Refresh credentials and confirm cluster access:

```bash
export AWS_PROFILE=thesis-project
export AWS_REGION=ap-southeast-2

aws sts get-caller-identity --profile "$AWS_PROFILE"
aws eks update-kubeconfig \
  --name drift-thesis-eks \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE"
kubectl get nodes
kubectl -n argocd get applications
```

If `kubectl` times out, compare the current WSL address with the EKS cluster's
`publicAccessCidrs` before changing anything:

```bash
curl -fsS https://checkip.amazonaws.com
aws eks describe-cluster \
  --name drift-thesis-eks \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'cluster.resourcesVpcConfig.publicAccessCidrs' \
  --output json
```

Only update the CIDR allowlist if the current address is absent. Preserve the
existing entries when calling `update-cluster-config`.

Confirm the target app and collector are healthy before triggering an event:

```bash
kubectl -n argocd get applications
kubectl -n argocd get deployments drift-collector drift-baseline
kubectl -n online-boutique get pods
```

The expected baseline state is zero replicas. The treatment ECS service's
persistent worker is also intended to remain at zero; the Step Functions path
launches its task for each event.

## Check Bedrock before a treatment run

Check the task role's permission first. Do not send a new event while this
returns an explicit deny:

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::218733194774:role/drift-thesis-treatment-task \
  --action-names bedrock:Retrieve \
  --resource-arns arn:aws:bedrock:ap-southeast-2:218733194774:knowledge-base/B1GD0KQ1N4 \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'EvaluationResults[].{decision:EvalDecision,organizations:OrganizationsDecisionDetail.AllowedByOrganizations}' \
  --output table
```

Proceed with a fresh test event only when the decision is `allowed` and
`organizations` is `True`. An `SUCCEEDED` Step Functions status alone does not
mean the classifier succeeded; inspect the final state output for
`classification_result.status`.

## Inspect a completed workflow

List recent executions:

```bash
STATE_MACHINE_ARN="$(terraform -chdir=infra output -raw treatment_workflow_state_machine_arn)"
aws stepfunctions list-executions \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --max-results 10 \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'executions[].{status:status,started:startDate,arn:executionArn}' \
  --output table
```

Inspect the selected execution's final state output:

```bash
aws stepfunctions get-execution-history \
  --execution-arn "$EXECUTION_ARN" \
  --reverse-order \
  --max-results 15 \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'events[?stateExitedEventDetails.output!=null].stateExitedEventDetails.output' \
  --output json
```

Expected successful treatment progress includes a classification result and a
validated proposal. `ESCALATE` is a safe terminal outcome for missing evidence,
authorization failures, invalid model output, or cases requiring human review.
The worker does not merge a pull request or apply a GitOps patch to the cluster.

## Bedrock-independent baseline arm

The deterministic baseline worker uses `drift-thesis-baseline-events`, which
is intentionally not connected to the collector SNS topic. Its Deployment
stays at zero unless a separate, explicitly approved baseline experiment is
being run. Operational severity 2 follows the hard-revert path. Critical
severity 3 is also reverted and receives a separate audit escalation for human
security review. The worker asks Argo CD to sync only the named resources in a
validated event, so publishing a message can change the live application.
Before any such experiment, confirm the event, queue, target resources, and
rollback plan with the supervisor. Do not scale the worker or enqueue a live
event as a routine health check.

## Operational constraints

- Terraform state locking is enabled. The current Organizations policy denies
  access to the S3 lock object, so Terraform operations that need the lock
  cannot proceed until that policy issue is resolved. Do not use `-lock=false`.
- Keep the collector and worker secrets out of terminal transcripts, Git, and
  screenshots. Do not print secret values while checking External Secrets.
- Do not trigger repeated test events while a previous workflow is still
  running; use a new, controlled event after checking queue and execution state.
- After a live experiment, verify the Online Boutique Argo CD app is Synced and
  Healthy, the frontend is at its baseline replica count, and optional
  consumers are stopped.

## Troubleshooting and escalation

| Symptom | Interpretation | Next action |
| --- | --- | --- |
| `kubectl` times out | WSL address may not be in the EKS public CIDR allowlist | Compare current IP and `publicAccessCidrs`; update only if needed |
| Step Functions is `SUCCEEDED`, result is `ESCALATE` | Workflow handled the failure safely; classification did not complete | Read the final state output and audit record |
| `bedrock:Retrieve` is `explicitDeny`, Organizations is `False` | The applicable Organizations policy blocks the task role | Continue with AWS Support/organization-policy investigation; do not rerun yet |
| Terraform reports S3 lock-object `AccessDenied` | The lock-file object is covered by an Organizations restriction | Keep locking enabled and ask AWS Support to identify the controlling policy |

For the current account, the Organizations API reports project account
`218733194774` under organization `o-0waq3z2tf7`, with management account
`575172595449`. The project account role cannot change policies attached from
that management account.
