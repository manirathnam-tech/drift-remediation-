# Deterministic baseline scope inventory

## Result

The current synthetic development split contains 60 records. Every record's
`kind` and `name` pair appears in the deterministic baseline's
`ALLOWED_RESOURCES` map (60/60, 100%). The development-label distribution is:

| Label | Count |
| --- | ---: |
| Benign | 15 |
| Operational | 30 |
| Critical | 15 |
| Severity 0 / 1 / 2 / 3 | 15 each |
| Intentional-benign / unintended / malicious | 8 / 37 / 15 |
| Expected accept / revert / revert + escalate | 8 / 37 / 15 |

The resource identity coverage by kind is:

| Kind | Records | Supported resource identities |
| --- | ---: | ---: |
| Deployment | 12 | 12 |
| NetworkPolicy | 12 | 12 |
| Role | 12 | 12 |
| RoleBinding | 12 | 12 |
| Service | 12 | 12 |

The collector's normalized `DriftEvent v1` includes `live_state` and
`target_state`, which are the two snapshots consumed by the baseline rules.
This confirms that the current dev corpus is in scope at the resource identity
and event-contract levels.

## Limits

This is **not** a baseline classification score. It does not apply each
synthetic mutation, compare the resulting changed paths with the rule table,
evaluate `review_required`, or measure remediation correctness. The records
are synthetic development fixtures, not observed incidents or approved real
world performance evidence. Do not report 100% as baseline accuracy.

## Offline dev replay

The replay evaluator was run against `harness/dev.jsonl` on 2026-11-10. It
classified all 60 records and reported class accuracy `0.500`, class macro-F1
`0.389`, severity accuracy `0.400`, and severity macro-F1 `0.323`. The live
Kubernetes object structure revealed nested RBAC paths under `spec`; the rules
now distinguish added permissions/subjects from reductions. All 15 gold
critical examples were classified critical. Fifteen of 30 operational examples
were also classified critical: six NetworkPolicy changes, six RoleBinding
subject updates, and three Role permission expansions. Those scenarios need
policy and impact adjudication before the labels can establish whether these
are false positives. All 15 gold benign examples were classified operational,
not benign: three as severity 1 and twelve as severity 2. This shows that the
baseline lacks authorization and evidence context needed to identify benign
changes, and that its NetworkPolicy rule still treats every spec change as
critical.

The corpus labels for severity 2 describe measured impact requirements, but
these fixtures contain the manifest mutations rather than the observed probes
needed to establish that impact. Interpret the severity metrics as diagnostic
comparisons between the rule output and synthetic scenario labels, not as a
standalone measure of taxonomy compliance.

The full confusion matrices, per-record rule paths, rule-hit counts, and error list are in
[`results/baseline-dev.md`](results/baseline-dev.md) and
[`results/baseline-dev.json`](results/baseline-dev.json). These are
development-fixture metrics only. They are not results on the held-out test
split, observed production incidents, or a comparison against an LLM.

The baseline arm also does not infer intent from a manifest diff. In
particular, its rules cannot distinguish an authorized harmless change from an
unintended harmless change without an experiment authorization record. Report
that boundary separately from class and severity metrics.

## Reproduce the inventory

Run from the repository root. This reads only the dev JSONL and the baseline
allowlist; it makes no AWS calls and writes no output files.

```bash
python3 - <<'PY'
import collections
import json
from pathlib import Path

from infra.baseline.src.rules import ALLOWED_RESOURCES

rows = [
    json.loads(line)
    for line in Path("harness/dev.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
counts = collections.Counter((row["kind"], row["name"]) for row in rows)
supported = sum(
    count
    for (kind, name), count in counts.items()
    if name in ALLOWED_RESOURCES.get(kind, set())
)
print("dev_records={}".format(len(rows)))
print("supported_resource_identity={}/{}".format(supported, len(rows)))
for (kind, name), count in sorted(counts.items()):
    covered = name in ALLOWED_RESOURCES.get(kind, set())
    print("{} / {}: covered={} count={}".format(kind, name, covered, count))
PY
```

## Reproduce and extend

Reproduce with `python3 experiments/eval_baseline.py --out
experiments/results/baseline-dev.md`. The runner uses no AWS or model calls.
Keep the held-out test split out of development and rule tuning. Record class
and severity scores separately; intent and remediation limitations are
reported separately because the baseline does not infer authorization or
intent.
