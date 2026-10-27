#!/usr/bin/env python3
"""Reset a trial through Argo CD and record the outcome in DynamoDB."""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone


REGION = "ap-southeast-2"
TABLE = "drift-thesis-experiment-ledger"
APP = "online-boutique"
APP_NAMESPACE = "argocd"


def run_json(args):
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def kubectl_json(*args):
    return run_json(["kubectl", *args, "-o", "json"])


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def update_ledger(instance_id, trial_id, status, started, error=None):
    ended = utc_now()
    values = {
        ":status": {"S": status},
        ":start": {"S": timestamp(started)},
        ":end": {"S": timestamp(ended)},
        ":duration": {
            "N": str(round((ended - started).total_seconds() * 1000))
        },
    }
    expression = (
        "SET reset_status=:status, t_reset_start=:start, "
        "t_reset_end=:end, reset_duration_ms=:duration"
    )

    if error:
        values[":error"] = {"S": error[:900]}
        expression += ", reset_error=:error"

    subprocess.run(
        [
            "aws",
            "dynamodb",
            "update-item",
            "--table-name",
            TABLE,
            "--region",
            REGION,
            "--key",
            json.dumps(
                {
                    "pk": {"S": f"DRIFT#{instance_id}"},
                    "sk": {"S": f"TRIAL#{trial_id}"},
                }
            ),
            "--update-expression",
            expression,
            "--expression-attribute-values",
            json.dumps(values),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return ended


def main():
    parser = argparse.ArgumentParser(
        description="Sync the Online Boutique app to its current Git revision."
    )
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    started = utc_now()

    try:
        key = {
            "pk": {"S": f"DRIFT#{args.instance_id}"},
            "sk": {"S": f"TRIAL#{args.trial_id}"},
        }
        item = run_json(
            [
                "aws",
                "dynamodb",
                "get-item",
                "--table-name",
                TABLE,
                "--region",
                REGION,
                "--key",
                json.dumps(key),
            ]
        ).get("Item")

        if not item:
            raise RuntimeError("No matching trial record exists in the ledger")

        target = {
            "kind": item["kind"]["S"],
            "name": item["name"]["S"],
            "namespace": item["namespace"]["S"],
        }

        app = kubectl_json("get", "application", APP, "-n", APP_NAMESPACE)
        revision = app.get("status", {}).get("sync", {}).get("revision")
        if not revision:
            raise RuntimeError("Argo CD Application has no synced Git revision")

        operation = {
            "operation": {
                "sync": {
                    "revision": revision,
                    "prune": False,
                }
            }
        }
        subprocess.run(
            [
                "kubectl",
                "patch",
                "application",
                APP,
                "-n",
                APP_NAMESPACE,
                "--type=merge",
                "-p",
                json.dumps(operation),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        deadline = time.monotonic() + args.timeout
        clean = False

        while time.monotonic() < deadline:
            current = kubectl_json(
                "get", "application", APP, "-n", APP_NAMESPACE
            )
            state = current.get("status", {})
            operation_state = state.get("operationState", {})
            op_started = operation_state.get("startedAt")

            operation_is_new = bool(
                op_started
                and datetime.fromisoformat(
                    op_started.replace("Z", "+00:00")
                )
                >= started
            )

            resources = state.get("resources", [])
            target_status = next(
                (
                    resource.get("status")
                    for resource in resources
                    if resource.get("kind") == target["kind"]
                    and resource.get("name") == target["name"]
                    and resource.get("namespace") == target["namespace"]
                ),
                None,
            )

            if operation_is_new and operation_state.get("phase") == "Failed":
                raise RuntimeError(
                    operation_state.get("message", "Argo CD sync failed")
                )

            if (
                operation_is_new
                and operation_state.get("phase") == "Succeeded"
                and state.get("sync", {}).get("status") == "Synced"
                and state.get("health", {}).get("status") == "Healthy"
                and target_status == "Synced"
            ):
                clean = True
                break

            time.sleep(5)

        if not clean:
            raise RuntimeError(
                "Timed out waiting for a new successful sync, "
                "Synced/Healthy app status, and Synced target resource"
            )

        ended = update_ledger(
            args.instance_id, args.trial_id, "CLEAN", started
        )
        print(
            json.dumps(
                {
                    "reset_status": "CLEAN",
                    "target": target,
                    "revision": revision,
                    "duration_seconds": round(
                        (ended - started).total_seconds(), 2
                    ),
                }
            )
        )

    except Exception as exc:
        try:
            update_ledger(
                args.instance_id,
                args.trial_id,
                "FAILED",
                started,
                str(exc),
            )
        except Exception as log_error:
            print(
                f"Could not write reset result to ledger: {log_error}",
                file=sys.stderr,
            )

        print(f"Reset failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
