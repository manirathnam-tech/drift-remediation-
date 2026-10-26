#!/usr/bin/env python3
"""Create a deterministic, balanced synthetic drift corpus and integrity manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
KINDS = ("Deployment", "Service", "Role", "RoleBinding", "NetworkPolicy")
SEVERITIES = (0, 1, 2, 3)
SEED_DEFAULT = 20261022
NAMESPACE = "online-boutique"


def obj(kind: str, name: str, spec: dict, api_version: str = "v1") -> dict:
    if kind == "Deployment":
        api_version = "apps/v1"
    elif kind == "NetworkPolicy":
        api_version = "networking.k8s.io/v1"
    elif kind in ("Role", "RoleBinding"):
        api_version = "rbac.authorization.k8s.io/v1"
    return {"apiVersion": api_version, "kind": kind,
            "metadata": {"name": name, "namespace": NAMESPACE, "labels": {"app": name}, "annotations": {}},
            "spec": spec}


def base_for(kind: str, i: int) -> dict:
    if kind == "Deployment":
        name = ("frontend", "cartservice", "currencyservice")[i % 3]
        return obj(kind, name, {"replicas": 1, "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": {"app": name}}, "spec": {
                "containers": [{"name": name, "image": "example.invalid/" + name + ":v1",
                    "securityContext": {"allowPrivilegeEscalation": False, "runAsNonRoot": True,
                                         "seccompProfile": {"type": "RuntimeDefault"}},
                    "resources": {"requests": {"cpu": "100m", "memory": "64Mi"},
                                  "limits": {"cpu": "250m", "memory": "128Mi"}}}]}}})
    if kind == "Service":
        name = ("frontend-external", "cartservice", "productcatalogservice")[i % 3]
        return obj(kind, name, {"type": "ClusterIP", "selector": {"app": name},
            "ports": [{"name": "http", "port": 80, "targetPort": 8080}]})
    if kind == "Role":
        return obj(kind, "drift-observer", {"rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]}]})
    if kind == "RoleBinding":
        return obj(kind, "drift-observer", {"roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "drift-observer"},
            "subjects": [{"kind": "ServiceAccount", "name": "drift-observer", "namespace": NAMESPACE}]})
    return obj(kind, ("allow-callers-to-productcatalogservice", "allow-cartservice-to-redis-cart")[i % 2],
        {"podSelector": {"matchLabels": {"app": "productcatalogservice"}},
         "policyTypes": ["Ingress"], "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": "frontend"}}}],
         "ports": [{"protocol": "TCP", "port": 3550}]}]})


def change_for(kind: str, severity: int, variant: int) -> tuple[str, str, object]:
    """Return a meaningful object path, operation and value for this stratum."""
    if severity == 0:
        return "/metadata/annotations/experiment.drift~1note", "add", f"approved-note-{variant % 7}"
    if kind == "Deployment":
        if severity == 1:
            return "/spec/template/spec/containers/0/image", "replace", f"example.invalid/workload:v{variant % 4 + 2}"
        if severity == 2:
            return "/spec/replicas", "replace", 0
        return "/spec/template/spec/containers/0/securityContext/privileged", "add", True
    if kind == "Service":
        if severity == 1:
            return "/spec/ports/0/name", "replace", f"http-{variant % 5}"
        if severity == 2:
            return "/spec/ports/0/targetPort", "replace", 18080
        return "/spec/type", "replace", "LoadBalancer"
    if kind == "Role":
        if severity == 1:
            return "/spec/rules/0/verbs", "replace", ["get"]
        if severity == 2:
            return "/spec/rules/0/verbs", "replace", []
        return "/spec/rules/-", "add", {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list"]}
    if kind == "RoleBinding":
        if severity == 1:
            return "/spec/subjects/0/name", "replace", f"observer-{variant % 5}"
        if severity == 2:
            return "/spec/subjects/0/namespace", "replace", "unrelated-namespace"
        return "/spec/subjects/-", "add", {"kind": "Group", "name": "system:authenticated"}
    if severity == 1:
        return "/spec/ingress/0/ports/0/port", "replace", 3551 + variant % 4
    if severity == 2:
        return "/spec/ingress/0/from", "replace", []
    return "/spec/ingress/0/from", "replace", [{}]


def remediation(label: str, intent: str) -> str:
    if label == "critical" or intent == "malicious":
        return "revert + escalate"
    if label == "benign" and intent == "intentional-benign":
        return "accept-into-Git"
    return "revert"


def make_records(count: int, seed: int) -> list[dict]:
    if count < 300 or count % 20:
        raise ValueError("count must be at least 300 and divisible by 20 for exact severity × kind balance and 20% dev split")
    cell_count = count // (len(KINDS) * len(SEVERITIES))
    if cell_count % 5:
        raise ValueError("count must yield a whole 20% development split in every severity-kind stratum")
    rng = random.Random(seed)
    records: list[dict] = []
    index = 0
    for severity in SEVERITIES:
        label = "benign" if severity == 0 else "critical" if severity == 3 else "operational"
        for kind in KINDS:
            cell: list[dict] = []
            variants = list(range(cell_count))
            rng.shuffle(variants)
            dev_count = cell_count // 5
            dev_indices = set(rng.sample(range(cell_count), dev_count))
            for local_i, variant in enumerate(variants):
                index += 1
                base = base_for(kind, variant)
                path, op, value = change_for(kind, severity, variant)
                mutation = {"op": op, "path": path}
                if op != "remove":
                    mutation["value"] = value
                intent = ("intentional-benign" if local_i % 2 == 0 else "unintended") if severity == 0 else "malicious" if severity == 3 else "unintended"
                intent_remediation = remediation(label, intent)
                cell.append({
                    "schema_version": "1.0.0", "id": f"SYN-{seed}-{index:04d}",
                    "class": label, "severity": severity, "intent": intent,
                    "kind": kind, "namespace": NAMESPACE,
                    "name": base["metadata"]["name"], "mutation": [mutation],
                    "expected_remediation": intent_remediation,
                    "scenario_id": f"{label.upper()}-S{severity}-{kind.upper()}-{variant % 5 + 1:02d}",
                    "synthetic": True, "seed": seed,
                    "split": "dev" if local_i in dev_indices else "test",
                    "base_object": base,
                    "rationale": rationale(kind, severity),
                    "evidence_required": evidence(kind, severity),
                    "source_trace": trace(kind, severity)
                })
            records.extend(cell)
    rng.shuffle(records)
    return records


def rationale(kind: str, severity: int) -> str:
    if severity == 0:
        return "Synthetic metadata-only annotation; evaluate as benign only when no effective behavior or trust-boundary effect is present."
    if severity == 1:
        return "Synthetic configuration change with no assumed outage; classify as operational severity 1 pending measured effect."
    if severity == 2:
        return "Synthetic mutation representing a pre-registered availability or service-function failure; verify the objective impact threshold."
    return "Synthetic mutation that weakens an effective security boundary or expands privilege/reachability; critical regardless of observed exploitation."


def evidence(kind: str, severity: int) -> list[str]:
    shared = ["record baseline and mutated object hashes", "capture Kubernetes audit/event timestamps"]
    if kind == "Deployment":
        shared += ["record readiness and restart status", "run frontend/service probes"]
    elif kind == "Service":
        shared += ["record Service type and endpoint slices", "test approved reachability boundary"]
    elif kind in ("Role", "RoleBinding"):
        shared += ["compare effective permissions with baseline", "record subject, namespace and authorization review"]
    else:
        shared += ["run before/after network connectivity policy tests", "record allowed source and destination tuples"]
    if severity == 2:
        shared += ["capture three consecutive failed checks at five-second intervals"]
    if severity == 3:
        shared += ["capture policy evaluation showing effective trust-boundary change"]
    return shared


def trace(kind: str, severity: int) -> list[str]:
    refs = ["project taxonomy v1.0.0: explicit decision rules; supervisor review pending"]
    if kind in ("Deployment", "Role", "RoleBinding", "NetworkPolicy") and severity == 3:
        refs.append("Rahman et al. 2023: Kubernetes manifest security misconfigurations; maps to this synthetic scenario, not empirical label proof")
    if severity in (1, 2) or kind in ("Service", "NetworkPolicy"):
        refs.append("Barletta et al. 2024: Kubernetes availability/service-networking failure categories; motivates impact checks")
    if severity == 0:
        refs.append("Hassan et al. 2024: desired/observed configuration comparison method; project benign threshold is an operational definition")
    return refs


def apply_patch_value(document: dict, patch: dict) -> dict:
    """Validate/apply the generator's JSON Patch subset (add/replace, objects/arrays)."""
    result = deepcopy(document)
    tokens = [t.replace("~1", "/").replace("~0", "~") for t in patch["path"].lstrip("/").split("/")]
    parent = result
    for token in tokens[:-1]:
        parent = parent[int(token)] if isinstance(parent, list) else parent[token]
    key = tokens[-1]
    if patch["op"] == "add":
        if isinstance(parent, list):
            if key == "-": parent.append(deepcopy(patch["value"]))
            else: parent.insert(int(key), deepcopy(patch["value"]))
        else:
            parent[key] = deepcopy(patch["value"])
    elif patch["op"] == "replace":
        if isinstance(parent, list): parent[int(key)] = deepcopy(patch["value"])
        else:
            if key not in parent: raise ValueError(f"replace path does not exist: {patch['path']}")
            parent[key] = deepcopy(patch["value"])
    elif patch["op"] == "remove":
        if isinstance(parent, list): del parent[int(key)]
        else: del parent[key]
    return result


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n" for r in records), encoding="utf-8", newline="\n")


def write_report(path: Path, records: list[dict], seed: int) -> None:
    by_severity = Counter(str(r["severity"]) for r in records)
    by_kind = Counter(r["kind"] for r in records)
    by_class = Counter(r["class"] for r in records)
    by_intent = Counter(r["intent"] for r in records)
    by_split = Counter(r["split"] for r in records)
    by_cell = Counter((r["severity"], r["kind"]) for r in records)
    lines = ["# Synthetic corpus balance report", "", f"Seed: `{seed}`", f"Records: **{len(records)}**", "", "> Generated mutation fixtures only. These are not observed production incidents, and the taxonomy has not yet received supervisor approval. Do not present this count as an empirical corpus.", "", "## Counts", "", "| Dimension | Counts |", "|---|---|", f"| Class | {dict(sorted(by_class.items()))} |", f"| Severity | {dict(sorted(by_severity.items()))} |", f"| Resource kind | {dict(sorted(by_kind.items()))} |", f"| Intent | {dict(sorted(by_intent.items()))} |", f"| Split | {dict(sorted(by_split.items()))} |", "", "## Severity × kind cross-balance", "", "| Severity | " + " | ".join(KINDS) + " |", "|---:|" + "---:|" * len(KINDS)]
    for sev in SEVERITIES:
        lines.append("| " + str(sev) + " | " + " | ".join(str(by_cell[(sev, k)]) for k in KINDS) + " |")
    lines += ["", "The default generator creates exactly 15 examples in each severity × kind cell (75 per severity; 60 per kind), with a stratified 20% development and 80% test split within every cell.", ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300, help="record count; minimum 300, divisible by 100")
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.count < 300 or args.count % 100:
        parser.error("--count must be at least 300 and divisible by 100 to retain exact 20% stratification in each of 20 cells")
    records = make_records(args.count, args.seed)
    validator = Draft202012Validator(json.loads((ROOT / "schema.json").read_text(encoding="utf-8")))
    errors = []
    for i, record in enumerate(records):
        errors.extend((i, e) for e in validator.iter_errors(record))
        try:
            apply_patch_value(record["base_object"], record["mutation"][0])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SystemExit(f"Invalid mutation for {record['id']}: {exc}") from exc
        if record["base_object"]["kind"] != record["kind"] or record["base_object"]["metadata"]["name"] != record["name"]:
            raise SystemExit(f"Target identity does not match base object for {record['id']}")
    if errors:
        i, error = errors[0]
        raise SystemExit(f"Schema validation failed for record {i}: {error.message}")
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    all_path, dev_path, test_path = out / "corpus.jsonl", out / "dev.jsonl", out / "test.jsonl"
    write_jsonl(all_path, records)
    write_jsonl(dev_path, [r for r in records if r["split"] == "dev"])
    write_jsonl(test_path, [r for r in records if r["split"] == "test"])
    report = out / "balance_report.md"
    write_report(report, records, args.seed)
    files = {p.name: sha256(p) for p in (ROOT / "schema.json", ROOT / "generate_corpus.py", all_path, dev_path, test_path, report)}
    manifest = {"schema_version": "1.0.0", "seed": args.seed, "record_count": len(records),
        "synthetic": True,
        "split": {"dev": len([r for r in records if r["split"] == "dev"]), "test": len([r for r in records if r["split"] == "test"])},
        "stratified_by": ["severity", "kind"], "supervisor_review": "pending",
        "usage_note": "Synthetic fixtures, not observed incidents. Prompt tuning may use dev only; test is held out.",
        "sha256": files}
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    sums_path = out / "SHA256SUMS"
    sums_path.write_text("".join(f"{digest}  {name}\n" for name, digest in sorted({**files, manifest_path.name: sha256(manifest_path)}.items())), encoding="utf-8", newline="\n")
    print(f"Generated and schema-validated {len(records)} synthetic records in {out}")
    print(f"Split: dev={manifest['split']['dev']}, test={manifest['split']['test']}; fixed seed={args.seed}")
    print("Every JSON Patch was checked against its included base object; review the synthetic-data warning and supervisor status before research claims.")


if __name__ == "__main__":
    main()
