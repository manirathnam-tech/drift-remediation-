"""Runtime composition for the SQS treatment worker."""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3
from audit_store import make_audit_writer
from bedrock_model import BedrockJSONModel, make_classifier, make_proposal_generator
from evidence_retrieval import make_evidence_retriever
from golden_store import load_golden_manifest
from queue_worker import run_forever
from treatment import run_treatment

VALIDATOR_SRC = Path(__file__).resolve().parents[2] / "validator" / "src"
sys.path.insert(0, str(VALIDATOR_SRC))
from validate_patch import validate_rendered_snapshot

LOGGER = logging.getLogger("drift.orchestrator")


def build_processor() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build dependencies once and return the per-event treatment callback."""
    queue_url = os.environ.get("TREATMENT_QUEUE_URL")
    if not queue_url:
        raise ValueError("TREATMENT_QUEUE_URL is required")

    evidence_provider = make_evidence_retriever()
    model = BedrockJSONModel()
    audit = make_audit_writer()
    classifier = make_classifier(model, repair=True, audit=audit)
    proposal_generator = make_proposal_generator(model)
    def process(event: dict[str, Any]) -> dict[str, Any]:
        def patch_gate(patch: str, golden: dict[str, Any], revision: str) -> list[str]:
            report = {}
            errors = validate_rendered_snapshot(
                patch, golden["source_archive"], revision, golden["source_sha256"],
                report=report,
            )
            audit({
                "event_id": event["event_id"], "stage": "patch_validation",
                "verdict": report["status"], "revision": revision,
                "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
                **report,
            })
            return errors

        return run_treatment(
            event,
            load_golden=load_golden_manifest,
            retrieve_evidence=evidence_provider,
            classify=classifier,
            generate_proposal=proposal_generator,
            patch_gate=patch_gate,
            audit=audit,
        )

    return process


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    queue_url = os.environ.get("TREATMENT_QUEUE_URL")
    if not queue_url:
        raise ValueError("TREATMENT_QUEUE_URL is required")
    region = os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
    sqs = boto3.client("sqs", region_name=region)
    processor = build_processor()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    LOGGER.info("Treatment worker started; queue polling enabled")
    run_forever(sqs, queue_url, processor, stop)
    LOGGER.info("Treatment worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
