"""Poll the collector queue from the in-cluster pod and enrich accepted events."""

from __future__ import annotations

import json
import os
import time

import boto3
from collector import handler

REGION = os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
QUEUE_URL = os.environ["COLLECTOR_QUEUE_URL"]


def run_forever() -> None:
    sqs = boto3.client("sqs", region_name=REGION)
    while True:
        response = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=90,
        )
        for message in response.get("Messages", []):
            message_id = message.get("MessageId", "")
            try:
                event = json.loads(message.get("Body", ""))
                if not isinstance(event, dict):
                    raise TypeError("queued event must be a JSON object")
                result = handler(event)
                status = int(result.get("statusCode", 500))
                # The handler quarantines upstream failures and rejects bad inputs
                # with a client status. Both are terminal outcomes for this delivery.
                if status < 500:
                    sqs.delete_message(
                        QueueUrl=QUEUE_URL,
                        ReceiptHandle=message["ReceiptHandle"],
                    )
                else:
                    print(
                        json.dumps(
                            {
                                "event": "collector_retry",
                                "message_id": message_id,
                                "status": status,
                            }
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                print(
                    json.dumps(
                        {
                            "event": "collector_message_failed",
                            "message_id": message_id,
                            "error_type": type(exc).__name__,
                        }
                    )
                )
            time.sleep(0)


def run_supervised() -> None:
    """Restart the consumer loop after an unexpected AWS or worker failure."""
    while True:
        try:
            run_forever()
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "event": "collector_worker_error",
                        "error_type": type(exc).__name__,
                    }
                )
            )
            time.sleep(5)


if __name__ == "__main__":
    run_supervised()
