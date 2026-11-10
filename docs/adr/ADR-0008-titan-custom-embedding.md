# ADR-0008: Use Titan custom embeddings for the managed Knowledge Base

- Status: Accepted
- Date: 2026-11-09
- Deciders: Project owner

## Context

AWS Support enabled Amazon Bedrock service access for the account. In
`ap-southeast-2`, `amazon.titan-embed-text-v2:0` reports agreement, authorization,
entitlement, and regional availability as available. A direct invocation using a
short text returned a 1024-dimensional embedding. `cohere.embed-english-v3`
remains unavailable because its agreement status is `NOT_AVAILABLE`.

The Terraform-managed Knowledge Base uses the managed storage and S3 connector,
but it must use a customer-selected embedding model to use the newly verified
Titan access. The account's AWS infrastructure has been torn down; this decision
updates configuration only and does not authorize provisioning.

## Decision

Keep the Bedrock managed Knowledge Base and managed S3 connector. Configure the
Knowledge Base to use Titan Text Embeddings V2 as a custom embedding model with
1024-dimensional float32 vectors. Grant its service role `bedrock:InvokeModel`
only for the Titan model ARN, along with the model-list permissions Bedrock
requires, and retain its scoped S3 corpus read permissions.

Remove the now-unused S3 Vectors bucket and index from Terraform. The managed
Knowledge Base owns its retrieval storage; these separate resources are not part
of its data path.

## Consequences

- The selected model was directly invoked successfully, but ingestion still
  needs to be verified after infrastructure is deliberately recreated.
- Switching embedding type requires replacing the Knowledge Base. This is safe
  for the next deployment because the previous Terraform-managed infrastructure
  has been destroyed.
- Custom embedding uses the account's model access and quota. Ingestion can
  still be throttled even when a direct short invocation succeeds.
- The custom embedding choice does not use the managed reranker.

## Validation

- `GetFoundationModelAvailability` reports all four availability fields as
  available for Titan Text Embeddings V2 in Sydney.
- A direct `InvokeModel` call succeeded and returned 1024 dimensions.
- Terraform validation and a future reviewed plan are required before
  provisioning the updated configuration.
