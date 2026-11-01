"""Run static manifest checks and an explicitly requested server-side dry run."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

POLICY_DIR = Path(__file__).resolve().parents[1] / "policy"


def _run(label: str, command: list[str], timeout: int) -> tuple[str, str]:
    """Run one validator without a shell and report its output and verdict."""
    print(f"\n== {label} ==", flush=True)
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        print(f"Required executable not found: {command[0]}", file=sys.stderr)
        return "ERROR", f"Required executable not found: {command[0]}"
    except subprocess.TimeoutExpired:
        print(f"Timed out after {timeout}s: {label}", file=sys.stderr)
        return "ERROR", f"Timed out after {timeout}s"

    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        end = "" if result.stderr.endswith("\n") else "\n"
        print(result.stderr, file=sys.stderr, end=end)
    print(f"{label}: {'PASS' if result.returncode == 0 else 'FAIL'}")
    return ("PASS" if result.returncode == 0 else "FAIL",
            (result.stderr.strip() or result.stdout.strip())[:2000])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Rendered Kubernetes YAML to validate")
    parser.add_argument("--json-report", type=Path, help="write per-check verdicts and timing")
    parser.add_argument(
        "--server-side",
        action="store_true",
        help="also run kubectl server-side dry-run using the current kubeconfig/context",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="timeout in seconds for each validator (default: 120)",
    )
    args = parser.parse_args()

    manifest = args.manifest.expanduser().resolve()
    if args.timeout < 1:
        parser.error("--timeout must be at least 1")
    if not manifest.is_file():
        parser.error(f"manifest does not exist or is not a file: {manifest}")
    if manifest.stat().st_size == 0:
        parser.error("manifest is empty")

    commands = [
        (
            "kubeconform",
            ["kubeconform", "-strict", "-summary", str(manifest)],
        ),
        (
            "conftest",
            ["conftest", "test", "--policy", str(POLICY_DIR), str(manifest)],
        ),
        (
            "checkov",
            ["checkov", "--framework", "kubernetes", "--skip-download", "--file", str(manifest)],
        ),
    ]

    started = time.monotonic()
    checks = {"server_side": "SKIP"}
    reasons = {"server_side": "not requested"}
    passed = True
    for label, command in commands:
        verdict, detail = _run(label, command, args.timeout)
        checks[label] = verdict
        reasons[label] = detail
        passed = verdict == "PASS" and passed

    if args.server_side:
        verdict, detail = (
            _run(
                "kubectl server-side dry run",
                [
                    "kubectl",
                    "apply",
                    "--dry-run=server",
                    "--validate=strict",
                    "-f",
                    str(manifest),
                ],
                args.timeout,
            )
        )
        checks["server_side"] = verdict
        reasons["server_side"] = detail
        passed = verdict == "PASS" and passed

    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps({
            "checks": checks,
            "reasons": reasons,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "status": "PASS" if passed else "FAIL",
        }, indent=2) + "\n", encoding="utf-8")

    print(f"\nValidation result: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
