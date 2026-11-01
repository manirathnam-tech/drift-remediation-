# ADR-0002: Keep cluster validation outside the treatment worker
- Status: Accepted for this implementation
- Date: 2026-10-31
- Deciders: Project owner (implementation choice delegated to Codex)

## Context

The worker proposes a patch against a revision-addressed GitOps snapshot. It
must check the patch without changing the live cluster. Kubernetes server-side
dry-run is useful for API admission checks, but the API authorizes dry-run
requests using the same permissions as a real write. Giving the ECS task those
permissions would break the project's no-cluster-write boundary.
The [Kubernetes API concepts documentation](https://kubernetes.io/docs/reference/using-api/api-concepts/)
states that dry-run requests use the same authorization checks as normal
requests; see also the [Kubernetes RBAC guide](https://kubernetes.io/docs/reference/access-authn-authz/rbac/).

## Decision

The worker runs local patch checks, Kustomize rendering, kubeconform, Conftest,
and Checkov. The worker receives no EKS credentials, Kubernetes client, or
server-side dry-run option. A supervisor may run server-side dry-run as a
separate, manual review step using their workstation credentials. That result
must be recorded with the review before any later Git change.

## Alternatives considered

- Give the ECS worker a Kubernetes identity for server-side dry-run. Rejected:
  dry-run requires the same API authorization as a write and cannot be
  distinguished by Kubernetes RBAC.
- Add a validator broker with a separately constrained identity. Deferred:
  this adds a new service and an authorization boundary before the static
  validation path has been evaluated.

## Consequences

- Automated results mean static validation passed; they do not claim that live
  cluster admission would accept the patch.
- The manual review step can check admission behavior without giving the model
  worker cluster access.
- Any future automated server-side check requires a dedicated broker design,
  review, and a new decision record.

## Action items

- Remove the runtime server-side-validation switch and keep the validator's
  optional CLI mode available for a human-operated check.
- State this limit in the treatment runbook and audit output.
