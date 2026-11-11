# Thesis results and evidence summary

**Evidence cut-off:** 2026-11-11

**Purpose:** Give the thesis Results and Limitations sections a concise record of what the implementation and demonstration establish. Keep verified outcomes separate from intended but blocked behavior.

## Executive result

The DRIFT prototype demonstrated the GitOps application state, event-ingestion path, workflow orchestration, and safe escalation when evidence retrieval failed. It did **not** demonstrate successful Bedrock-based classification, treatment proposal generation, or draft pull-request creation in a live end-to-end run. The deterministic baseline was evaluated offline against synthetic development fixtures; those metrics are not estimates of real-world incident performance.

## Evidence matrix

| Area | Evidence observed | What it supports | What it does not support |
| --- | --- | --- | --- |
| GitOps application | Argo CD reported `online-boutique` as `Synced` and `Healthy`; application pods were running. | The demonstration environment could deploy and report the target application state. | Continuous availability or reliability outside the observed demo window. |
| Event and workflow integration | The demo exercised Argo CD notification, collector ingestion, workflow ingestion, and golden-manifest verification. | The upstream event path and workflow entry points were integrated. | Successful evidence retrieval or treatment completion. |
| Treatment execution | A Step Functions execution ended `SUCCEEDED`, while its result was `ESCALATE` with `evidence lookup failed: EvidenceRetrievalError`. | The workflow reached classification handling and recorded a safe escalation on retrieval failure. | Successful model classification, a treatment proposal, a GitOps PR, or a remediation applied to the cluster. |
| Bedrock embedding call | A direct Titan Text Embeddings V2 request returned a 1,024-dimension embedding. | The account could invoke this embedding model directly at that time. | Knowledge Base retrieval, generation-model quota, or the treatment task role’s authorization. |
| Deterministic baseline | The 60-record synthetic dev split had 60/60 classified records, class accuracy 0.500, class macro-F1 0.389, severity accuracy 0.400, and severity macro-F1 0.323. | A reproducible development diagnostic for the current deterministic rules. | Held-out performance, observed-incident accuracy, or production effectiveness. |
| Automated checks | A local combined test run reported 62 passed; merged implementation PRs through #38 had successful CI checks. | The tested code paths passed the recorded automated checks. | Cloud integration success or full system acceptance. |

Baseline metrics and their confusion matrices are recorded in [`experiments/results/baseline-dev.md`](../experiments/results/baseline-dev.md) and [`experiments/results/baseline-dev.json`](../experiments/results/baseline-dev.json). The experiment scope and limitations are described in [`experiments/baseline-scope.md`](../experiments/baseline-scope.md).

## Thesis-ready results wording

> The live demonstration validated the GitOps application state and exercised the event-ingestion and workflow-orchestration path. The treatment execution terminated safely with an escalation after Knowledge Base evidence retrieval failed. Therefore, the demonstration establishes integration and failure handling, but not successful model-based remediation: no treatment proposal or draft pull request was produced. Separately, the deterministic baseline classified all 60 synthetic development records. Its class accuracy was 0.500 and severity accuracy was 0.400. These values describe performance on the development fixtures only and should not be interpreted as accuracy on observed incidents.

## Limitations and current blockers

1. **Organizations policy:** `bedrock:Retrieve` for the treatment task role evaluated as `explicitDeny`, with `AllowedByOrganizations: false`. The retrieval failure must be resolved by the organization management account owner or AWS Support before a successful treatment run can be claimed.
2. **Model generation availability:** Direct embedding availability does not establish an available generation model or usable inference quota. Verify the selected generation model and make a small direct request after the access and quota issue is resolved.
3. **Terraform backend lock:** Access to the S3 Terraform lock-file object was denied by an Organizations policy. Keep Terraform locking enabled; do not use `-lock=false` to bypass this control.
4. **No live infrastructure at the evidence cut-off:** Root and platform Terraform states had no managed resources after teardown. The managed Knowledge Base and its data source must be recreated and the corpus re-ingested before retrieval can be checked again.
5. **Evaluation scope:** The baseline results use a synthetic development split, not a held-out test set or observed incident corpus. Do not tune against or report these values as external validity evidence.
6. **Cost attribution:** Cost Explorer reported `-0.0000000240 USD` UnblendedCost for 2026-11-09 through 2026-11-11. This is an account-wide date-range observation, not a project or per-session cost estimate.

The operational state and rebuild procedure are maintained in the [project completion checklist](project-completion-checklist.md) and [end-to-end runbook](end-to-end-runbook.md).

## Evidence required for a successful end-to-end claim

After the AWS policy blockers are resolved and a demonstration rebuild is approved:

1. Verify the locked Terraform backend can read and write its state and lock object, and inspect a fresh plan before applying it.
2. Verify the treatment role is allowed to call `bedrock:Retrieve` on the recreated Knowledge Base and that the selected generation model has usable quota.
3. Recreate the Knowledge Base, re-ingest the corpus, and verify retrieval before sending a drift event.
4. Run one controlled event and preserve its event ID, execution history, classification result, proposal-validation result, audit record, and draft PR checks.
5. Report the exact terminal result. A Step Functions `SUCCEEDED` status with an `ESCALATE` classification is safe workflow completion, not successful treatment.
6. Keep the draft PR subject to human review. Do not merge it or apply a patch automatically as part of the evaluation.

## Reproducibility references

- [`docs/end-to-end-runbook.md`](end-to-end-runbook.md): deployed demo steps, observed escalation, and rebuild constraints.
- [`docs/project-completion-checklist.md`](project-completion-checklist.md): merged stories, remaining access gates, teardown and retained assets.
- [`experiments/eval_baseline.py`](../experiments/eval_baseline.py): baseline evaluation implementation.
- [`infra/baseline/src/test_rules.py`](../infra/baseline/src/test_rules.py): representative rule behavior and conservative escalation cases.
- [`infra/orchestrator/src/test_treatment_boundaries.py`](../infra/orchestrator/src/test_treatment_boundaries.py): treatment safety boundaries.
