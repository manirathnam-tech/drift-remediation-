"""Generate dev-only classification outputs from Bedrock for the local scorer."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_SRC = ROOT / "infra" / "orchestrator" / "src"
sys.path.insert(0, str(ORCHESTRATOR_SRC))

from bedrock_model import BedrockJSONModel, make_classifier
from errors import TreatmentDependencyError
from evidence_retrieval import make_evidence_retriever

DEV_DATA = ROOT / "harness" / "dev.jsonl"
MODEL_IDS = {
    "nova-lite": "amazon.nova-lite-v1:0",
    "claude-haiku-4-5-au": "au.anthropic.claude-haiku-4-5-20251001-v1:0",
}


def _read_dev(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("split") != "dev":
                raise ValueError(f"{path}:{line_number}: only split=dev records are accepted")
            if not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError(f"{path}:{line_number}: record id is required")
            rows.append(row)
    if not rows:
        raise ValueError(f"development corpus is empty: {path}")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("development corpus contains duplicate record IDs")
    return rows


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must start with '/'")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _apply_mutations(base: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    live: Any = copy.deepcopy(base)
    for operation in operations:
        action = operation.get("op")
        parts = _pointer_parts(operation.get("path", ""))
        if action not in {"add", "replace", "remove"} or not parts:
            raise ValueError("only non-root add, replace, and remove mutations are supported")
        parent = live
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        leaf = parts[-1]
        if isinstance(parent, list):
            index = len(parent) if leaf == "-" else int(leaf)
            if action == "add":
                parent.insert(index, copy.deepcopy(operation["value"]))
            elif action == "replace":
                parent[index] = copy.deepcopy(operation["value"])
            else:
                parent.pop(index)
        elif action == "remove":
            parent.pop(leaf)
        else:
            parent[leaf] = copy.deepcopy(operation["value"])
    if not isinstance(live, dict):
        raise TypeError("mutated Kubernetes object must remain a JSON object")
    return live


def _event(row: dict[str, Any]) -> dict[str, Any]:
    base = row.get("base_object")
    if not isinstance(base, dict):
        raise TypeError(f"{row['id']}: base_object is required")
    live = _apply_mutations(base, row.get("mutation", []))
    kind = row.get("kind") or base.get("kind")
    name = row.get("name") or base.get("metadata", {}).get("name")
    namespace = row.get("namespace") or base.get("metadata", {}).get("namespace", "default")
    return {
        "event_id": row["id"],
        "event_type": "configuration_drift_detected",
        "application": {"name": "online-boutique", "namespace": "argocd"},
        "revision": "0" * 40,
        "t_detect": "2026-01-01T00:00:00Z",
        "reconciled_at": "2026-01-01T00:00:00Z",
        "resources": [
            {
                "group": "apps" if kind in {"Deployment", "StatefulSet", "DaemonSet"} else "",
                "version": "v1",
                "kind": kind,
                "namespace": namespace,
                "name": name,
                "status": "OutOfSync",
                "target_state": base,
                "live_state": live,
            }
        ],
    }


def _run_model(model_name: str, rows: list[dict[str, Any]], output: Path) -> None:
    model_id = MODEL_IDS[model_name]
    classifier = make_classifier(BedrockJSONModel(model_id=model_id))
    retrieve = make_evidence_retriever()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as destination:
        for index, row in enumerate(rows, start=1):
            event = _event(row)
            golden = {"revision": event["revision"], "sha256": "0" * 64}
            raw_output: dict[str, Any]
            try:
                evidence = retrieve(event, golden)
                raw_output = classifier(event, golden, evidence)
            except (TreatmentDependencyError, ValueError) as exc:
                raw_output = {"evaluation_error": type(exc).__name__}
            destination.write(
                json.dumps(
                    {"id": row["id"], "model_id": model_id, "raw_output": raw_output},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            print(f"{model_name}: {index}/{len(rows)} {row['id']}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEV_DATA)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments" / "results")
    parser.add_argument(
        "--models",
        default="nova-lite",
        help="comma-separated model keys: nova-lite,claude-haiku-4-5-au",
    )
    parser.add_argument(
        "--confirm-model-calls",
        action="store_true",
        help="required because Bedrock model calls and knowledge-base retrieval can incur charges",
    )
    args = parser.parse_args()
    if not args.confirm_model_calls:
        parser.error("pass --confirm-model-calls to start paid Bedrock requests")
    selected = [value.strip() for value in args.models.split(",") if value.strip()]
    if not selected or any(name not in MODEL_IDS for name in selected):
        parser.error(f"models must be selected from: {', '.join(MODEL_IDS)}")
    rows = _read_dev(args.data)
    for model_name in selected:
        output = args.output_dir / f"{model_name}-dev-predictions.jsonl"
        _run_model(model_name, rows, output)
        print(f"Saved outputs: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
