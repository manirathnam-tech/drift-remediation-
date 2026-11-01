"""Score saved classifier outputs using only the development corpus split."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra" / "orchestrator" / "src"))

# Keep the shared validator as the source of truth for evaluator input checks.
from contracts import validate_classification

CLASS_LABELS = ("benign", "operational", "critical")
SEVERITY_LABELS = (0, 1, 2, 3)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: each JSONL row must be an object")
            rows.append(value)
    return rows


def _load_dev(path: Path) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(path)
    if not rows:
        raise ValueError(f"development dataset is empty: {path}")

    dev: dict[str, dict[str, Any]] = {}
    for row in rows:
        instance_id = row.get("id")
        if row.get("split") != "dev":
            raise ValueError(
                f"refusing non-dev data: {instance_id or '<missing id>'} has split={row.get('split')!r}"
            )
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("every development record must have a non-empty id")
        if instance_id in dev:
            raise ValueError(f"duplicate development record id: {instance_id}")
        if row.get("class") not in CLASS_LABELS or row.get("severity") not in SEVERITY_LABELS:
            raise ValueError(f"unsupported gold labels for development record {instance_id}")
        dev[instance_id] = row
    return dev


def _parse_prediction(raw: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, [f"invalid JSON output: {exc.msg}"]
    if not isinstance(raw, dict):
        return None, ["model output must be a JSON object"]
    errors = validate_classification(raw)
    return (raw if not errors else None), errors


def _load_predictions(
    family: str,
    path: Path,
    dev: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    predictions: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for line_number, row in enumerate(_read_jsonl(path), start=1):
        instance_id = row.get("id")
        if not isinstance(instance_id, str) or not instance_id:
            errors.append(f"{family} line {line_number}: missing id; output is invalid")
            continue
        if instance_id not in dev:
            raise ValueError(
                f"{family} predictions include {instance_id}, which is not in the dev split; "
                "held-out or unknown records are refused"
            )
        if instance_id in predictions:
            raise ValueError(f"{family} predictions contain duplicate id {instance_id}")

        result, validation_errors = _parse_prediction(row.get("raw_output"))
        if result is None:
            errors.append(
                f"{family} {instance_id}: " + "; ".join(validation_errors)
            )
        else:
            predictions[instance_id] = result
    return predictions, errors


def _confusion(
    labels: tuple[str, ...] | tuple[int, ...],
    gold: list[Any],
    predicted: list[Any],
) -> dict[str, dict[str, int]]:
    matrix = {str(actual): {str(choice): 0 for choice in labels} for actual in labels}
    for actual, choice in zip(gold, predicted, strict=True):
        matrix[str(actual)][str(choice)] += 1
    return matrix


def _macro_f1(labels: tuple[str, ...] | tuple[int, ...], gold: list[Any], predicted: list[Any]) -> float | None:
    if not gold:
        return None
    scores = []
    for label in labels:
        true_positive = sum(actual == label and choice == label for actual, choice in zip(gold, predicted, strict=True))
        false_positive = sum(actual != label and choice == label for actual, choice in zip(gold, predicted, strict=True))
        false_negative = sum(actual == label and choice != label for actual, choice in zip(gold, predicted, strict=True))
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append(2 * true_positive / denominator if denominator else 0.0)
    return sum(scores) / len(scores)


def _score_family(
    family: str,
    dev: dict[str, dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
    invalid_outputs: list[str],
) -> dict[str, Any]:
    ids = sorted(predictions)
    gold_class = [dev[instance_id]["class"] for instance_id in ids]
    predicted_class = [predictions[instance_id]["drift_class"] for instance_id in ids]
    gold_severity = [dev[instance_id]["severity"] for instance_id in ids]
    predicted_severity = [predictions[instance_id]["severity"] for instance_id in ids]
    total_outputs = len(predictions) + len(invalid_outputs)

    return {
        "model_family": family,
        "dev_records": len(dev),
        "valid_predictions": len(predictions),
        "invalid_first_pass_outputs": len(invalid_outputs),
        "first_pass_schema_valid_rate": (
            len(predictions) / total_outputs if total_outputs else 0.0
        ),
        "dev_coverage": len(predictions) / len(dev),
        "class_accuracy": (
            sum(a == b for a, b in zip(gold_class, predicted_class, strict=True)) / len(ids)
            if ids
            else None
        ),
        "class_macro_f1": _macro_f1(CLASS_LABELS, gold_class, predicted_class),
        "severity_accuracy": (
            sum(a == b for a, b in zip(gold_severity, predicted_severity, strict=True)) / len(ids)
            if ids
            else None
        ),
        "severity_macro_f1": _macro_f1(SEVERITY_LABELS, gold_severity, predicted_severity),
        "class_confusion_matrix": _confusion(CLASS_LABELS, gold_class, predicted_class),
        "severity_confusion_matrix": _confusion(SEVERITY_LABELS, gold_severity, predicted_severity),
        "invalid_output_examples": invalid_outputs[:20],
    }


def _prediction_argument(value: str) -> tuple[str, Path]:
    family, separator, path = value.partition("=")
    if not separator or not family.strip() or not path.strip():
        raise argparse.ArgumentTypeError("prediction must be FAMILY=PATH")
    return family.strip(), Path(path).expanduser()


def _markdown(results: list[dict[str, Any]], dev_path: Path) -> str:
    lines = [
        "# Development-split classification evaluation",
        "",
        f"Dataset: `{dev_path}`. Only rows marked `split=dev` are accepted.",
        "",
        "| Model family | First-pass schema valid | Dev coverage | Class accuracy | Class macro-F1 | Severity accuracy | Severity macro-F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            "| {model_family} | {first_pass_schema_valid_rate:.3f} | {dev_coverage:.3f} | "
            "{class_accuracy} | {class_macro_f1} | {severity_accuracy} | {severity_macro_f1} |".format(
                model_family=result["model_family"],
                first_pass_schema_valid_rate=result["first_pass_schema_valid_rate"],
                dev_coverage=result["dev_coverage"],
                class_accuracy=_format_metric(result["class_accuracy"]),
                class_macro_f1=_format_metric(result["class_macro_f1"]),
                severity_accuracy=_format_metric(result["severity_accuracy"]),
                severity_macro_f1=_format_metric(result["severity_macro_f1"]),
            )
        )
    for result in results:
        lines.extend(
            [
                "",
                f"## {result['model_family']}",
                "",
                f"Valid predictions: {result['valid_predictions']} / {result['dev_records']}",
                f"Invalid first-pass outputs: {result['invalid_first_pass_outputs']}",
                "",
                "### Class confusion matrix (rows = gold; columns = predicted)",
                "",
                "```json",
                json.dumps(result["class_confusion_matrix"], indent=2),
                "```",
                "",
                "### Severity confusion matrix (rows = gold; columns = predicted)",
                "",
                "```json",
                json.dumps(result["severity_confusion_matrix"], indent=2),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def _format_metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dev",
        type=Path,
        default=ROOT / "harness" / "dev.jsonl",
        help="development JSONL; every row must declare split=dev",
    )
    parser.add_argument(
        "--prediction",
        action="append",
        type=_prediction_argument,
        required=True,
        metavar="FAMILY=PATH",
        help="JSONL model outputs; each row has id and raw_output",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "experiments" / "results" / "classification-dev.md",
        help="Markdown report path (JSON metrics are written beside it)",
    )
    args = parser.parse_args()

    try:
        dev = _load_dev(args.dev)
        families = [family for family, _ in args.prediction]
        if len(set(families)) != len(families):
            raise ValueError("each model family may be supplied only once")
        results = []
        for family, path in args.prediction:
            predictions, invalid_outputs = _load_predictions(family, path, dev)
            results.append(_score_family(family, dev, predictions, invalid_outputs))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    report = _markdown(results, args.dev)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    json_path = args.out.with_suffix(".json")
    json_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(report, end="")
    print(f"Wrote {args.out} and {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
