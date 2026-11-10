# DRIFT treatment worker

The treatment path consumes the SNS fanout queue `drift-thesis-treatment-events`
with a single ECS Fargate worker. The service is provisioned with desired count
zero, so the worker does not run outside an explicitly configured session.
Messages that fail processing are retried by SQS and move to
`drift-thesis-treatment-dlq` after three receives.

## Processing stages

1. Receive a collector `DriftEvent` from SQS using a 20-second long poll.
2. Load `rendered.yaml`, `source.tar.gz`, `SHA256SUMS`, and `metadata.json` for
   the event's full Git revision from the private golden-manifest S3 bucket.
   Check repository identity, revision, and both artifact checksums.
3. Retrieve up to five supporting excerpts from the Bedrock Knowledge Base.
   Each excerpt has a stable evidence ID and source URI. The audit ledger keeps
   the bounded excerpt and its citation for later review. Missing or failed
   retrieval causes escalation.
4. Ask Amazon Nova Lite in Sydney to classify drift using the versioned prompt,
   JSON schema, and temperature zero. Record model and prompt provenance.
5. Escalate cases that require review. Otherwise, ask the model for a small
   GitOps patch, with at most two retries after the initial proposal.
6. Apply the patch only inside a temporary copy of the exact golden source
   snapshot. Render Kustomize output and run kubeconform, Conftest, and Checkov.
   The code does not apply or merge a patch.
7. Append each stage and verdict to the treatment audit DynamoDB table. Delete
   the SQS message only after a durable `VALIDATED` or `ESCALATE` result.

## Provision and load the retrieval corpus

Terraform manages a Bedrock managed Knowledge Base with a custom Amazon Titan
Text Embeddings V2 model (`amazon.titan-embed-text-v2:0`, 1024 dimensions,
float32) and a managed S3 connector. It uploads the curated Markdown files from
`infra/orchestrator/knowledge_base/` into the private golden-manifest bucket.
The Knowledge Base role is limited to reading that corpus and invoking the
selected embedding model. Changing between managed and custom embedding types
requires creating a new Knowledge Base. After Terraform creates the Knowledge
Base and data source, ingest the corpus from an operator shell:

```sh
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$(terraform -chdir=infra output -raw treatment_knowledge_base_id)" \
  --data-source-id "$(terraform -chdir=infra output -raw treatment_knowledge_base_data_source_id)" \
  --region ap-southeast-2 --profile thesis-project
```

Wait for the ingestion job to report `COMPLETE` before starting the worker.
Repeat ingestion after changing the corpus. The worker's default ECS desired
count remains zero; set it to one only for an approved run, and restore it to
zero afterwards.

## Dev-only model comparison

The runner refuses to start model requests unless the operator explicitly
confirms them. It reads only `harness/dev.jsonl`; it does not pass labels,
expected remediation, rationale, or held-out split rows to a model.

```sh
export AWS_PROFILE=thesis-project
python3 experiments/run_bedrock_eval.py \
  --models nova-lite,claude-haiku-4-5-au \
  --confirm-model-calls

python3 experiments/eval_classify.py \
  --prediction nova-lite=experiments/results/nova-lite-dev-predictions.jsonl \
  --prediction claude-haiku-4-5-au=experiments/results/claude-haiku-4-5-au-dev-predictions.jsonl \
  --out experiments/results/dev-model-comparison.md
```

The Claude comparator uses an Australian geo inference profile; requests may
route between Sydney and Melbourne. Keep it out of the normal worker IAM role
unless a later measured decision explicitly selects it.

## Validation boundary and remaining setup

`validate_manifest.py --json-report <path>` writes per-check verdicts, bounded tool output and
elapsed milliseconds. Patch evaluation records use JSONL with `id`, `attempt`,
`checks`, and optional `duration_ms`. Record the independent `git_apply` result
alongside the manifest checks. `experiments/eval_patch_validity.py --records
<results.jsonl> --out <metrics.json>` scores only attempt 1 for dev IDs. It
reports static and full-gate rates separately; skipped admission checks never
count as a full-gate pass. No measured results are supplied until those gates
are actually run on generated proposals.

To validate saved proposals without model calls, use JSONL rows containing
`id`, `attempt` (1–3), `revision` (full Git SHA), and `proposal` (the patch
proposal contract). The runner refuses non-dev IDs and duplicate attempts,
exports the exact committed Git snapshot, and validates in a temporary copy.
It never changes the source checkout. Use a new output filename for each run:

```sh
python3 experiments/run_patch_eval.py \
  --proposals experiments/results/dev-proposals.jsonl \
  --gitops-checkout ../drift-gitops \
  --revision FULL_GIT_SHA \
  --out experiments/results/dev-validation.jsonl
python3 experiments/eval_patch_validity.py \
  --records experiments/results/dev-validation.jsonl \
  --out experiments/results/dev-validity.json
```

This runner requires git, kubectl (for local rendering), kubeconform, Conftest,
and Checkov on PATH. It does not request server-side validation. Missing tools
and timeouts are recorded as errors; skipped checks never count as passes.
The worker records the same structured gate outcomes in its audit table,
correlated by event ID, revision, and patch SHA-256.

Automated worker validation is local to the verified source snapshot: patch
checks, Kustomize, kubeconform, Conftest, and Checkov. It does not verify live
API admission. A supervisor can run the validator's optional server-side dry
run separately from a workstation with Kubernetes credentials. The worker has
no EKS identity or write permission because Kubernetes authorizes dry-run with
the same verbs as a real write.

Before deployment, publish the immutable worker image from the workstation,
ingest the knowledge-base corpus, and configure the ECS service desired count
for the approved window. GitHub Actions remains AWS-free because this account's
Organizations policy blocks GitHub OIDC provider creation.

## Local verification and worker controls

Build the treatment image and run policy unit tests without AWS credentials:

```sh
docker build -f infra/orchestrator/Dockerfile -t drift-treatment:local .
make policy-test
```

The policy test container has networking disabled. Tests cover ordinary and
init containers, host access, non-root settings, resource declarations, broad
RBAC grants, public services, and NetworkPolicy fields. CI runs the same tests.

`python experiments/check_knowledge_leakage.py` checks the Markdown files that
Terraform selects for ingestion against dev/test identities. It checks IDs,
not semantic overlap or adjudication. The current curated corpus contains no
dev examples; do not describe it as an adjudicated example index. Live retrieval
quality and the planned ten-query check remain outstanding.

The container also provides `/app/validator/src/smoke_validation.py`. With
networking disabled it checks that kubeconform rejects malformed YAML and that
Conftest and Checkov reject a privileged Pod. It emits individual durations.
These seeded smoke checks are distinct from schema coverage (including CRDs),
dev-proposal pass rates, and live API admission tests.

For a later explicitly authorized live session, `make treatment-start` sets
the existing ECS service to one worker. `make treatment-stop` sets it to zero,
and `make treatment-status` shows running and pending task counts. Stopping
is asynchronous; inspect status until running and pending counts reach zero.
These targets use `AWS_PROFILE=thesis-project` and `AWS_REGION=ap-southeast-2`
unless overridden. They do not provision infrastructure. Terraform keeps its
default desired count at zero, so a subsequent apply restores that configured
value. None of these AWS commands is part of the local test target.
