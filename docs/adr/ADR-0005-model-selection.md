# ADR-0005: Use Amazon Nova Lite as the default treatment model

- Status: Accepted for the initial evaluation; production choice remains
  measurement-gated
- Date: 2026-10-31
- Deciders: Project owner (implementation choice delegated to Codex)

## Context

The worker needs a JSON-capable model for classification and bounded patch
proposals. The evaluation corpus has a dev split, and the project should record
model and prompt provenance. Cost and keeping the normal request path in
Sydney matter; accuracy must be measured rather than assumed.

## Decision

AWS lists Nova Lite as a low-cost model with in-Region Sydney availability in
the [Nova Lite model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-lite.html).
The [Claude Haiku 4.5 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html)
documents the Australia geo profile used for comparison.

Use Amazon Nova Lite (`amazon.nova-lite-v1:0`) through Bedrock Converse as the
default runtime model in `ap-southeast-2`. Compare it on the dev split with
Claude Haiku 4.5 through the Australian geo inference profile
`au.anthropic.claude-haiku-4-5-20251001-v1:0`. Keep temperature at zero and
record the exact model ID and prompt checksum. The comparator is opt-in and
must not be enabled in the worker IAM role unless the project explicitly
chooses it after reviewing measured quality, cost, and residency needs.

## Alternatives considered

- Claude Haiku 4.5 as the default: retained as a comparator; the geo profile
  can route within Australia, but it is not a single-region-only endpoint.
- Select the production model by intuition: rejected; the development set is
  the pre-registered comparison split.

## Consequences

- The default uses the Sydney regional model ID and a narrow foundation-model
  invocation grant.
- The evaluation runner invokes models only when an operator runs it and only
  reads `dev.jsonl`; it never sends labels or held-out splits to the model.
- No model request is made by CI or Terraform.

## Action items

- Set the default runtime model ID and IAM ARN.
- Add a dev-only Bedrock inference runner that writes raw outputs with model
  and prompt provenance for the existing scorer.
