"""In-cluster injector: apply one synthetic JSON Patch and record T_inject."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3
from kubernetes import client, config
from kubernetes.client.rest import ApiException

NAMESPACE = "online-boutique"
FIELD_MANAGER = "drift-injector"
CONTENT_TYPE = "application/json-patch+json"
ID_PATTERN = re.compile(r"^SYN-[0-9]+-[0-9]{4}$")

RESOURCE_NAMES = {
    "Deployment": {"frontend", "cartservice", "currencyservice"},
    "Service": {
        "frontend-external",
        "cartservice",
        "productcatalogservice",
    },
    "Role": {"drift-observer"},
    "RoleBinding": {"drift-observer"},
    "NetworkPolicy": {
        "allow-callers-to-productcatalogservice",
        "allow-cartservice-to-redis-cart",
    },
}

ALLOWED_TRIGGERS = {
    "manual",
    "kubernetes-job",
    "kubernetes-cronjob",
}


def _load_corpus() -> dict[str, dict]:
    """Load only valid synthetic records for the target namespace."""
    corpus_path = Path(os.environ.get("CORPUS_PATH", "/app/corpus.jsonl"))
    records: dict[str, dict] = {}

    with corpus_path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue

            record = json.loads(line)

            if (
                record.get("synthetic") is not True
                or record.get("namespace") != NAMESPACE
            ):
                continue

            record_id = record.get("id")
            if (
                not isinstance(record_id, str)
                or not ID_PATTERN.fullmatch(record_id)
            ):
                raise ValueError(
                    f"Corpus has an invalid record ID at line {line_number}"
                )

            if record_id in records:
                raise ValueError(f"Corpus has duplicate record ID {record_id}")

            records[record_id] = record

    return records


def _api_client() -> client.ApiClient:
    """Use the Kubernetes credentials mounted for this pod's ServiceAccount."""
    config.load_incluster_config()
    return client.ApiClient()


def _validate_record(record: dict, requested_id: str) -> None:
    if record.get("id") != requested_id or record.get("synthetic") is not True:
        raise ValueError("Selected record is not a synthetic corpus instance")

    kind = record.get("kind")
    namespace = record.get("namespace")
    name = record.get("name")

    if (
        namespace != NAMESPACE
        or kind not in RESOURCE_NAMES
        or name not in RESOURCE_NAMES[kind]
    ):
        raise ValueError(
            "Record target is outside the injector's namespace/resource allowlist"
        )

    mutation = record.get("mutation")
    if not isinstance(mutation, list) or not mutation:
        raise ValueError("Record has no JSON Patch operations")

    for operation in mutation:
        if (
            not isinstance(operation, dict)
            or operation.get("op") not in {"add", "replace", "remove"}
        ):
            raise ValueError("Record contains an unsupported JSON Patch operation")

        path = operation.get("path")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or "/metadata/managedFields" in path
        ):
            raise ValueError("Record contains an invalid or forbidden JSON Patch path")

        if operation["op"] in {"add", "replace"} and "value" not in operation:
            raise ValueError("Add/replace patch operations require a value")

        if operation["op"] == "remove" and "value" in operation:
            raise ValueError("Remove patch operations cannot include a value")


def _api_for(api_client: client.ApiClient, kind: str):
    if kind == "Deployment":
        return client.AppsV1Api(api_client), "deployment"

    if kind == "Service":
        return client.CoreV1Api(api_client), "service"

    if kind == "Role":
        return client.RbacAuthorizationV1Api(api_client), "role"

    if kind == "RoleBinding":
        return client.RbacAuthorizationV1Api(api_client), "role_binding"

    if kind == "NetworkPolicy":
        return client.NetworkingV1Api(api_client), "network_policy"

    raise ValueError(f"Unsupported Kubernetes kind: {kind}")


def _patch_record(api_client: client.ApiClient, record: dict):
    api, noun = _api_for(api_client, record["kind"])
    name = record["name"]
    namespace = record["namespace"]

    read_method = getattr(api, f"read_namespaced_{noun}")
    patch_method = getattr(api, f"patch_namespaced_{noun}")

    current = read_method(name=name, namespace=namespace)
    patch = list(record["mutation"])

    # Initialize the annotations map if the mutation adds an annotation
    # and the map does not already exist.
    if patch[0].get("path", "").startswith("/metadata/annotations/"):
        metadata = getattr(current, "metadata", None)
        annotations = getattr(metadata, "annotations", None) if metadata else None

        if not annotations:
            patch.insert(
                0,
                {
                    "op": "add",
                    "path": "/metadata/annotations",
                    "value": {},
                },
            )

    return patch_method(
        name=name,
        namespace=namespace,
        body=patch,
        field_manager=FIELD_MANAGER,
        _content_type=CONTENT_TYPE,
    )


def _ledger_table():
    table_name = os.environ.get("LEDGER_TABLE")
    if not table_name:
        raise ValueError("LEDGER_TABLE environment variable is required")

    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("AWS_REGION_NAME")
    )
    if not region:
        raise ValueError("Set AWS_REGION or AWS_DEFAULT_REGION for DynamoDB")

    return boto3.resource("dynamodb", region_name=region).Table(table_name)


def require_previous_reset_clean() -> None:
    """Refuse injection unless the previous trial has a recorded clean reset."""
    previous_instance_id = os.environ.get("PREVIOUS_INSTANCE_ID")
    previous_trial_id = os.environ.get("PREVIOUS_TRIAL_ID")

    if not previous_instance_id or not previous_trial_id:
        raise ValueError(
            "PREVIOUS_INSTANCE_ID and PREVIOUS_TRIAL_ID are required; "
            "reset the previous trial before injecting another"
        )

    current_instance_id = os.environ.get("INSTANCE_ID")
    current_trial_id = os.environ.get("TRIAL_ID")

    if (
        previous_instance_id == current_instance_id
        and previous_trial_id == current_trial_id
    ):
        raise ValueError("The previous trial cannot be the current trial")

    item = _ledger_table().get_item(
        Key={
            "pk": f"DRIFT#{previous_instance_id}",
            "sk": f"TRIAL#{previous_trial_id}",
        },
        ConsistentRead=True,
    ).get("Item")

    if not item or item.get("reset_status") != "CLEAN":
        raise RuntimeError(
            "Previous trial has no CLEAN reset record; refusing injection"
        )


def _write_ledger(
    record: dict,
    trial_id: str,
    t_inject: str,
    epoch_ms: int,
    trigger: str,
) -> None:
    """Write the successful injection timestamp using the pod's AWS identity."""
    table = _ledger_table()
    table.put_item(
        Item={
            "pk": f"DRIFT#{record['id']}",
            "sk": f"TRIAL#{trial_id}",
            "record_id": record["id"],
            "trial_id": trial_id,
            "class": record["class"],
            "severity": record["severity"],
            "intent": record["intent"],
            "kind": record["kind"],
            "namespace": record["namespace"],
            "name": record["name"],
            "mutation": json.dumps(
                record["mutation"],
                sort_keys=True,
                separators=(",", ":"),
            ),
            "field_manager": FIELD_MANAGER,
            "trigger": trigger,
            "t_inject": t_inject,
            "t_inject_epoch_ms": epoch_ms,
        },
        ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
    )


def handler(event: dict | None, context=None) -> dict:
    """Run one injection after confirming the previous trial reset is clean."""
    event = event or {}

    instance_id = event.get("instance_id", event.get("id"))
    if not isinstance(instance_id, str) or not ID_PATTERN.fullmatch(instance_id):
        raise ValueError(
            "Provide instance_id as a valid corpus ID, "
            "for example SYN-20261022-0006"
        )

    record = _load_corpus().get(instance_id)
    if record is None:
        raise ValueError(f"Corpus record not found: {instance_id}")

    _validate_record(record, instance_id)

    trial_id = event.get("trial_id") or str(uuid.uuid4())
    if (
        not isinstance(trial_id, str)
        or len(trial_id) > 128
        or not re.fullmatch(r"[A-Za-z0-9._:-]+", trial_id)
    ):
        raise ValueError(
            "trial_id must contain only letters, digits, dot, underscore, "
            "colon or hyphen"
        )

    trigger = event.get("trigger", "manual")
    if trigger not in ALLOWED_TRIGGERS:
        raise ValueError(
            "trigger must be manual, kubernetes-job, or kubernetes-cronjob"
        )

    # This check must succeed before creating a Kubernetes client or mutating
    # the cluster. Each Job must provide the previous trial's IDs as env vars.
    require_previous_reset_clean()

    api_client = _api_client()
    try:
        response = _patch_record(api_client, record)

        epoch_ms = time.time_ns() // 1_000_000
        t_inject = (
            datetime.fromtimestamp(epoch_ms / 1000, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

        _write_ledger(record, trial_id, t_inject, epoch_ms, trigger)

        metadata = getattr(response, "metadata", None)
        resource_version = getattr(metadata, "resource_version", None)

        return {
            "status": "injected",
            "instance_id": instance_id,
            "trial_id": trial_id,
            "kind": record["kind"],
            "namespace": record["namespace"],
            "name": record["name"],
            "field_manager": FIELD_MANAGER,
            "t_inject": t_inject,
            "ledger_table": os.environ["LEDGER_TABLE"],
            "resource_version": resource_version,
        }
    except ApiException as exc:
        # Do not emit response bodies that might contain application data.
        raise RuntimeError(
            f"Kubernetes API request failed with status {exc.status}: {exc.reason}"
        ) from None
    finally:
        api_client.close()


def main() -> None:
    """Read one run's parameters from the Kubernetes Job environment."""
    event = {
        "instance_id": os.environ.get("INSTANCE_ID"),
        "trial_id": os.environ.get("TRIAL_ID"),
        "trigger": os.environ.get("TRIGGER", "manual"),
    }

    result = handler(event)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
