"""Long-poll treatment messages and acknowledge only durable final outcomes."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

LOGGER = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 256 * 1024
POLL_WAIT_SECONDS = 20
PROCESSING_VISIBILITY_SECONDS = 900
FINAL_STATUSES = {"VALIDATED", "ESCALATE"}

EventProcessor = Callable[[dict[str, Any]], dict[str, Any]]


def run_once(sqs: Any, queue_url: str, process_event: EventProcessor) -> bool:
    """Receive and process at most one event; return whether a message was found."""
    response = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=POLL_WAIT_SECONDS,
        VisibilityTimeout=PROCESSING_VISIBILITY_SECONDS,
        AttributeNames=["ApproximateReceiveCount"],
    )
    messages = response.get("Messages", [])
    if not messages:
        return False

    message = messages[0]
    receipt_handle = message["ReceiptHandle"]
    event_id = "unknown"
    try:
        body = message.get("Body", "")
        if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("treatment message exceeds the 256 KiB limit")
        event = json.loads(body)
        if not isinstance(event, dict):
            raise TypeError("treatment message must be a JSON object")
        event_id = str(event.get("event_id") or "unknown")
        result = process_event(event)
        if result.get("status") not in FINAL_STATUSES:
            raise ValueError("processor returned no durable final treatment status")
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        LOGGER.error(
            json.dumps(
                {
                    "event": "treatment_message_failed",
                    "event_id": event_id,
                    "reason": str(exc),
                    "receive_count": message.get("Attributes", {}).get(
                        "ApproximateReceiveCount", "unknown"
                    ),
                },
                separators=(",", ":"),
            )
        )
        return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.error(
            json.dumps(
                {
                    "event": "treatment_dependency_failed",
                    "event_id": event_id,
                    "reason": type(exc).__name__,
                },
                separators=(",", ":"),
            )
        )
        return True

    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    LOGGER.info(
        json.dumps(
            {
                "event": "treatment_message_completed",
                "event_id": event_id,
                "status": result["status"],
                "decision": result.get("decision"),
            },
            separators=(",", ":"),
        )
    )
    return True


def run_forever(
    sqs: Any,
    queue_url: str,
    process_event: EventProcessor,
    stop: Any,
) -> None:
    """Long-poll until the supplied event-like stop signal is set."""
    while not stop.is_set():
        run_once(sqs, queue_url, process_event)
