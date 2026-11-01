"""Score saved first-attempt validator results against development IDs only."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from eval_classify import _load_dev, _read_jsonl

CHECKS = ("git_apply", "kubeconform", "conftest", "checkov", "server_side")


def score(records: list[dict], dev: dict) -> dict:
    first = {}
    for record in records:
        instance_id = record.get("id")
        if instance_id not in dev:
            raise ValueError("validator results contain an unknown or non-dev ID")
        if record.get("attempt") != 1:
            continue
        if instance_id in first:
            raise ValueError("duplicate first-attempt result")
        checks = record.get("checks")
        if not isinstance(checks, dict):
            raise TypeError("checks must be a mapping of check names to verdicts")
        if any(value not in {"PASS", "FAIL", "SKIP", "ERROR"} for value in checks.values()):
            raise ValueError("unsupported validator verdict")
        first[instance_id] = record
    per_check = {}
    for check in CHECKS:
        values = [row["checks"].get(check, "SKIP") for row in first.values()]
        attempted = sum(value != "SKIP" for value in values)
        per_check[check] = {
            "attempted": attempted,
            "passed": values.count("PASS"),
            "skipped": values.count("SKIP"),
            "pass_rate": values.count("PASS") / attempted if attempted else None,
        }
    durations = [
        row["duration_ms"] for row in first.values()
        if isinstance(row.get("duration_ms"), (int, float))
        and not isinstance(row["duration_ms"], bool) and row["duration_ms"] >= 0
    ]
    static_passed = sum(
        all(row["checks"].get(check) == "PASS" for check in CHECKS[:-1])
        for row in first.values()
    )
    complete_passed = sum(
        all(row["checks"].get(check) == "PASS" for check in CHECKS)
        for row in first.values()
    )
    return {
        "dev_records": len(dev),
        "first_attempt_records": len(first),
        "coverage": len(first) / len(dev) if dev else 0,
        "per_check": per_check,
        "static_first_pass_rate": static_passed / len(first) if first else None,
        "full_gate_first_pass_rate": complete_passed / len(first) if first else None,
        "median_validation_ms": statistics.median(durations) if durations else None,
        "duration_records": len(durations),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", type=Path, default=root / "harness/dev.jsonl")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = score(_read_jsonl(args.records), _load_dev(args.dev))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
