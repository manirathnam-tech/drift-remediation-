# Drift taxonomy and severity rubric

**Status:** Approved by supervisor
**Schema version:** `1.0.0`  
**Review date:** Not recorded
**Scope:** Kubernetes workload and namespaced configuration drift in the Online Boutique experiment.

> This is a project-specific operational rubric informed by the studies below. The cited studies do not jointly prescribe these exact labels or thresholds. The supervisor reviewed and approved this rubric; the approval date was not recorded.

## 1. Unit of labelling and decision order

A labelled instance is one controlled change from a recorded Git baseline to one live Kubernetes object or one tightly coupled set of objects. Record the baseline and live-manifest hashes, the exact JSONPath/resource fields changed, and the observed effect. Split a scenario into separate instances when independent changes could receive different labels. Assign the highest-impact applicable class and severity when a change affects multiple resources.

Apply these decisions in order:

1. **Critical / severity 3:** Does the diff create or materially increase an unsafe security capability or cross a trust boundary? Examples: effective RBAC privilege escalation, privileged execution or removal of a required security control, newly public service exposure, effective network isolation removal, or dangerous hostPath access. If yes, label `critical` / `3`.
2. **Operational / severity 2:** Otherwise, does a reproducible check show user-visible outage, failed readiness, resource exhaustion, or a material service-networking failure? If yes, label `operational` / `2`.
3. **Operational / severity 1:** Otherwise, does the diff change runtime behavior, image, resource allocation, environment/configuration, or workload scale beyond the benign conditions below? If yes, label `operational` / `1`.
4. **Benign / severity 0:** Only if the change is in the approved benign set and evidence confirms it caused no security-boundary change, user-visible failure, or policy/SLO violation. Otherwise do not use benign.

A textual diff alone is insufficient for effects that depend on effective policy or reachability. Use Kubernetes object evaluation, policy tests, endpoint/readiness probes, or a controlled test where applicable. If required evidence is missing, the instance is not ready for ground-truth adjudication; do not guess its label.

## 2. Taxonomy, severity and default response

| Class | Severity | Objective decision rule | Examples | Default expected remediation |
|---|---:|---|---|---|
| `benign` | `0` (none) | No effective change to runtime security, externally reachable surface, service correctness, or an agreed availability/SLO guardrail. Change must be within a documented experiment allowlist. | Approved annotation/label metadata edit with no selector or policy effect; replica count changed within the pre-approved range (2–3) with all required probes passing and no SLO breach. | `accept-into-Git` only for an authorized `intentional-benign` change. If unintended, `revert` to Git. |
| `operational` | `1` (low) or `2` (high) | Changes workload behavior or service operation but does not meet a critical security-boundary rule. Severity 2 requires observed user-visible impact or objective threshold breach; otherwise severity 1. | Trusted image tag/digest change; resource request/limit change; ConfigMap/environment change; scale outside the approved benign band; failed readiness/liveness or endpoint check. | `revert` to desired Git state. A human may instead approve and commit an intended change to Git; the automated expected action remains revert. |
| `critical` | `3` (critical) | Creates or weakens an effective security boundary or grants a capability that violates the baseline policy, irrespective of whether exploitation is observed. | RBAC grants new effective permissions beyond the Role/RoleBinding baseline (especially wildcard, secret access, privilege escalation, cluster scope); removal/weakening of required `securityContext`; public `NodePort`/`LoadBalancer` or public ingress where not approved; deletion/weakening of a NetworkPolicy that expands effective flows; dangerous `hostPath`, especially writable host paths. | `revert + escalate` to a human reviewer/security owner. Never auto-accept into Git. |

### Exact operational thresholds

- **Benign replica changes:** allowed only within the experiment allowlist in `docs/taxonomy.md` (initially 2–3 replicas), with all required probes passing and no pre-registered availability threshold breached. Outside that band is operational. A replica edit that leaves a required service unavailable is operational severity 2.
- **Operational severity 2:** at least one pre-registered user-facing endpoint or required readiness check fails on three consecutive observations at five-second intervals, or a pre-registered service/networking check shows a material failure. Record probe command, timestamps and results. Do not retroactively choose thresholds after observing a result.
- **Security severity 3:** effective permissions, routes, capabilities, mounts, or service exposure must be compared with the baseline and policy. A field change that is ineffective (for example, a policy deletion with no widened allowed flows) is not critical solely because the object was deleted; record it as operational/benign based on its effects and rationale.
- **Service exposure:** classify critical when the change makes the service reachable from a broader, unapproved trust zone (for example, private/ClusterIP to public LoadBalancer/NodePort or public ingress). A new service object that remains private and authorized is not automatically critical.
- **SecurityContext:** classify critical when a required control is removed or weakened, such as enabling `privileged`, allowing privilege escalation, adding dangerous capabilities, removing required non-root/seccomp restrictions, or enabling host namespaces. Harmless formatting or an unchanged effective value does not qualify.
- **HostPath:** classify critical when a workload newly mounts a host path outside an explicitly approved read-only allowlist. Writable paths, container-runtime sockets, host credentials, and sensitive node paths are critical by default.

## 3. Intent label

Intent is a separate ground-truth field. It is assigned from the experiment record, authorization/audit trail, and scenario design, not inferred from the manifest diff alone.

- `intentional-benign`: a planned, authorized change whose expected impact satisfies the benign criteria.
- `unintended`: an accidental or unauthorized non-malicious change, including an experiment mutation explicitly designated as accidental.
- `malicious`: a deliberately hostile change intended to bypass policy, gain privilege, expose a service/data, disrupt operation, or conceal unauthorized behavior.

An ambiguous or undocumented intent must be adjudicated before the record enters the scored dataset. Do not label a change malicious merely because it is dangerous; danger determines class/severity, while intent requires provenance. The labeler must save the scenario specification or audit evidence supporting intent.

For binary false-positive analysis, pre-register the target: a detection is a **true positive** when the ground truth requires action (`unintended` or `malicious`, or any critical condition); a detection of a `benign` + `intentional-benign` event is a candidate **false positive** if the system recommends corrective action. Report class/severity and intent metrics separately; do not collapse them into one score.

## 4. Expected remediation decision table

| Condition | Expected remediation | Human action |
|---|---|---|
| `intent=malicious` (any class/severity) | `revert + escalate` | Preserve evidence; notify the designated reviewer; do not auto-merge. |
| `class=critical` | `revert + escalate` | Review the security finding and confirm the baseline is restored. |
| `class=benign` and `intent=intentional-benign` | `accept-into-Git` | Verify the change is authorized and inside the allowlist, then commit it to the desired-state repository. |
| `class=benign` and `intent=unintended` | `revert` | Restore the Git-defined state. |
| `class=operational` and `intent=unintended` | `revert` | Restore the Git-defined state and record any service impact. |
| `class=operational` and `intent=intentional-benign` | Not a valid combination | Re-adjudicate: either the effect meets benign criteria, or the intent label is unsupported by this schema. |
| `class=operational` and `intent=malicious` | `revert + escalate` | Preserve evidence and escalate even if the observed impact is currently bounded. |

`revert` means restore the approved Git baseline through the GitOps path; the LLM does not write directly to the cluster. `accept-into-Git` means a human-authorized desired-state change is committed through the normal review process. `revert + escalate` means restore baseline and route for human/security review.

## 5. Label schema

The original 12-record pilot generated by `scripts/generate_taxonomy_pilot.py` uses the pilot field names below and produces synthetic examples only. DRIFT-20's versioned experiment-record schema is `harness/schema.json`; its records use `id`, `class`, and JSON Patch fields. Neither the pilot nor the generated DRIFT-20 fixtures are observed incidents or approved ground truth.

| Field | Meaning |
|---|---|
| `schema_version` | Version of this label schema. |
| `instance_id` | Unique generated identifier in the DRIFT-19 pilot. DRIFT-20 uses `id`. |
| `scenario_id` | Stable identifier for the scenario template. |
| `synthetic` | `true` for generator examples; never count these as observed corpus instances. |
| `seed` | Random seed used for this output. |
| `drift_class` | `benign`, `operational`, or `critical` in the pilot. DRIFT-20 uses `class`. |
| `severity` | Integer `0`–`3` using the thresholds above. |
| `intent` | `intentional-benign`, `unintended`, or `malicious`. |
| `expected_remediation` | `revert`, `accept-into-Git`, or `revert + escalate`. |
| `change_summary` | Human-readable example. |
| `rationale` | Rule that supports the label. |
| `evidence_required` | Evidence to capture for a real trial. |
| `source_trace` | Literature connection; not a claim that the paper defined this exact label. |
| `split` | Synthetic generator records use `pilot`; they are not dev/train/test evaluation data. |

The DRIFT-20 synthetic generator includes `kind`, `namespace`, `name`, `mutation`, and `base_object` so each JSON Patch can be reproduced against its target fixture. For real records, extend the schema with `baseline_commit`, `baseline_sha256`, `live_sha256`, `resource_api_version`, `changed_paths`, `observed_effect`, `injection_method`, `event_timestamp_utc`, `adjudicator_ids`, and `adjudication_notes`. Store evidence without credentials or secret values. The scenario author should not be the sole adjudicator of scored examples.

## 6. Literature traceability and limits

| Rubric element | Source | How it informs this rubric | Boundary of the claim |
|---|---|---|---|
| Security misconfigurations and critical controls | Rahman et al. (2023) | Empirical Kubernetes-manifest categories include absent resource limits/securityContext, host namespace activation, capability misuse, Docker socket mounting, escalated child-process privileges, and privileged securityContext. These support testing the listed security-sensitive drift targets. | The paper studies misconfigurations in manifests; it does not define our drift intent labels or severity levels. NetworkPolicy/RBAC rules in this rubric are project policy and should be supported by the project's effective-policy tests. |
| Operational impact and failure thresholds | Barletta et al. (2024) | Their Kubernetes failure analysis distinguishes system-wide failure, service networking issues, and under/over-provisioning. This motivates separate operational impact labels and objective observations instead of judging only by changed YAML. | Their fault injection targets the cluster state store; our workload-manifest drift scenarios are different. The numeric thresholds here are study-specific. |
| State comparison, evidence and adjudication | Hassan et al. (2024) | Their IaC state-reconciliation study defines reconciliation in terms of discovering/communicating with inventory, comparing desired and provided state, and provisioning; its qualitative coding/reporting method informs our baseline-vs-live comparison and evidence fields. | Their study concerns Ansible/IaC state-reconciliation defects, not Kubernetes drift severity or this taxonomy. Its results are methodological context only. |

### References

1. Akond Rahman, Shazibul Islam Shamim, Dibyendu Brinto Bose, and Rahul Pandita (2023). “Security Misconfigurations in Open Source Kubernetes Manifests: An Empirical Study.” *ACM Transactions on Software Engineering and Methodology*, 32(4), Article 99, 1–36. [DOI: 10.1145/3579639](https://doi.org/10.1145/3579639); [author-hosted paper](https://akondrahman.github.io/files/papers/tosem-k8s.pdf).
2. Marco Barletta, Marcello Cinque, Catello Di Martino, Zbigniew T. Kalbarczyk, and Ravishankar K. Iyer (2024). “Mutiny! How Does Kubernetes Fail, and What Can We Do about It?” *54th Annual IEEE/IFIP International Conference on Dependable Systems and Networks (DSN)*, 1–14. [DOI: 10.1109/DSN58291.2024.00016](https://doi.org/10.1109/DSN58291.2024.00016); [conference paper](https://dsn2024uq.github.io/Proceedings/pdfs/DSN2024-6rvE3SSpzFYmysif75Dkid/410500a001/410500a001.pdf).
3. Md. Mahadi Hassan, John Salvador, Shubhra Kanti Karmaker Santu, and Akond Rahman (2024). “State Reconciliation Defects in Infrastructure as Code.” *Proceedings of the ACM on Software Engineering*, 1(FSE), Article 83, 1–24. [DOI: 10.1145/3660790](https://doi.org/10.1145/3660790); [author-hosted paper](https://akondrahman.github.io/files/papers/fse2024-iac.pdf).

## 7. Pilot and corpus-size guardrail

The generator's default output is a **12-record synthetic pilot** for checking schema consistency and label coverage. It is not evidence that 300 real instances are feasible, and it is not part of the scored corpus. Keep 300 as the proposed corpus target while measuring injection, reset, adjudication and validation throughput. Do not reduce the target without supervisor approval. If the approved target changes, record the approval/date/reason and report the achieved real corpus size, exclusions and incomplete trials in the thesis.

Before scoring real data, run a small pilot, have two reviewers independently label a sample, resolve disagreements using the rules above, and report inter-rater agreement. The supervisor approved this rubric. Record the approval date and any requested revisions when available; the approval status is reflected here.

**Supervisor review:** Approved
**Review date:** Not recorded
**Approved version:** `1.0.0`

The versioned corpus files were generated before this approval and retain their original generation metadata and hashes. This approval records the rubric review; it does not claim that synthetic fixtures are observed or supervisor-adjudicated incidents.

### Generate the synthetic pilot

From the `drift-remediation-` repository root, run:

```bash
python3 scripts/generate_taxonomy_pilot.py
```

This writes 12 synthetic JSONL examples to `data/pilot/taxonomy-pilot.jsonl` with seed `20261022`. To reproduce a different fixed sample, pass the same `--seed` and `--count`; to choose a different output path, pass `--out PATH`. Counts above 12 repeat scenario templates and do not increase the number of distinct scenarios.
