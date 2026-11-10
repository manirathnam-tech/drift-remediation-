# ADR-0006: Use a managed Bedrock Knowledge Base

- Status: Superseded by [ADR-0008](ADR-0008-titan-custom-embedding.md)
- Date: 2026-11-05
- Deciders: Project owner (implementation choice delegated to Codex)

## Context

The treatment service needs a small, traceable collection of project rules.
The AWS account has already created and successfully ingested the corpus into
a Bedrock managed Knowledge Base, then retrieved the decision-rules document.
Direct access to the candidate customer-managed embedding models was not
confirmed for this account. Keeping the S3 Vectors/Titan configuration would
replace the working Knowledge Base and depend on unverified embedding access.

## Decision

Keep the existing Bedrock managed Knowledge Base and its managed S3 connector.
Use service-managed embeddings and call `Retrieve` with
`managedSearchConfiguration`. Store corpus Markdown in the private,
versioned golden-manifest S3 bucket under `knowledge-base/`. Do not invoke a
customer-selected embedding model for ingestion.

Keep the previously provisioned S3 Vectors bucket and index in Terraform state
while confirming that the managed Knowledge Base operates correctly. They are
not the active Knowledge Base's backing store and will not be deleted as part
of this configuration correction.

## Consequences

- Terraform configuration matches the existing Knowledge Base ID and connector
  instead of replacing them.
- The Knowledge Base role needs access to the S3 corpus but not permission to
  invoke Titan embeddings or access the unused S3 Vectors index.
- Ingestion remains an explicit operator action after corpus updates.
- The worker continues to fail closed when retrieval fails or returns no usable
  evidence.

## Follow-up

- Confirm a fresh Terraform plan contains no Knowledge Base or data-source
  replacement.
- Re-ingest the corpus after the infrastructure update and confirm managed
  retrieval from the treatment worker's runtime path.
- Inspect whether the unused S3 Vectors bucket contains data before considering
  its removal in a separate reviewed change.
