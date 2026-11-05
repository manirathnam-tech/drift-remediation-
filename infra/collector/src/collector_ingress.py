"""Non-VPC API Gateway relay that durably queues the original webhook event."""

from __future__ import annotations

import json
import os

import boto3

_sqs = None


def handler(event: dict, context=None) -> dict:
    del context
    global _sqs
    try:
        body = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        if len(body.encode("utf-8")) > 120 * 1024:
            return {
                "statusCode": 413,
                "headers": {"content-type": "application/json"},
                "body": '{"error":"request too large"}',
            }
        if _sqs is None:
            _sqs = boto3.client(
                "sqs", region_name=os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
            )
        _sqs.send_message(QueueUrl=os.environ["COLLECTOR_QUEUE_URL"], MessageBody=body)
        return {
            "statusCode": 202,
            "headers": {"content-type": "application/json"},
            "body": '{"status":"queued"}',
        }
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"event": "collector_enqueue_failed", "error_type": type(exc).__name__}))
        return {
            "statusCode": 503,
            "headers": {"content-type": "application/json"},
            "body": '{"error":"temporarily unavailable"}',
        }
