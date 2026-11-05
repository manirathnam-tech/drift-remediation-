# ADR-0003: Use Bedrock Knowledge Bases with S3 Vectors for retrieval
- Status: Superseded by [ADR-0006](ADR-0006-managed-knowledge-base.md)
- Date: 2026-10-31
- Deciders: Project owner (implementation choice delegated to Codex)

## Context

Classification needs a small, traceable set of project rules and operational
evidence. The workload is intermittent and the project does not need a
continuously provisioned search cluster. Retrieval must return source excerpts
that can be cited in the treatment audit.
AWS describes [S3 Vectors as a cost-effective choice for infrequent RAG
workloads](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html)
that does not need provisioned vector infrastructure. The role policy follows
the [Bedrock Knowledge Bases IAM guidance](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-permissions.html).

## Decision

Use an Amazon Bedrock Knowledge Base backed by an S3 Vectors index in
`ap-southeast-2`, with Titan Text Embeddings V2 at 1024 dimensions. Store the
curated project knowledge in the existing private, versioned golden-manifest
bucket under `knowledge-base/`. The worker uses `bedrock-agent-runtime:Retrieve`
with a small top-K limit and fails closed if retrieval fails or returns no
usable evidence. S3 data-source ingestion is an explicit setup/update action;
the worker does not mutate its own knowledge base.

## Alternatives considered

- OpenSearch Serverless: rejected for this low-volume, intermittent prototype
  because it has a provisioned capacity cost even while the worker is stopped.
- Static evidence embedded in the prompt: rejected because the evidence would
  not be separately retrievable, cited, or updated as a corpus.
- A custom vector database: rejected because it would add a service and
  operational work without improving this project's required behavior.

## Consequences

- Retrieval is managed by Bedrock and S3 Vectors; corpus ingestion is a
  documented operator action after infrastructure creation or corpus changes.
- Evidence snippets are bounded and carry stable IDs and S3 source locations.
- A missing/empty index leads to escalation instead of an unsupported answer.

## Action items

- Provision the vector bucket, index, knowledge base, role, and S3 data source
  with Terraform.
- Add a bounded retriever adapter and operator ingestion instructions.
