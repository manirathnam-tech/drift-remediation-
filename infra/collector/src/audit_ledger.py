"""Reusable writer for immutable drift audit events."""

from __future__ import annotations


def record_event(table, event: dict, created_at: int) -> None:
    """Store the enriched event in the durable audit ledger."""
    table.put_item(
        Item={
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "schema_version": event["schema_version"],
            "application_name": event["application"]["name"],
            "application_namespace": event["application"]["namespace"],
            "revision": event["revision"],
            "t_detect": event["t_detect"],
            "reconciled_at": event["reconciled_at"],
            "received_at": event["received_at"],
            "resources": event["resources"],
            "status": "ACCEPTED",
            "created_at": created_at,
        }
    )
