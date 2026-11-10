"""Score deterministic baseline rules against synthetic development records."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra" / "baseline" / "src"))

from eval_classify import _load_dev
from rules import classify_event

CLASS_LABELS = ("benign", "operational", "critical")
SEVERITY_LABELS = (0, 1, 2, 3)
REVISION = "0" * 40
TIMESTAMP = "2026-11-10T00:00:00Z"


def _pointer_parts(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("JSON Patch path must be an absolute JSON Pointer")
    parts = path[1:].split("/")
    decoded = []
    for part in parts:
        if re.search(r"~(?![01])", part):
            raise ValueError("JSON Pointer contains an invalid escape")
        decoded.append(part.replace("~1", "/").replace("~0", "~"))
    return decoded


def _array_index(token: str, length: int, *, allow_end: bool) -> int:
    if token == "-" and allow_end:
        return length
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise ValueError("JSON Pointer array index is invalid")
    index = int(token)
    upper_bound = length if allow_end else length - 1
    if index < 0 or index > upper_bound:
        raise ValueError("JSON Pointer array index is out of range")
    return index


def _parent(document: Any, parts: list[str]) -> tuple[Any, str]:
    if not parts:
        raise ValueError("root-object replacement is not supported by the corpus")
    current = document
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            current = current[_array_index(part, len(current), allow_end=False)]
        else:
            raise ValueError("JSON Pointer parent does not exist")
    if not isinstance(current, (dict, list)):
        raise TypeError("JSON Pointer parent is not a container")
    return current, parts[-1]


def _apply_mutation(
    document: dict[str, Any], mutation: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply the corpus schema's add/remove/replace JSON Patch operations."""
    result = copy.deepcopy(document)
    for operation in mutation:
        op = operation.get("op")
        parent, token = _parent(result, _pointer_parts(operation.get("path")))
        if op == "add":
            value = copy.deepcopy(operation["value"])
            if isinstance(parent, dict):
                parent[token] = value
            elif isinstance(parent, list):
                parent.insert(_array_index(token, len(parent), allow_end=True), value)
            else:
                raise ValueError("JSON Patch add target is not a container")
        elif op == "replace":
            value = copy.deepcopy(operation["value"])
            if isinstance(parent, dict) and token in parent:
                parent[token] = value
            elif isinstance(parent, list):
                parent[_array_index(token, len(parent), allow_end=False)] = value
            else:
                raise ValueError("JSON Patch replace target does not exist")
        elif op == "remove":
            if isinstance(parent, dict) and token in parent:
                del parent[token]
            elif isinstance(parent, list):
                del parent[_array_index(token, len(parent), allow_end=False)]
            else:
                raise ValueError("JSON Patch remove target does not exist")
        else:
            raise ValueError("unsupported corpus JSON Patch operation")
    return result


def _event(record: dict[str, Any]) -> dict[str, Any]:
    live_state = _apply_mutation(record["base_object"], record["mutation"])
    event_id = hashlib.sha256(record["id"].encode("utf-8")).hexdigest()
    return {
        "event_type": "configuration_drift_detected",
        "event_id": event_id,
        "application": {"name": "online-boutique", "namespace": "online-boutique"},
        "revision": REVISION,
        "t_detect": TIMESTAMP,
        "received_at": TIMESTAMP,
        "reconciled_at": TIMESTAMP,
        "resources": [
            {
                "kind": record["kind"],
                "name": record["name"],
                "namespace": record["namespace"],
                "status": "OutOfSync",
                "live_state": live_state,
                "target_state": record["base_object"],
            }
        ],
    }


def _confusion(
    labels: tuple[str, ...] | tuple[int, ...],
    gold: list[Any],
    predicted: list[Any],
) -> dict[str, dict[str, int]]:
    matrix = {str(actual): {str(choice): 0 for choice in labels} for actual in labels}
    for actual, choice in zip(gold, predicted, strict=True):
        matrix[str(actual)][str(choice)] += 1
    return matrix


def _macro_f1(
    labels: tuple[str, ...] | tuple[int, ...], gold: list[Any], predicted: list[Any]
) -> float | None:
    if not gold:
        return None
    scores = []
    for label in labels:
        true_positive = sum(
            a == label and p == label for a, p in zip(gold, predicted, strict=True)
        )
        false_positive = sum(
            a != label and p == label for a, p in zip(gold, predicted, strict=True)
        )
        false_negative = sum(
            a == label and p != label for a, p in zip(gold, predicted, strict=True)
        )
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append(2 * true_positive / denominator if denominator else 0.0)
    return sum(scores) / len(scores)


def evaluate(dev: dict[str, dict[str, Any]]) -> dict[str, Any]:
    predictions = {}
    errors = []
    rule_counts: Counter[str] = Counter()
    for instance_id, record in sorted(dev.items()):
        try:
            result = classify_event(_event(record))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"id": instance_id, "error": str(exc)})
            continue
        predictions[instance_id] = result
        for resource in result["resources"]:
            rule_counts.update(finding["rule_id"] for finding in resource["findings"])

    ids = sorted(predictions)
    record_results = []
    for instance_id in ids:
        record = dev[instance_id]
        result = predictions[instance_id]
        findings = [
            {
                "kind": resource["kind"],
                "name": resource["name"],
                "path": finding["path"],
                "rule_id": finding["rule_id"],
                "severity": finding["severity"],
            }
            for resource in result["resources"]
            for finding in resource["findings"]
        ]
        record_results.append(
            {
                "id": instance_id,
                "scenario_id": record["scenario_id"],
                "kind": record["kind"],
                "name": record["name"],
                "gold_class": record["class"],
                "predicted_class": result["drift_class"],
                "gold_severity": record["severity"],
                "predicted_severity": result["severity"],
                "gold_intent_context": record["intent"],
                "gold_expected_remediation_context": record["expected_remediation"],
                "predicted_review_required": result["review_required"],
                "rule_findings": findings,
            }
        )
    gold_class = [dev[instance_id]["class"] for instance_id in ids]
    predicted_class = [predictions[instance_id]["drift_class"] for instance_id in ids]
    gold_severity = [dev[instance_id]["severity"] for instance_id in ids]
    predicted_severity = [predictions[instance_id]["severity"] for instance_id in ids]
    return {
        "synthetic_dev_records": len(dev),
        "classified_records": len(predictions),
        "coverage": len(predictions) / len(dev) if dev else 0.0,
        "classification_errors": errors,
        "record_results": record_results,
        "class_accuracy_on_classified": (
            sum(a == b for a, b in zip(gold_class, predicted_class, strict=True)) / len(ids)
            if ids else None
        ),
        "class_macro_f1_on_classified": _macro_f1(
            CLASS_LABELS, gold_class, predicted_class
        ),
        "class_confusion_matrix": _confusion(CLASS_LABELS, gold_class, predicted_class),
        "severity_accuracy_on_classified": (
            sum(a == b for a, b in zip(gold_severity, predicted_severity, strict=True)) / len(ids)
            if ids else None
        ),
        "severity_macro_f1_on_classified": _macro_f1(
            SEVERITY_LABELS, gold_severity, predicted_severity
        ),
        "severity_confusion_matrix": _confusion(SEVERITY_LABELS, gold_severity, predicted_severity),
        "rule_finding_counts": dict(sorted(rule_counts.items())),
        "intent_metric": "not measured; the deterministic baseline does not infer intent",
        "remediation_metric": "not scored; intent and authorization evidence are external to this rule output",
    }


def _markdown(result: dict[str, Any], dev_path: Path) -> str:
    return "\n".join(
        [
            "# Deterministic baseline development evaluation",
            "",
            f"Dataset: `{dev_path}`. This is a synthetic development split, not observed incident performance.",
            "",
            (
                f"- Records classified: {result['classified_records']} / "
                f"{result['synthetic_dev_records']} (coverage {result['coverage']:.3f})"
            ),
            f"- Class accuracy (classified records): {_format(result['class_accuracy_on_classified'])}",
            f"- Class macro-F1: {_format(result['class_macro_f1_on_classified'])}",
            (
                f"- Severity accuracy (classified records): "
                f"{_format(result['severity_accuracy_on_classified'])}"
            ),
            f"- Severity macro-F1: {_format(result['severity_macro_f1_on_classified'])}",
            "- Intent and remediation decisions are not scored because the baseline does not infer experiment authorization or intent.",
            "",
            "## Class confusion matrix (rows = gold, columns = predicted)",
            "",
            "```json",
            json.dumps(result["class_confusion_matrix"], indent=2),
            "```",
            "",
            "## Severity confusion matrix (rows = gold, columns = predicted)",
            "",
            "```json",
            json.dumps(result["severity_confusion_matrix"], indent=2),
            "```",
            "",
            (
                "These metrics describe this ruleset against synthetic dev fixtures only. "
                "They are not empirical deployment accuracy and must not be reported as "
                "performance on observed incidents."
            ),
            "",
        ]
    )


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", type=Path, default=ROOT / "harness" / "dev.jsonl")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "experiments" / "results" / "baseline-dev.md",
    )
    args = parser.parse_args()
    dev = _load_dev(args.dev)
    result = evaluate(dev)
    report = _markdown(result, args.dev)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
