"""Append-only DynamoDB writer for treatment attempts and decisions."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from errors import TreatmentDependencyError

MAX_AUDIT_RECORD_BYTES = 350_000


class AuditWriteError(TreatmentDependencyError):
    """Raised when an audit record cannot be safely persisted."""


def _dynamo_value(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _dynamo_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dynamo_value(item) for item in value]
    return value


def make_audit_writer(
    table_name: str | None = None,
    *,
    region: str | None = None,
) -> Callable[[dict[str, Any]], None]:
    """Create a writer that appends one bounded record per treatment stage."""
    selected_table = table_name or os.environ.get("TREATMENT_AUDIT_TABLE")
    if not selected_table:
        raise AuditWriteError("TREATMENT_AUDIT_TABLE is required")

    dynamodb = boto3.resource(
        "dynamodb",
        region_name=region or os.environ.get("AWS_REGION_NAME", "ap-southeast-2"),
    )
    table = dynamodb.Table(selected_table)

    def write(record: dict[str, Any]) -> None:
        event_id = record.get("event_id")
        stage = record.get("stage")
        verdict = record.get("verdict")
        if not isinstance(event_id, str) or not event_id:
            raise AuditWriteError("audit record requires event_id")
        if not isinstance(stage, str) or not stage:
            raise AuditWriteError("audit record requires stage")
        if not isinstance(verdict, str) or not verdict:
            raise AuditWriteError("audit record requires verdict")

        created_at = int(time.time())
        item = {
            **record,
            "record_id": uuid.uuid4().hex,
            "created_at": created_at,
            "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        try:
            encoded_size = len(
                json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise AuditWriteError("audit record must contain JSON-compatible values") from exc
        if encoded_size > MAX_AUDIT_RECORD_BYTES:
            raise AuditWriteError("audit record exceeds the safe DynamoDB item-size limit")

        try:
            table.put_item(
                Item=_dynamo_value(item),
                ConditionExpression="attribute_not_exists(event_id) AND attribute_not_exists(record_id)",
            )
        except (BotoCoreError, ClientError, TypeError, ValueError) as exc:
            raise AuditWriteError("DynamoDB audit write failed") from exc

    return write
