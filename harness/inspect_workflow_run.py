#!/usr/bin/env python3
"""Read-only summary of one Step Functions treatment execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

ORCHESTRATOR_SRC = os.path.join(os.path.dirname(__file__), "..", "infra", "orchestrator", "src")
sys.path.insert(0, os.path.abspath(ORCHESTRATOR_SRC))
from workflow_state import load_checkpoint

EVENT_ID = re.compile(r"^[a-f0-9]{64}$")


def _execution_arn(state_machine_arn: str, event_id: str) -> str:
    marker = ":stateMachine:"
    if marker not in state_machine_arn:
        raise ValueError("state-machine ARN is invalid")
    prefix, state_machine_name = state_machine_arn.split(marker, 1)
    if not state_machine_name or ":" in state_machine_name:
        raise ValueError("state-machine ARN is invalid")
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return f"{prefix}:execution:{state_machine_name}:drift-{digest}"


def _execution_summary(client, execution_arn: str) -> dict[str, Any]:
    try:
        execution = client.describe_execution(executionArn=execution_arn)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "ExecutionDoesNotExist":
            return {"status": "NOT_FOUND"}
        raise
    return {
        "status": execution.get("status"),
        "start_date": execution.get("startDate"),
        "stop_date": execution.get("stopDate"),
        "error": execution.get("error"),
    }


def _audit_summary(table_name: str, event_id: str, region: str) -> list[dict[str, Any]]:
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    response = table.query(
        KeyConditionExpression=Key("event_id").eq(event_id),
        ConsistentRead=True,
        ScanIndexForward=True,
        Limit=200,
        ProjectionExpression=(
            "#rid, #recorded, #stage, #verdict, #decision, #error_type, "
            "#patch_sha, #pr_number"
        ),
        ExpressionAttributeNames={
            "#rid": "record_id",
            "#recorded": "recorded_at",
            "#stage": "stage",
            "#verdict": "verdict",
            "#decision": "decision",
            "#error_type": "error_type",
            "#patch_sha": "patch_sha256",
            "#pr_number": "pr_number",
        },
    )
    rows = response.get("Items", [])
    return [
        {
            key: row[key]
            for key in (
                "record_id",
                "recorded_at",
                "stage",
                "verdict",
                "decision",
                "error_type",
                "patch_sha256",
                "pr_number",
            )
            if key in row
        }
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--state-machine-arn", required=True)
    parser.add_argument("--bucket", default=os.environ.get("WORKFLOW_STATE_BUCKET"))
    parser.add_argument(
        "--audit-table", default=os.environ.get("TREATMENT_AUDIT_TABLE")
    )
    parser.add_argument(
        "--region", default=os.environ.get("AWS_REGION", "ap-southeast-2")
    )
    args = parser.parse_args(argv)
    if not EVENT_ID.fullmatch(args.event_id):
        parser.error("--event-id must be a 64-character lowercase SHA-256 digest")
    if not args.bucket:
        parser.error("--bucket or WORKFLOW_STATE_BUCKET is required")
    if not args.audit_table:
        parser.error("--audit-table or TREATMENT_AUDIT_TABLE is required")

    os.environ["AWS_REGION_NAME"] = args.region
    os.environ["WORKFLOW_STATE_BUCKET"] = args.bucket
    state_key = (
        "workflow-runs/state/"
        f"{hashlib.sha256(args.event_id.encode('utf-8')).hexdigest()}.json"
    )
    report: dict[str, Any] = {
        "event_id": args.event_id,
        "execution": _execution_summary(
            boto3.client("stepfunctions", region_name=args.region),
            _execution_arn(args.state_machine_arn, args.event_id),
        ),
    }
    try:
        checkpoint, _ = load_checkpoint(state_key)
        proposal = checkpoint.get("proposal")
        report["checkpoint"] = {
            "stage": checkpoint.get("stage"),
            "retry_count": checkpoint.get("retry_count"),
            "failure_type": checkpoint.get("failure_type"),
            "decision": checkpoint.get("decision"),
            "proposal_summary": proposal.get("summary")
            if isinstance(proposal, dict)
            else None,
            "proposal_patch_sha256": checkpoint.get("proposal_patch_sha256"),
        }
    except Exception as exc:  # noqa: BLE001 - report a bounded read-only diagnostic
        report["checkpoint_error"] = type(exc).__name__

    try:
        report["audit"] = _audit_summary(args.audit_table, args.event_id, args.region)
    except Exception as exc:  # noqa: BLE001 - retain other diagnostic results
        report["audit_error"] = type(exc).__name__
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
