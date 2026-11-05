"""SQS entry point for deterministic baseline classification and Argo CD sync."""

from __future__ import annotations

import base64
import json
import os
import ssl
import time
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import boto3
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError
from rules import classify_event

REGION = os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
APP_NAME = "online-boutique"
APP_NAMESPACE = "argocd"
MAX_BODY_BYTES = 256 * 1024
MAX_AUDIT_RECORD_BYTES = 350_000


class BaselineError(RuntimeError):
    """Raised when an event cannot be safely classified or reverted."""


def _audit(record: dict[str, Any]) -> None:
    table_name = os.environ["TREATMENT_AUDIT_TABLE"]
    item = {
        **record,
        "record_id": f"baseline#{record['stage']}",
        "created_at": int(time.time()),
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if len(json.dumps(item, separators=(",", ":")).encode("utf-8")) > MAX_AUDIT_RECORD_BYTES:
        raise BaselineError("baseline audit record exceeds the safe item-size limit")
    try:
        boto3.resource("dynamodb", region_name=REGION).Table(table_name).put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(event_id) AND attribute_not_exists(record_id)"
            ),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def _eks_token(cluster_name: str) -> str:
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise BaselineError("AWS credentials are unavailable")
    request = AWSRequest(
        method="GET",
        url=f"https://sts.{REGION}.amazonaws.com/?" + urlencode(
            {"Action": "GetCallerIdentity", "Version": "2011-06-15"}
        ),
        headers={"x-k8s-aws-id": cluster_name},
    )
    SigV4QueryAuth(credentials.get_frozen_credentials(), "sts", REGION, expires=60).add_auth(
        request
    )
    token = base64.urlsafe_b64encode(request.url.encode("utf-8")).decode("ascii")
    return "k8s-aws-v1." + token.rstrip("=")


def _request_sync(event: dict[str, Any]) -> dict[str, Any]:
    endpoint = os.environ["EKS_ENDPOINT"].rstrip("/")
    cluster_name = os.environ["EKS_CLUSTER_NAME"]
    ca_pem = base64.b64decode(os.environ["EKS_CA_DATA"], validate=True).decode("ascii")
    path = (
        f"/apis/argoproj.io/v1alpha1/namespaces/{APP_NAMESPACE}"
        f"/applications/{APP_NAME}"
    )
    resources = [
        {
            "group": resource.get("group", ""),
            "version": resource.get("version", "v1"),
            "kind": resource["kind"],
            "namespace": resource["namespace"],
            "name": resource["name"],
        }
        for resource in event["resources"]
    ]
    patch = {
        "operation": {
            "sync": {
                "revision": event["revision"],
                "prune": True,
                "resources": resources,
            }
        }
    }
    context = ssl.create_default_context(cadata=ca_pem)
    headers = {
        "Authorization": f"Bearer {_eks_token(cluster_name)}",
        "Accept": "application/json",
    }
    try:
        get_request = Request(endpoint + path, headers=headers, method="GET")
        with urlopen(get_request, timeout=10, context=context) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise BaselineError("Kubernetes API response exceeded the size limit")
            application = json.loads(body) if body else {}
        status = application.get("status", {})
        if status.get("sync", {}).get("revision") != event["revision"]:
            raise BaselineError("event revision is stale relative to the Argo CD application")
        if status.get("sync", {}).get("status") != "OutOfSync":
            return {"already_synced": True, "spec": {}}
        operation_state = status.get("operationState", {})
        operation_phase = operation_state.get("phase")
        if application.get("operation") and operation_phase not in {
            "Succeeded",
            "Failed",
            "Error",
        }:
            raise BaselineError("Argo CD already has a pending sync operation")
        if operation_phase in {"Running", "Terminating"}:
            raise BaselineError("Argo CD already has an active sync operation")

        patch_request = Request(
            endpoint + path,
            data=json.dumps(patch, separators=(",", ":")).encode("utf-8"),
            headers={
                **headers,
                "Content-Type": "application/merge-patch+json",
            },
            method="PATCH",
        )
        with urlopen(patch_request, timeout=10, context=context) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise BaselineError("Kubernetes API response exceeded the size limit")
            return json.loads(body) if body else {}
    except HTTPError as exc:
        raise BaselineError(f"Kubernetes API rejected the Argo CD sync request ({exc.code})") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BaselineError("Kubernetes API Argo CD sync request failed") from exc


def _process_message(body: str) -> None:
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise BaselineError("queue message is invalid or exceeds the size limit")
    try:
        event = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BaselineError("queue message is not valid JSON") from exc
    if not isinstance(event, dict):
        raise BaselineError("queue message must be a JSON object")

    result = classify_event(event)
    common = {
        "event_id": result["event_id"],
        "arm": "baseline",
        "ruleset_version": result["ruleset_version"],
        "severity": result["severity"],
        "drift_class": result["drift_class"],
        "application": result["application"],
        "revision": result["revision"],
        "t_detect": result["t_detect"],
        "received_at": result["received_at"],
        "reconciled_at": result["reconciled_at"],
        "output": result,
    }
    _audit(
        {
            **common,
            "stage": "classification",
            "verdict": "PASS",
            "decision": result["decision"],
        }
    )

    response = _request_sync(event)
    if response.get("already_synced"):
        _audit(
            {
                **common,
                "stage": "baseline_reversion",
                "verdict": "NOOP",
                "decision": "already_converged",
            }
        )
        return
    operation = response.get("operation", {})
    _audit(
        {
            **common,
            "stage": "baseline_reversion",
            "verdict": "REQUESTED",
            "decision": "hard_revert",
            "operation_revision": operation.get("sync", {}).get("revision"),
            "target_resources": [
                {
                    "kind": item["kind"],
                    "namespace": item["namespace"],
                    "name": item["name"],
                }
                for item in event["resources"]
            ],
        }
    )


def handler(event: dict[str, Any], context: Any = None) -> dict[str, list[dict[str, str]]]:
    """Process SQS messages and report individual failures for safe retries."""
    del context
    failures = []
    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            _process_message(record.get("body", ""))
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "event": "baseline_message_failed",
                        "message_id": message_id,
                        "error_type": type(exc).__name__,
                    },
                    separators=(",", ":"),
                )
            )
            if isinstance(message_id, str) and message_id:
                failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
