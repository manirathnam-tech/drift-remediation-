"""Persist one SQS DriftEvent for a Step Functions execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import boto3
from botocore.exceptions import ClientError

MAX_EVENT_BYTES = 1024 * 1024
_EVENT_ID = re.compile(r"^[a-f0-9]{64}$")
_REVISION = re.compile(r"^[a-f0-9]{40}$")
_s3 = None
_sfn = None


class WorkflowIngestError(ValueError):
    """Raised when an SQS event cannot safely start a workflow."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowIngestError(f"duplicate event JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise WorkflowIngestError(f"non-standard JSON number: {value}")


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))
    return _s3


def _step_functions_client():
    global _sfn
    if _sfn is None:
        _sfn = boto3.client(
            "stepfunctions",
            region_name=os.environ.get("AWS_REGION", "ap-southeast-2"),
        )
    return _sfn


def _put_once(bucket: str, key: str, body: bytes, metadata: dict[str, str]) -> None:
    client = _client()
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            Metadata=metadata,
            IfNoneMatch="*",
        )
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
        existing = client.head_object(Bucket=bucket, Key=key)
        existing_metadata = existing.get("Metadata", {})
        if any(existing_metadata.get(name) != value for name, value in metadata.items()):
            raise WorkflowIngestError(
                "duplicate workflow object has different content; refusing replacement"
            ) from exc


def _body(event: Any) -> bytes:
    records: Any = event
    if isinstance(event, dict) and "Records" in event:
        records = event["Records"]
    if isinstance(records, list):
        if len(records) != 1 or not isinstance(records[0], dict):
            raise WorkflowIngestError("workflow ingress requires exactly one SQS record")
        records = records[0]
    if not isinstance(records, dict):
        raise WorkflowIngestError("SQS record must be an object")
    raw = records.get("body")
    if raw is None:
        raw = records.get("Body")
    if not isinstance(raw, str):
        raise WorkflowIngestError("SQS message body must be a string")
    body = raw.encode("utf-8")
    if not body or len(body) > MAX_EVENT_BYTES:
        raise WorkflowIngestError("SQS event body is empty or exceeds 1 MiB")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, WorkflowIngestError) as exc:
        raise WorkflowIngestError("SQS message body is not an unambiguous JSON object") from exc
    if not isinstance(payload, dict):
        raise WorkflowIngestError("SQS message body must contain a JSON object")
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
        raise WorkflowIngestError("DriftEvent event_id must be a SHA-256 hex digest")
    if payload.get("schema_version") != "1.0.0":
        raise WorkflowIngestError("unsupported DriftEvent schema_version")
    if payload.get("event_type") != "configuration_drift_detected":
        raise WorkflowIngestError("unsupported DriftEvent event_type")
    application = payload.get("application")
    if not isinstance(application, dict) or any(
        not isinstance(application.get(field), str)
        or not application[field].strip()
        or len(application[field]) > 253
        for field in ("name", "namespace")
    ):
        raise WorkflowIngestError("DriftEvent application identity is invalid")
    if not isinstance(payload.get("revision"), str) or not _REVISION.fullmatch(
        payload["revision"]
    ):
        raise WorkflowIngestError("DriftEvent revision must be a full Git SHA")
    resources = payload.get("resources")
    if not isinstance(resources, list) or not resources or len(resources) > 200:
        raise WorkflowIngestError("DriftEvent resources must contain 1 to 200 entries")
    for resource in resources:
        if not isinstance(resource, dict) or any(
            not isinstance(resource.get(field), str)
            or not resource[field].strip()
            or len(resource[field]) > 253
            for field in ("kind", "name", "namespace")
        ):
            raise WorkflowIngestError("DriftEvent contains an invalid resource identity")
    return body


def handler(event: Any, context=None) -> dict[str, str]:
    """Persist an event and idempotently start its Standard workflow execution."""
    if not isinstance(event, (dict, list)):
        raise WorkflowIngestError("Pipe enrichment input must be an object or record list")
    body = _body(event)
    payload = json.loads(
        body,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )
    event_id = payload["event_id"]
    event_digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    body_digest = hashlib.sha256(body).hexdigest()
    bucket = os.environ.get("WORKFLOW_STATE_BUCKET", "")
    if not bucket:
        raise WorkflowIngestError("WORKFLOW_STATE_BUCKET is required")
    key = f"workflow-runs/inputs/{event_digest}.json"
    input_metadata = {
        "event-id-sha256": event_digest,
        "body-sha256": body_digest,
    }
    _put_once(bucket, key, body, input_metadata)
    state_key = f"workflow-runs/state/{event_digest}.json"
    initial_state = json.dumps(
        {
            "event_id": event_id,
            "input_key": key,
            "input_sha256": body_digest,
            "stage": "RECEIVED",
            "retry_count": 0,
            "proposal_attempts": [],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    _put_once(
        bucket,
        state_key,
        initial_state,
        {"event-id-sha256": event_digest, "input-sha256": body_digest},
    )

    workflow_input = {
        "event_id": event_id,
        "state_bucket": bucket,
        "input_key": key,
        "state_key": state_key,
        "input_sha256": body_digest,
    }
    state_machine_arn = os.environ.get("WORKFLOW_STATE_MACHINE_ARN", "")
    if not state_machine_arn:
        raise WorkflowIngestError("WORKFLOW_STATE_MACHINE_ARN is required")
    try:
        response = _step_functions_client().start_execution(
            stateMachineArn=state_machine_arn,
            name=f"drift-{event_digest}",
            input=json.dumps(
                workflow_input,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ExecutionAlreadyExists":
            raise
        execution_arn = (
            state_machine_arn.replace(":stateMachine:", ":execution:")
            + f":drift-{event_digest}"
        )
        previous = _step_functions_client().describe_execution(
            executionArn=execution_arn
        )
        previous_status = previous.get("status")
        if previous_status in {"RUNNING", "SUCCEEDED"}:
            return {
                "event_id": event_id,
                "status": (
                    "already_running" if previous_status == "RUNNING" else "already_completed"
                ),
            }
        raise WorkflowIngestError(
            "the deterministic workflow execution already ended unsuccessfully; "
            "the message will follow SQS redrive"
        ) from exc
    return {
        "event_id": event_id,
        "execution_arn": response.get("executionArn", ""),
        "status": "started",
    }
