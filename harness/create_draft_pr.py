#!/usr/bin/env python3
"""Interactively create a draft GitOps PR from a routed treatment proposal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Any

from audit_store import make_audit_writer
from github_app_pr import create_draft_pr
from workflow_state import load_checkpoint, load_event


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _load_routed_proposal(
    event_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    state_key = f"workflow-runs/state/{digest}.json"
    checkpoint, _ = load_checkpoint(state_key)
    if checkpoint.get("event_id") != event_id or checkpoint.get("stage") != "ROUTED":
        raise ValueError("event is not at the routed review stage")
    input_key = checkpoint.get("input_key")
    input_sha256 = checkpoint.get("input_sha256")
    if not isinstance(input_key, str) or not isinstance(input_sha256, str):
        raise TypeError("workflow checkpoint has invalid input references")
    event = load_event(input_key, input_sha256)
    proposal = checkpoint.get("proposal")
    if not isinstance(proposal, dict):
        raise TypeError("routed workflow checkpoint has no proposal")
    validator_report = checkpoint.get("validation_report")
    if not isinstance(validator_report, dict):
        raise TypeError("routed workflow checkpoint has no validator report")
    if validator_report.get("status") != "PASS":
        raise ValueError("routed proposal does not have a passing validator report")
    return event, proposal, validator_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review a validated DRIFT proposal and open a draft GitHub PR."
    )
    parser.add_argument("--event-id", required=True)
    parser.add_argument(
        "--approval-reference",
        required=True,
        help="Reference to the supervisor/operator approval (for example, a ticket or meeting note).",
    )
    args = parser.parse_args(argv)

    if len(args.event_id) > 256 or not args.event_id.strip():
        parser.error("--event-id must be a non-empty event identifier of at most 256 characters")
    if not args.approval_reference.strip() or len(args.approval_reference) > 200:
        parser.error("--approval-reference must contain 1 to 200 characters")

    os.environ.setdefault("AWS_REGION_NAME", os.environ.get("AWS_REGION", "ap-southeast-2"))
    _required_env("GITHUB_APP_ID")
    _required_env("GITHUB_APP_INSTALLATION_ID")
    _required_env("GITHUB_APP_PRIVATE_KEY_SECRET_ARN")
    _required_env("GOLDEN_MANIFESTS_BUCKET")
    _required_env("TREATMENT_AUDIT_TABLE")

    event, proposal, validator_report = _load_routed_proposal(args.event_id)
    patch_sha = hashlib.sha256(proposal["patch"].encode("utf-8")).hexdigest()
    print(f"Application: {event['application']['namespace']}/{event['application']['name']}")
    print(f"Revision: {event['revision']}")
    print(f"Decision: {proposal['decision']}")
    print(f"Severity: {proposal['classification']['severity']}")
    print(f"Summary: {proposal['summary']}")
    print(f"Justification: {proposal['classification']['justification']}")
    print(f"Files: {', '.join(proposal['files'])}")
    print(f"Patch SHA-256: {patch_sha}")
    print("Validator checks:")
    for check, verdict in sorted(validator_report.get("checks", {}).items()):
        print(f"  {check}: {verdict}")
    print(f"Approval reference: {args.approval_reference.strip()}")
    print("This action pushes a branch and opens a draft PR. It does not merge or apply it.")
    if input("Type CREATE DRAFT PR to continue: ").strip() != "CREATE DRAFT PR":
        print("Cancelled; no GitHub changes were made.")
        return 2

    result = create_draft_pr(
        event,
        proposal,
        approved=True,
        approval_reference=args.approval_reference.strip(),
        validator_report=validator_report,
        audit=make_audit_writer(),
    )
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
