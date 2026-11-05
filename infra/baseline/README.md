# DRIFT baseline control arm

The baseline arm classifies collector events with ruleset `baseline-rules-v1`
in `src/rules.py`. Audit records preserve the collector detection, receipt, and
reconciliation timestamps for later arm comparison. Unknown fields receive severity 2 so missing rules do not
silently become benign. Replica-count changes are severity 1 because their
availability impact is not measured by this classifier. Pod and Service
annotations require review because controllers may use them to alter runtime
behavior, routing, or exposure. The Lambda writes classification and sync-request
records to the treatment audit table, then asks Argo CD to sync only the
resources named by the event at the recorded Git revision.
Audit record IDs are deterministic per event and stage, making repeated SQS
deliveries idempotent in the append-only table.

The sync request is written to the Application's top-level `operation` field.
The Lambda checks both the current operation and operation status before
submitting a new sync. Its security group permits HTTPS to the private VPC and
the regional DynamoDB prefix list used by the audit table.

Before requesting a sync, the handler checks that Argo CD still reports the
event revision and that the application remains OutOfSync. It does not replace
an active operation. Pruning is enabled only for the explicitly allowlisted
resources named in the event, so desired deletions can also be reverted. The
IAM principal is mapped to a Kubernetes group with `get` and `patch` access only to the
`online-boutique` Application in the `argocd` namespace.

Terraform creates a separate baseline queue and a disabled SQS event source
mapping. The queue is intentionally absent from the shared SNS fanout. Keep
`baseline_consumer_enabled = false` until DRIFT-51 provides mutually exclusive
arm selection for each trial. No AWS resources are created or changed by local
checks.

## Static checks

```sh
python3 -m py_compile infra/baseline/src/*.py
ruff check infra/baseline/src
python -m pytest -q infra/baseline/src/test_rules.py
terraform fmt -check -recursive infra
terraform -chdir=infra validate
```

The rule validation contains 20 representative event cases plus checks that
out-of-scope resources, non-OutOfSync resources, and timezone-free timestamps
are rejected.

Terraform validation requires `terraform init -backend=false` first in a clean
worktree.
