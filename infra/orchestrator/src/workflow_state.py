"""Bounded, checksummed S3 state for Step Functions treatment executions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from errors import TreatmentDependencyError

MAX_INPUT_BYTES = 1024 * 1024
MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024
_INPUT_KEY = re.compile(r"^workflow-runs/inputs/[a-f0-9]{64}\.json$")
_STATE_KEY = re.compile(r"^workflow-runs/state/[a-f0-9]{64}\.json$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_s3 = None


class WorkflowStateError(TreatmentDependencyError):
    """Raised when a workflow input or checkpoint is invalid or unavailable."""


def _event_digest(event_id: str) -> str:
    return hashlib.sha256(event_id.encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate workflow JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-standard JSON number: {value}")


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION_NAME", "ap-southeast-2"),
        )
    return _s3


def _bucket() -> str:
    bucket = os.environ.get("WORKFLOW_STATE_BUCKET") or os.environ.get(
        "GOLDEN_MANIFESTS_BUCKET", ""
    )
    if not bucket:
        raise WorkflowStateError("WORKFLOW_STATE_BUCKET is required")
    return bucket


def _read_json_object(key: str, limit: int) -> tuple[dict[str, Any], str, bytes]:
    try:
        response = _client().get_object(Bucket=_bucket(), Key=key)
        stream = response["Body"]
        try:
            body = stream.read(limit + 1)
        finally:
            stream.close()
    except (BotoCoreError, ClientError, KeyError, OSError) as exc:
        raise WorkflowStateError("workflow state object could not be read") from exc
    if len(body) > limit:
        raise WorkflowStateError("workflow state object exceeds its size limit")
    try:
        value = json.loads(
            body,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorkflowStateError("workflow state object is not valid JSON") from exc
    if not isinstance(value, dict):
        raise WorkflowStateError("workflow state object must contain a JSON object")
    etag = response.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise WorkflowStateError("workflow state object has no ETag")
    return value, etag, body


def load_event(input_key: str, expected_sha256: str) -> dict[str, Any]:
    """Load the original event only from the dedicated input-object prefix."""
    if not isinstance(input_key, str) or not _INPUT_KEY.fullmatch(input_key):
        raise WorkflowStateError("workflow input key is outside the approved prefix")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise WorkflowStateError("workflow input checksum is invalid")
    value, _, body = _read_json_object(input_key, MAX_INPUT_BYTES)
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise WorkflowStateError("workflow input checksum does not match")
    event_id = value.get("event_id")
    if (
        not isinstance(event_id, str)
        or not event_id.strip()
        or len(event_id) > 256
        or _event_digest(event_id) != input_key.rsplit("/", 1)[1][:-5]
    ):
        raise WorkflowStateError("workflow input has no event_id")
    return value


def load_checkpoint(state_key: str) -> tuple[dict[str, Any], str]:
    """Load the enrichment-created checkpoint and its ETag."""
    if not isinstance(state_key, str) or not _STATE_KEY.fullmatch(state_key):
        raise WorkflowStateError("workflow checkpoint key is outside the approved prefix")
    value, etag, _ = _read_json_object(state_key, MAX_CHECKPOINT_BYTES)
    event_id = value.get("event_id")
    input_key = value.get("input_key")
    input_sha256 = value.get("input_sha256")
    expected_digest = state_key.rsplit("/", 1)[1][:-5]
    if (
        not isinstance(event_id, str)
        or not event_id.strip()
        or len(event_id) > 256
        or _event_digest(event_id) != expected_digest
        or input_key != f"workflow-runs/inputs/{expected_digest}.json"
        or not isinstance(input_sha256, str)
        or not _SHA256.fullmatch(input_sha256)
    ):
        raise WorkflowStateError("workflow checkpoint identity or input reference is invalid")
    return value, etag


def save_checkpoint(
    state_key: str,
    state: dict[str, Any],
    *,
    next_stage: str,
    expected_etag: str | None,
) -> str:
    """Conditionally save state so a duplicate stage cannot overwrite newer data."""
    if not isinstance(state_key, str) or not _STATE_KEY.fullmatch(state_key):
        raise WorkflowStateError("workflow checkpoint key is outside the approved prefix")
    if not isinstance(state, dict):
        raise WorkflowStateError("workflow checkpoint state must be an object")
    event_id = state.get("event_id")
    input_sha256 = state.get("input_sha256")
    expected_digest = state_key.rsplit("/", 1)[1][:-5]
    if (
        not isinstance(event_id, str)
        or not event_id.strip()
        or len(event_id) > 256
        or _event_digest(event_id) != expected_digest
    ):
        raise WorkflowStateError("workflow checkpoint requires event_id")
    if state.get("input_key") != f"workflow-runs/inputs/{expected_digest}.json":
        raise WorkflowStateError("workflow checkpoint input reference does not match event_id")
    if not isinstance(input_sha256, str) or not _SHA256.fullmatch(input_sha256):
        raise WorkflowStateError("workflow checkpoint requires a valid input checksum")
    if not isinstance(next_stage, str):
        raise WorkflowStateError("workflow checkpoint next stage must be a string")
    if next_stage not in {
        "ProcessTreatment",
        "Synthesize",
        "Validate",
        "Retry",
        "Escalate",
        "Route",
        "RouteProposal",
        "CreatePR",
        "MergePolicy",
        "WaitForConvergence",
        "RecordLedger",
        "Done",
    }:
        raise WorkflowStateError("unsupported next workflow stage")
    try:
        body = json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorkflowStateError("workflow checkpoint must be finite JSON data") from exc
    if len(body) > MAX_CHECKPOINT_BYTES:
        raise WorkflowStateError("workflow checkpoint exceeds the 4 MiB size limit")

    if expected_etag is not None and (
        not isinstance(expected_etag, str) or not expected_etag
    ):
        raise WorkflowStateError("workflow checkpoint ETag is invalid")
    condition = {"IfMatch": expected_etag} if expected_etag else {"IfNoneMatch": "*"}
    try:
        response = _client().put_object(
            Bucket=_bucket(),
            Key=state_key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            Metadata={
                "event-id-sha256": hashlib.sha256(event_id.encode("utf-8")).hexdigest(),
                "input-sha256": input_sha256,
                "next-stage": next_stage,
            },
            **condition,
        )
    except (BotoCoreError, ClientError) as exc:
        raise WorkflowStateError("workflow checkpoint update failed or conflicted") from exc
    etag = response.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise WorkflowStateError("workflow checkpoint write returned no ETag")
    return etag
