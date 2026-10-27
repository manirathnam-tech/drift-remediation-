#!/usr/bin/env python3
"""Run 20 guarded replica-drift trials and measure Argo CD detection latency."""

import csv
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


IMAGE = (
    "218733194774.dkr.ecr.ap-southeast-2.amazonaws.com/"
    "drift-thesis-injector@sha256:"
    "12301d7bc5e832b30d307b8f89465b933fe2f8221b067a742a9dce2f14db2c32"
)
NAMESPACE = "online-boutique"
APP = "online-boutique"
COUNT = 20
TIMEOUT_SECONDS = 180
RESULTS = Path("harness/results")
CSV_PATH = RESULTS / "drift23-detection-20261026.csv"
SVG_PATH = RESULTS / "drift23-detection-20261026.svg"


def command(args, *, input_text=None, check=True):
    return subprocess.run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
    )


def kubectl_json(*args):
    result = command(["kubectl", *args, "-o", "json"])
    return json.loads(result.stdout)


def write_plot(rows):
    width, height = 900, 420
    left, top, bottom = 70, 35, 65
    plot_height = height - top - bottom
    max_ms = max(30000, max(row["latency_ms"] for row in rows))
    bar_width = 24
    gap = 14
    bars = []

    for index, row in enumerate(rows):
        x = left + index * (bar_width + gap)
        bar_height = row["latency_ms"] / max_ms * plot_height
        y = top + plot_height - bar_height
        bars.append(
            f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" '
            f'height="{bar_height:.1f}" fill="#2878b5"/>'
            f'<text x="{x + bar_width / 2}" y="{height - 38}" '
            f'text-anchor="middle" font-size="11">{index + 1}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}">'
        f'<text x="{width / 2}" y="20" text-anchor="middle" '
        f'font-size="16">Argo CD detection latency, 20 trials</text>'
        f'<text x="18" y="{height / 2}" transform="rotate(-90 18 {height / 2})" '
        f'text-anchor="middle" font-size="12">T_detect − T_inject (ms)</text>'
        f'<line x1="{left}" y1="{top + plot_height}" '
        f'x2="{width - 20}" y2="{top + plot_height}" stroke="black"/>'
        + "".join(bars)
        + f'<text x="{width / 2}" y="{height - 10}" text-anchor="middle" '
        f'font-size="12">Trial</text></svg>'
    )
    SVG_PATH.write_text(svg, encoding="utf-8")


def main():
    global COUNT

    RESULTS.mkdir(parents=True, exist_ok=True)

    records = [
        json.loads(line)
        for line in Path("harness/corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    eligible = sorted(
        (
            record
            for record in records
            if record.get("kind") == "Deployment"
            and any(
                patch.get("path") == "/spec/replicas"
                and patch.get("value") == 0
                for patch in record.get("mutation", [])
            )
        ),
        key=lambda record: record["id"],
    )
    if not eligible:
        raise SystemExit("No eligible Deployment replica mutations found")

    trials = [eligible[index % len(eligible)] for index in range(COUNT)]
    previous_instance = "SYN-20261022-0004"
    previous_trial = "drift22-guard-accept-20261026-01"
    rows = []

    for index, record in enumerate(trials, start=1):
        job_name = f"drift23-detect-{index:02d}"
        trial_id = f"drift23-20261026-{index:02d}"

        app = kubectl_json("get", "application", APP, "-n", "argocd")
        app_status = app.get("status", {})
        if (
            app_status.get("sync", {}).get("status") != "Synced"
            or app_status.get("health", {}).get("status") != "Healthy"
        ):
            raise SystemExit("App is not Synced/Healthy before trial; stopping")

        job = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name, "namespace": NAMESPACE},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": 180,
                "ttlSecondsAfterFinished": 3600,
                "template": {
                    "spec": {
                        "serviceAccountName": "drift-injector",
                        "restartPolicy": "Never",
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 10001,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [{
                            "name": "injector",
                            "image": IMAGE,
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {"name": "INSTANCE_ID", "value": record["id"]},
                                {"name": "TRIAL_ID", "value": trial_id},
                                {"name": "TRIGGER", "value": "kubernetes-job"},
                                {"name": "LEDGER_TABLE", "value": "drift-thesis-experiment-ledger"},
                                {"name": "PREVIOUS_INSTANCE_ID", "value": previous_instance},
                                {"name": "PREVIOUS_TRIAL_ID", "value": previous_trial},
                            ],
                        }],
                    }
                },
            },
        }

        command(["kubectl", "apply", "-f", "-"], input_text=json.dumps(job))
        wait_result = command(
            [
                "kubectl", "wait", "--for=condition=complete",
                f"job/{job_name}", "-n", NAMESPACE, "--timeout=240s",
            ],
            check=False,
        )
        logs = command(
            ["kubectl", "logs", f"job/{job_name}", "-n", NAMESPACE],
            check=False,
        ).stdout

        if wait_result.returncode != 0:
            raise SystemExit(f"Injection job failed ({job_name}):\n{logs}")

        result = next(
            (
                json.loads(line)
                for line in logs.splitlines()
                if line.startswith("{") and '"status": "injected"' in line
            ),
            None,
        )
        if not result:
            raise SystemExit(f"No injection result found in {job_name} logs:\n{logs}")

        injected_at = datetime.fromisoformat(
            result["t_inject"].replace("Z", "+00:00")
        ).timestamp() * 1000

        detected_at = None
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status = kubectl_json(
                "get", "application", APP, "-n", "argocd"
            ).get("status", {})
            if status.get("sync", {}).get("status") == "OutOfSync":
                detected_at = time.time_ns() // 1_000_000
                break
            time.sleep(1)

        row = {
            "trial": index,
            "instance_id": record["id"],
            "trial_id": trial_id,
            "t_inject": result["t_inject"],
            "t_detect_epoch_ms": detected_at or "",
            "latency_ms": round(detected_at - injected_at) if detected_at else "",
            "result": "detected" if detected_at else "timeout",
        }
        rows.append(row)

        # Always reset after a successful injection, including a detection timeout.
        reset = command(
            [
                "python3", "harness/reset.py",
                "--instance-id", record["id"],
                "--trial-id", trial_id,
            ]
        )
        reset_result = json.loads(reset.stdout)
        if reset_result.get("reset_status") != "CLEAN":
            raise SystemExit(f"Reset did not finish cleanly: {reset.stdout}")

        previous_instance = record["id"]
        previous_trial = trial_id

        with CSV_PATH.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        print(json.dumps({**row, "reset_seconds": reset_result["duration_seconds"]}))

        if not detected_at:
            raise SystemExit(
                f"Argo did not report OutOfSync within {TIMEOUT_SECONDS}s; "
                "trial was reset and the campaign stopped"
            )

    write_plot(rows)
    latencies = sorted(row["latency_ms"] for row in rows)
    print(f"Completed {len(rows)} detections")
    print(f"Median latency: {latencies[len(latencies) // 2]} ms")
    print(f"CSV: {CSV_PATH}")
    print(f"Plot: {SVG_PATH}")


if __name__ == "__main__":
    main()
