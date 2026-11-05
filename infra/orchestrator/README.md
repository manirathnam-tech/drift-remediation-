# DRIFT treatment core

This package contains the deterministic contract layer and provider adapters
for the Sprint 4 classification and patch-proposal flow. The Bedrock adapter
uses a runtime-configured model ID; AWS calls occur only when a caller
constructs and invokes it. The package never writes to a cluster or applies a
Git patch.

## Boundaries

- `prompts/` contains versioned prompts. `contracts.prompt_provenance()` records
  the prompt SHA-256 and configured model ID for each model response.
- `schemas/` defines strict JSON contracts for classification and GitOps patches.
- `src/contracts.py` checks those contracts, the approved taxonomy, safe GitOps
  paths, and `git apply --check` against the exact GitOps checkout. It never
  applies the patch.
- `src/retry.py` allows one initial generation and at most two validator-guided
  retries. It returns each attempt, verdict, and reason for a caller to persist.
- `src/treatment.py` coordinates desired-state lookup, evidence retrieval,
  classification, patch retries, exact-revision checks, and audit callbacks.
  Provider adapters and the rendered-manifest patch gate are required inputs;
  patches are checked but never applied.
- `src/audit_store.py` appends each treatment-stage record to a separate
  DynamoDB table keyed by event and unique record ID. The ECS task role should
  receive `dynamodb:PutItem` on this table only when that runtime is provisioned.
- `src/golden_store.py` retrieves the S3 artifact for a full Git SHA and checks
  the rendered manifest and source snapshot checksums, commit metadata,
  repository identity, encoding, and size before returning either artifact.
- `src/bedrock_model.py` provides classifier and patch-generator adapters using
  Bedrock Converse. It requires a model ID at runtime, fixes temperature at
  zero, checks the output shape later through the contracts, and redacts common
  credential fields before sending resource state. It supplies the output schema
  in the system prompt. ADR-0005 selects Nova Lite for the initial runtime.
- `src/queue_worker.py` long-polls the SNS-fanned treatment queue, gives a
  processing message a 15-minute visibility window, and deletes it only after
  a validated proposal or audited escalation. Failed messages are retried by
  SQS up to three receives before they move to the treatment dead-letter queue.
- `src/worker_app.py` composes the SQS, Bedrock, golden-manifest, audit, and
  validator adapters. It requires `TREATMENT_KNOWLEDGE_BASE_ID` and uses the
  bounded Bedrock Knowledge Base retriever selected by ADR-0003.
- `Dockerfile` packages the worker with pinned Kubernetes manifest validation
  tools. Terraform defines an ECS Fargate service with desired count zero by
  default. Before starting it, ingest the knowledge-base corpus, publish
  an immutable image, and set the image tag. No worker task runs
  while the desired count is zero.
- `../validator/policy/kubernetes.rego` contains independent Conftest deny rules.

Decision logic mirrors `docs/taxonomy.md`: benign intentional drift can be
accepted into Git; ordinary unintended drift is reverted to Git; critical,
malicious, uncertain, or low-confidence cases are escalated. Nothing here has
cluster-write permissions.

See `docs/adr/` for the accepted implementation decisions. The worker uses
static validation, a Bedrock managed Knowledge Base through its managed search
API, and Nova Lite in Sydney. Its IAM role allows retrieval and inference on
the configured resources only.
Server-side Kubernetes dry-run remains a separate operator review step.
