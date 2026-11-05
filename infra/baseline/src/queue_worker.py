"""Poll the isolated baseline queue from an EKS pod and run deterministic handling."""

from __future__ import annotations

import json
import os
import time

import boto3
from handler import handler

REGION = os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
QUEUE_URL = os.environ["BASELINE_QUEUE_URL"]
POLL_SECONDS = 20


def main() -> None:
    sqs = boto3.client("sqs", region_name=REGION)
    while True:
        response = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=POLL_SECONDS,
            VisibilityTimeout=300,
            AttributeNames=["ApproximateReceiveCount"],
        )
        for message in response.get("Messages", []):
            message_id = message.get("MessageId", "")
            result = handler(
                {
                    "Records": [
                        {"messageId": message_id, "body": message.get("Body", "")}
                    ]
                }
            )
            if result.get("batchItemFailures"):
                print(
                    json.dumps(
                        {"event": "baseline_retry", "message_id": message_id}
                    )
                )
                continue
            sqs.delete_message(
                QueueUrl=QUEUE_URL,
                ReceiptHandle=message["ReceiptHandle"],
            )


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "event": "baseline_worker_error",
                        "error_type": type(exc).__name__,
                    }
                )
            )
            time.sleep(5)
