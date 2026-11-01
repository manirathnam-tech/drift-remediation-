"""Validate saved dev proposals locally; never request model calls or publish patches."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from eval_classify import _load_dev, _read_jsonl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra/validator/src"))

from contracts import validate_proposal
from validate_patch import validate_rendered_snapshot


def validate_rows(rows: list[dict], dev: dict, archive: bytes, revision: str):
    """Refuse unknown IDs before running any validation and retain every attempt."""
    seen = set()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be a full lowercase Git SHA")
    for row in rows:
        instance_id = row.get("id")
        attempt = row.get("attempt")
        if not isinstance(instance_id, str) or instance_id not in dev:
            raise ValueError("proposal contains an unknown or non-dev ID")
        if type(attempt) is not int or attempt not in (1, 2, 3):
            raise ValueError("attempt must be 1, 2, or 3")
        key = (instance_id, attempt)
        if key in seen:
            raise ValueError("duplicate proposal attempt")
        seen.add(key)

    for row in rows:
        proposal = row.get("proposal")
        errors = validate_proposal(proposal) if isinstance(proposal, dict) else ["proposal must be an object"]
        report = {"checks": {"proposal": "FAIL"}, "status": "FAIL", "reasons": errors}
        if not errors and row.get("revision") != revision:
            errors.append("proposal revision does not match the supplied Git snapshot")
        if not errors and proposal.get("decision") == "escalate":
            report.update(status="SKIP", reasons=["escalation has no patch to validate"])
            report["checks"]["proposal"] = "PASS"
        elif not errors:
            validate_rendered_snapshot(
                proposal["patch"], archive, revision, hashlib.sha256(archive).hexdigest(),
                report=report,
            )
            report["checks"]["proposal"] = "PASS"
        yield {"id": row["id"], "attempt": row["attempt"], "revision": revision, **report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", type=Path, default=ROOT / "harness/dev.jsonl")
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--gitops-checkout", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    dev = _load_dev(args.dev)
    rows = _read_jsonl(args.proposals)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", args.revision], cwd=args.gitops_checkout,
        capture_output=True, check=True, timeout=180,
    ).stdout
    results = validate_rows(rows, dev, archive, args.revision)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as output:
        for result in results:
            output.write(json.dumps(result) + "\n")
            output.flush()
            print(f"{result['id']} attempt={result['attempt']} status={result['status']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
