#!/usr/bin/env python3
"""Generate deterministic synthetic label records for the DRIFT-19 schema pilot."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

SCENARIOS = [
    {
        "scenario_id": "B-ANNOTATION",
        "drift_class": "benign",
        "severity": 0,
        "intent": "intentional-benign",
        "change_summary": "Add a non-functional annotation to a Deployment.",
        "rationale": "Metadata-only change; selector, runtime behavior, policy and service checks are unchanged.",
        "evidence_required": ["compare selectors and spec", "required endpoint probes pass"],
        "source_trace": ["Hassan2024: compare desired and observed state; project benign threshold"],
    },
    {
        "scenario_id": "B-REPLICAS-IN-BAND",
        "drift_class": "benign",
        "severity": 0,
        "intent": "intentional-benign",
        "change_summary": "Increase replicas from 2 to 3 within the approved experiment range.",
        "rationale": "Within allowlist; all required probes pass and no pre-registered SLO threshold is breached.",
        "evidence_required": ["replica count", "readiness", "endpoint probes", "SLO observation"],
        "source_trace": ["Barletta2024: under/over-provisioning impact motivates measuring service effect"],
    },
    {
        "scenario_id": "B-ANNOTATION-UNINTENDED",
        "drift_class": "benign",
        "severity": 0,
        "intent": "unintended",
        "change_summary": "Accidentally add an unused annotation to a Service.",
        "rationale": "No effective behavior/security change; audit record identifies an accidental edit.",
        "evidence_required": ["field-level diff", "audit/provenance record", "endpoint probes"],
        "source_trace": ["Hassan2024: compare desired and observed state; project benign threshold"],
    },
    {
        "scenario_id": "O-IMAGE-TAG",
        "drift_class": "operational",
        "severity": 1,
        "intent": "unintended",
        "change_summary": "Change a workload image tag to a different image from the trusted registry.",
        "rationale": "Runtime image changes; no security boundary or measured outage is established in this example.",
        "evidence_required": ["old/new image reference and digest", "pod rollout status", "application probes"],
        "source_trace": ["Barletta2024: operational effects must be observed rather than inferred from syntax"],
    },
    {
        "scenario_id": "O-RESOURCE-LIMIT",
        "drift_class": "operational",
        "severity": 1,
        "intent": "unintended",
        "change_summary": "Reduce a container memory limit without triggering a measured service failure.",
        "rationale": "Changes resource allocation; absent resource limits are a Kubernetes security/configuration concern, but severity depends on effect.",
        "evidence_required": ["old/new requests and limits", "OOM/restart metrics", "readiness and endpoint probes"],
        "source_trace": ["Rahman2023: absent resource limits is a studied manifest misconfiguration; Barletta2024: provisioning impact"],
    },
    {
        "scenario_id": "O-CONFIGMAP",
        "drift_class": "operational",
        "severity": 1,
        "intent": "unintended",
        "change_summary": "Change an application ConfigMap value used by a service.",
        "rationale": "Runtime configuration changed; no critical boundary or threshold breach is established in this example.",
        "evidence_required": ["changed key names (not secret values)", "rollout status", "application probes"],
        "source_trace": ["Hassan2024: desired-versus-observed state comparison; project runtime-impact rule"],
    },
    {
        "scenario_id": "O-REPLICAS-OUT-OF-BAND",
        "drift_class": "operational",
        "severity": 1,
        "intent": "unintended",
        "change_summary": "Change replicas outside the approved 2–3 replica experiment band while probes still pass.",
        "rationale": "Scale is outside the explicit benign allowlist but no measured user-visible outage is shown.",
        "evidence_required": ["replica count", "approved range", "readiness and endpoint probes"],
        "source_trace": ["Barletta2024: under/over-provisioning is a distinct service failure effect"],
    },
    {
        "scenario_id": "O-ENDPOINT-FAILURE",
        "drift_class": "operational",
        "severity": 2,
        "intent": "unintended",
        "change_summary": "A configuration change causes the required frontend endpoint to fail three consecutive five-second probes.",
        "rationale": "Meets the pre-registered user-visible failure threshold; no security-boundary change is present.",
        "evidence_required": ["probe command and timestamps", "events/logs", "failed endpoint result"],
        "source_trace": ["Barletta2024: service networking and availability failure categories"],
    },
    {
        "scenario_id": "C-RBAC-ESCALATION",
        "drift_class": "critical",
        "severity": 3,
        "intent": "malicious",
        "change_summary": "Add an effective RoleBinding that grants a workload broader permissions than the baseline, including Secret reads.",
        "rationale": "Effective authorization exceeds the reviewed least-privilege baseline; exploit evidence is not required.",
        "evidence_required": ["Role/RoleBinding diff", "effective permission comparison", "subject and namespace"],
        "source_trace": ["Rahman2023: Kubernetes security misconfiguration evidence; project effective-RBAC rule"],
    },
    {
        "scenario_id": "C-PRIVILEGED-CONTEXT",
        "drift_class": "critical",
        "severity": 3,
        "intent": "malicious",
        "change_summary": "Set privileged=true or remove a mandatory securityContext restriction from a workload.",
        "rationale": "Weakens an enforced workload isolation/security control.",
        "evidence_required": ["pod/container securityContext diff", "policy evaluation", "admission result"],
        "source_trace": ["Rahman2023: privileged securityContext, absent securityContext and capability misuse"],
    },
    {
        "scenario_id": "C-PUBLIC-SERVICE",
        "drift_class": "critical",
        "severity": 3,
        "intent": "malicious",
        "change_summary": "Change an unapproved private Service to a public LoadBalancer or NodePort.",
        "rationale": "Expands reachability across the approved trust boundary.",
        "evidence_required": ["Service type/annotations diff", "cloud load-balancer address/scheme", "reachability test"],
        "source_trace": ["Rahman2023: security-focused configuration analysis; Barletta2024: service-networking impact"],
    },
    {
        "scenario_id": "C-NETWORKPOLICY-HOSTPATH",
        "drift_class": "critical",
        "severity": 3,
        "intent": "malicious",
        "change_summary": "Delete an effective isolation NetworkPolicy or add a writable sensitive hostPath mount.",
        "rationale": "Classify critical only when effective traffic expands or host access crosses the approved boundary.",
        "evidence_required": ["before/after effective policy test", "volume mount path and readOnly value", "policy/admission evidence"],
        "source_trace": ["Rahman2023: security misconfiguration categories; project effective-network and host-mount rules"],
    },
]


def expected_remediation(case: dict[str, object]) -> str:
    if case["intent"] == "malicious" or case["drift_class"] == "critical":
        return "revert + escalate"
    if case["drift_class"] == "benign" and case["intent"] == "intentional-benign":
        return "accept-into-Git"
    return "revert"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=12, help="number of synthetic records (default: 12)")
    parser.add_argument("--seed", type=int, default=20261022, help="deterministic random seed")
    parser.add_argument("--out", type=Path, default=Path("data/pilot/taxonomy-pilot.jsonl"))
    args = parser.parse_args()
    if not 1 <= args.count <= 300:
        parser.error("--count must be between 1 and 300; 300 is a proposed target, not a claim of feasibility")

    rng = random.Random(args.seed)
    cases = [dict(case) for case in SCENARIOS]
    rng.shuffle(cases)
    records = []
    for index in range(args.count):
        case = dict(cases[index % len(cases)])
        case.update(
            {
                "schema_version": "1.0.0",
                "instance_id": f"SYN-{args.seed}-{index + 1:04d}",
                "synthetic": True,
                "seed": args.seed,
                "expected_remediation": expected_remediation(case),
                "split": "pilot",
            }
        )
        records.append(case)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"Wrote {len(records)} synthetic records to {args.out} (seed={args.seed}).")
    print("These examples are not observed corpus instances and must not be scored as experiment data.")


if __name__ == "__main__":
    main()
