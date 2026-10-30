#!/usr/bin/env python3
"""Wait for an Argo CD target to converge and record the trial timestamp."""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime

REGION = "ap-southeast-2"
TABLE = "drift-thesis-experiment-ledger"
APP = "online-boutique"
APP_NAMESPACE = "argocd"


def timestamp(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run_json(args):
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def get_application():
    return run_json(
        [
            "kubectl",
            "get",
            "application",
            APP,
            "-n",
            APP_NAMESPACE,
            "-o",
            "json",
        ]
    )


def is_converged(application, target):
    status = application.get("status", {})
    if (
        status.get("sync", {}).get("status") != "Synced"
        or status.get("health", {}).get("status") != "Healthy"
    ):
        return False

    return any(
        resource.get("kind") == target["kind"]
        and resource.get("name") == target["name"]
        and resource.get("namespace") == target["namespace"]
        and resource.get("status") == "Synced"
        and (
               not resource.get("health")
               or resource["health"].get("status") == "Healthy"
            )
        for resource in status.get("resources", [])
    )


def update_ledger(instance_id, trial_id, started, converged_at=None, error=None):
    values = {
        ":start": {"S": timestamp(started)},
    }
    expression = "SET t_convergence_start=:start"

    if converged_at is not None:
        duration_ms = round((converged_at - started).total_seconds() * 1000)
        values[":end"] = {"S": timestamp(converged_at)}
        values[":duration"] = {"N": str(duration_ms)}
        values[":status"] = {"S": "CONVERGED"}
        expression += (
            ", t_converged=:end, convergence_duration_ms=:duration, "
            "convergence_status=:status"
        )

    if error:
        values[":status"] = {"S": "TIMED_OUT"}
        values[":error"] = {"S": error[:900]}
        expression += ", convergence_status=:status, convergence_error=:error"

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--poll-interval", type=int, default=5)
    args = parser.parse_args()

    started = datetime.now(UTC)

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

        if is_converged(get_application(), target):
            raise RuntimeError(
                "Target is already Synced and Healthy; start while drift is active"
            )

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            application = get_application()
            if is_converged(application, target):
                converged_at = datetime.now(UTC)
                update_ledger(
                    args.instance_id,
                    args.trial_id,
                    started,
                    converged_at=converged_at,
                )
                print(
                    json.dumps(
                        {
                            "status": "CONVERGED",
                            "target": target,
                            "t_converged": timestamp(converged_at),
                            "duration_seconds": round(
                                (converged_at - started).total_seconds(), 2
                            ),
                        }
                    )
                )
                return

            time.sleep(args.poll_interval)

        message = f"Timed out after {args.timeout} seconds waiting for convergence"
        update_ledger(args.instance_id, args.trial_id, started, error=message)
        raise RuntimeError(message)

    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"Convergence monitor failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
