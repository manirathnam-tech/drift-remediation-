"""Authenticate and enrich Argo CD OutOfSync notifications."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import ssl
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import boto3

REGION = os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
APP_NAME = os.environ.get("ARGOCD_APP_NAME", "online-boutique")
APP_NAMESPACE = "online-boutique"
ARGOCD_NAMESPACE = os.environ.get("ARGOCD_NAMESPACE", "argocd")
ARGOCD_SERVICE = os.environ.get("ARGOCD_SERVICE_NAME", "argocd-server")
MAX_BODY_BYTES = 128 * 1024
MAX_RESOURCES = 20

ALLOWED_KINDS = {"Deployment", "Service", "Role", "RoleBinding", "NetworkPolicy"}
ALLOWED_RESOURCES = {
    "Deployment": {"frontend", "cartservice", "currencyservice"},
    "Service": {"frontend-external", "cartservice", "productcatalogservice"},
    "Role": {"drift-observer"},
    "RoleBinding": {"drift-observer"},
    "NetworkPolicy": {
        "allow-callers-to-productcatalogservice",
        "allow-cartservice-to-redis-cart",
    },
}

_secrets: dict[str, tuple[str, float]] = {}
_idempotency_table = None


class EventError(ValueError):
    """A malformed or out-of-scope webhook event."""


class UpstreamError(RuntimeError):
    """An Argo CD or AWS dependency failed during event enrichment."""


def _json_response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, separators=(",", ":")),
    }


def _read_body(event: dict) -> tuple[dict, dict[str, str]]:
    headers = {
        str(key).lower(): str(value)
        for key, value in (event.get("headers") or {}).items()
    }
    raw = event.get("body") or ""

    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise EventError("Request body is not valid base64 UTF-8") from exc

    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise EventError("Request body exceeds the 128 KiB limit")

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise EventError("Request body must be valid JSON") from exc

    if not isinstance(payload, dict):
        raise EventError("Request body must be a JSON object")

    return payload, headers


def _validate_payload(payload: dict) -> list[dict]:
    if payload.get("event_type") != "argocd.application.out_of_sync":
        raise EventError("Unsupported event_type")
    if payload.get("application") != APP_NAME:
        raise EventError("Application is not allowlisted")
    if payload.get("namespace") != APP_NAMESPACE:
        raise EventError("Namespace is not allowlisted")
    if payload.get("sync_status") != "OutOfSync":
        raise EventError("Only OutOfSync notifications are accepted")

    revision = payload.get("revision")
    reconciled_at = payload.get("reconciled_at")

    if not isinstance(revision, str) or not revision or len(revision) > 128:
        raise EventError("revision is required")
    if not isinstance(reconciled_at, str) or len(reconciled_at) > 64:
        raise EventError("reconciled_at is required")

    try:
        datetime.fromisoformat(reconciled_at)
    except ValueError as exc:
        raise EventError("reconciled_at must be an RFC3339 timestamp") from exc

    resources = payload.get("resources")
    if not isinstance(resources, list) or not resources or len(resources) > MAX_RESOURCES:
        raise EventError("resources must contain between 1 and 20 entries")

    for resource in resources:
        if not isinstance(resource, dict):
            raise EventError("Each resource entry must be an object")

        kind = resource.get("kind")
        name = resource.get("name")

        if (
            not isinstance(kind, str)
            or kind not in ALLOWED_KINDS
            or not isinstance(name, str)
            or name not in ALLOWED_RESOURCES[kind]
            or not isinstance(resource.get("namespace"), str)
            or resource.get("namespace") != APP_NAMESPACE
            or not isinstance(resource.get("status"), str)
            or resource.get("status") != "OutOfSync"
        ):
            raise EventError("Resource is outside the allowlist or is not OutOfSync")

    return resources


def _secret(secret_arn: str) -> str:
    cached = _secrets.get(secret_arn)
    if cached is None or time.monotonic() - cached[1] > 300:
        response = boto3.client("secretsmanager", region_name=REGION).get_secret_value(
            SecretId=secret_arn
        )
        value = response.get("SecretString")
        if not isinstance(value, str) or not value:
            raise UpstreamError("Configured Secrets Manager value is empty")
        _secrets[secret_arn] = (value, time.monotonic())

    return _secrets[secret_arn][0]


def _argocd_url(path: str, query: dict[str, str] | None = None) -> str:
    service_host = f"{ARGOCD_SERVICE}.{ARGOCD_NAMESPACE}.svc.cluster.local"
    url = f"https://{service_host}{path}"
    return f"{url}?{urlencode(query)}" if query else url


def _get_argocd(path: str, query: dict[str, str] | None = None) -> dict:
    argo_token = _secret(os.environ["ARGOCD_TOKEN_SECRET_ARN"])
    request = Request(
        _argocd_url(path, query),
        headers={
            "Cookie": f"argocd.token={argo_token.strip()}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        ssl_context = ssl.create_default_context(
            cafile="/etc/drift-collector/argocd.crt"
        )
        with urlopen(request, timeout=10, context=ssl_context) as response:
            return json.loads(response.read(MAX_BODY_BYTES + 1))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamError("Argo CD API request failed") from exc


def _parse_state(value):
    if value is None or isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    return None


def _fetch_resources(
    app: dict,
    payload_resources: list[dict],
    revision: str,
) -> list[dict]:
    status = app.get("status", {})

    if status.get("sync", {}).get("status") != "OutOfSync":
        raise EventError("Argo CD no longer reports the application OutOfSync")
    if status.get("sync", {}).get("revision") != revision:
        raise EventError("Argo CD source revision changed after the notification")

    enriched = []

    for target in payload_resources:
        api_version = target.get("version") or "v1"
        group = target.get("group") or ""
        query = {
            "namespace": APP_NAMESPACE,
            "name": target["name"],
            "version": api_version,
            "group": group,
            "kind": target["kind"],
            "appNamespace": ARGOCD_NAMESPACE,
        }
        result = _get_argocd(
            f"/api/v1/applications/{quote(APP_NAME, safe='')}/managed-resources",
            query,
        )
        items = result.get("items", [])
        matching = [
            item
            for item in items
            if item.get("name") == target["name"]
            and item.get("namespace") == APP_NAMESPACE
            and item.get("kind") == target["kind"]
        ]

        if len(matching) != 1:
            raise EventError(
                "Argo CD did not return one matching OutOfSync managed resource"
            )

        item = matching[0]
        live = _parse_state(item.get("liveState"))
        target_state = _parse_state(item.get("targetState"))
        diff = _parse_state(item.get("diff"))

        if live is not None and not isinstance(live, dict):
            raise UpstreamError("Argo CD returned an invalid live resource object")

        enriched.append(
            {
                "group": group,
                "version": api_version,
                "kind": target["kind"],
                "namespace": APP_NAMESPACE,
                "name": target["name"],
                "status": "OutOfSync",
                "health": item.get("health"),
                "live_state": live,
                "target_state": target_state,
                "diff": diff,
            }
        )

    return enriched


def _event_id(payload: dict, resources: list[dict]) -> str:
    material = {
        "application": APP_NAME,
        "namespace": APP_NAMESPACE,
        "revision": payload["revision"],
        "reconciled_at": payload["reconciled_at"],
        "resources": resources,
    }
    encoded = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _table():
    global _idempotency_table

    if _idempotency_table is None:
        _idempotency_table = boto3.resource(
            "dynamodb",
            region_name=REGION,
        ).Table(os.environ["IDEMPOTENCY_TABLE"])

    return _idempotency_table


def _publish(event: dict) -> None:
    boto3.client("sns", region_name=REGION).publish(
        TopicArn=os.environ["EVENT_TOPIC_ARN"],
        Message=json.dumps(event, sort_keys=True, separators=(",", ":")),
        MessageAttributes={
            "schema_version": {
                "DataType": "String",
                "StringValue": "1.0.0",
            },
            "event_type": {
                "DataType": "String",
                "StringValue": event["event_type"],
            },
        },
    )


def _quarantine(payload: dict, context, error: Exception) -> None:
    message = {
        "payload": payload,
        "lambda_request_id": getattr(context, "aws_request_id", "unknown"),
        "failure_type": type(error).__name__,
        "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }

    try:
        boto3.client("sqs", region_name=REGION).send_message(
            QueueUrl=os.environ["FAILURE_QUEUE_URL"],
            MessageBody=json.dumps(message, separators=(",", ":")),
        )
    except Exception as queue_error:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "error": "collector_quarantine_failed",
                    "type": type(queue_error).__name__,
                }
            )
        )


def handler(event: dict, context=None) -> dict:
    """Handle an Argo CD OutOfSync notification and publish a DriftEvent."""
    payload = {}

    try:
        payload, headers = _read_body(event)
        expected = _secret(os.environ["WEBHOOK_SECRET_ARN"])
        supplied = headers.get("x-drift-webhook-secret", "")

        if not hmac.compare_digest(supplied, expected):
            return _json_response(401, {"error": "unauthorized"})

        payload_resources = _validate_payload(payload)
        app = _get_argocd(
            f"/api/v1/applications/{quote(APP_NAME, safe='')}",
            {"appNamespace": ARGOCD_NAMESPACE},
        )
        resources = _fetch_resources(
            app,
            payload_resources,
            payload["revision"],
        )
        event_id = _event_id(payload, resources)

        now = datetime.now(UTC)
        trial_event = {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "event_type": "configuration_drift_detected",
            "application": {
                "name": APP_NAME,
                "namespace": APP_NAMESPACE,
            },
            "revision": payload["revision"],
            "t_detect": now.isoformat().replace("+00:00", "Z"),
            "reconciled_at": payload["reconciled_at"],
            "received_at": now.isoformat().replace("+00:00", "Z"),
            "resources": resources,
        }

        try:
            _table().put_item(
                Item={
                    "event_id": event_id,
                    "status": "PUBLISHING",
                    "created_at": int(time.time()),
                    "expires_at": int(time.time()) + 86400,
                },
                ConditionExpression="attribute_not_exists(event_id)",
            )
        except Exception as exc:
            if type(exc).__name__ == "ConditionalCheckFailedException":
                return _json_response(
                    200,
                    {
                        "status": "duplicate_suppressed",
                        "event_id": event_id,
                    },
                )
            raise

        try:
            _publish(trial_event)
        except Exception:
            try:
                _table().delete_item(Key={"event_id": event_id})
            except Exception as cleanup_error:  # noqa: BLE001
                print(
                    json.dumps(
                        {
                            "error": "idempotency_cleanup_failed",
                            "type": type(cleanup_error).__name__,
                        }
                    )
                )
            raise

        return _json_response(
            202,
            {
                "status": "accepted",
                "event_id": event_id,
            },
        )
    except EventError as exc:
        return _json_response(400, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        _quarantine(payload, context, exc)
        return _json_response(202, {"status": "quarantined_for_review"})
