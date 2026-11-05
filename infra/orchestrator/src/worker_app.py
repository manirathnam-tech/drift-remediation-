"""Runtime composition for the SQS treatment worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
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
from workflow_stages import classify_stage, synthesize_stage, validate_stage
from workflow_state import load_checkpoint, load_event, save_checkpoint

VALIDATOR_SRC = Path(__file__).resolve().parents[2] / "validator" / "src"
sys.path.insert(0, str(VALIDATOR_SRC))
from validate_patch import validate_rendered_snapshot

LOGGER = logging.getLogger("drift.orchestrator")


def build_processor(*, require_queue: bool = True) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build dependencies once and return the per-event treatment callback."""
    queue_url = os.environ.get("TREATMENT_QUEUE_URL")
    if require_queue and not queue_url:
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


def run_workflow_event() -> dict[str, Any]:
    """Process one S3-backed event for an on-demand workflow task."""
    input_key = os.environ.get("WORKFLOW_INPUT_KEY", "")
    state_key = os.environ.get("WORKFLOW_STATE_KEY", "")
    input_sha256 = os.environ.get("WORKFLOW_INPUT_SHA256", "")
    if not input_key or not state_key or not input_sha256:
        raise ValueError("workflow input, state, and checksum references are required")

    checkpoint, etag = load_checkpoint(state_key)
    if (
        checkpoint.get("input_key") != input_key
        or checkpoint.get("input_sha256") != input_sha256
    ):
        raise ValueError("workflow task references do not match the S3 checkpoint")
    if checkpoint.get("stage") == "COMPLETED":
        previous_result = checkpoint.get("result", {})
        return {
            "status": previous_result.get("status", "COMPLETED")
            if isinstance(previous_result, dict)
            else "COMPLETED",
            "decision": previous_result.get("decision")
            if isinstance(previous_result, dict)
            else None,
        }
    attempt_count = checkpoint.get("attempt_count", 0)
    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int):
        raise TypeError("workflow checkpoint attempt_count is invalid")
    if checkpoint.get("stage") not in {"RECEIVED", "FAILED"} or attempt_count >= 3:
        raise ValueError("workflow event is already being processed or needs recovery")

    event = load_event(input_key, input_sha256)
    started_at = int(time.time())
    checkpoint.update(
        {
            "stage": "RUNNING",
            "attempt_count": attempt_count + 1,
            "started_at": started_at,
        }
    )
    checkpoint.pop("failure_type", None)
    etag = save_checkpoint(
        state_key,
        checkpoint,
        next_stage="ProcessTreatment",
        expected_etag=etag,
    )

    try:
        result = build_processor(require_queue=False)(event)
    except Exception as exc:
        checkpoint.update(
            {
                "stage": "FAILED",
                "completed_at": int(time.time()),
                "failure_type": type(exc).__name__,
            }
        )
        save_checkpoint(
            state_key,
            checkpoint,
            next_stage="Escalate",
            expected_etag=etag,
        )
        raise
    checkpoint.update(
        {
            "stage": "COMPLETED",
            "completed_at": int(time.time()),
            "result": result,
        }
    )
    save_checkpoint(
        state_key,
        checkpoint,
        next_stage="Done",
        expected_etag=etag,
    )
    return {
        "status": result.get("status", "UNKNOWN"),
        "decision": result.get("decision"),
    }


def run_workflow_stage(stage: str) -> None:
    """Execute one independently checkpointed stage and callback to Step Functions."""
    allowed_stages = {"Classify", "Synthesize", "Validate", "Route"}
    if stage not in allowed_stages:
        raise ValueError("unsupported workflow stage")
    token = os.environ.get("TASK_TOKEN", "")
    input_key = os.environ.get("WORKFLOW_INPUT_KEY", "")
    state_key = os.environ.get("WORKFLOW_STATE_KEY", "")
    input_sha256 = os.environ.get("WORKFLOW_INPUT_SHA256", "")
    if not all((token, input_key, state_key, input_sha256)):
        raise ValueError("workflow stage requires task token and S3 references")

    sfn = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION_NAME"))
    try:
        checkpoint, etag = load_checkpoint(state_key)
        if checkpoint.get("input_key") != input_key or checkpoint.get("input_sha256") != input_sha256:
            raise ValueError("workflow references do not match the checkpoint")
        completed_checkpoint_stages = {
            "Classify": {"CLASSIFIED", "ESCALATED"},
            "Synthesize": {"PROPOSED"},
            "Validate": {"RETRY_READY", "VALIDATED"},
            "Route": {"ROUTED"},
        }
        if (
            checkpoint.get("last_completed_stage") == stage
            and checkpoint.get("stage") in completed_checkpoint_stages[stage]
        ):
            previous_callback = checkpoint.get("last_stage_callback")
            if not isinstance(previous_callback, dict):
                raise ValueError("completed workflow stage has no saved callback result")
            sfn.send_task_success(
                taskToken=token,
                output=json.dumps(previous_callback, separators=(",", ":")),
            )
            return
        expected_source_stage = {
            "Classify": {"RECEIVED"},
            "Synthesize": {"CLASSIFIED", "RETRY_READY"},
            "Validate": {"PROPOSED"},
            "Route": {"VALIDATED"},
        }[stage]
        if checkpoint.get("stage") not in expected_source_stage:
            raise ValueError("workflow checkpoint is not ready for this stage")
        event = load_event(input_key, input_sha256)
        audit = make_audit_writer()

        last_validation_report: dict[str, Any] | None = None

        def patch_gate(patch: str, golden: dict[str, Any], revision: str) -> list[str]:
            nonlocal last_validation_report
            report: dict[str, Any] = {}
            errors = validate_rendered_snapshot(
                patch, golden["source_archive"], revision, golden["source_sha256"], report=report
            )
            last_validation_report = report
            audit({
                "event_id": event["event_id"],
                "stage": "patch_validation",
                "verdict": report.get("status", "ERROR"),
                "revision": revision,
                "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
                **report,
            })
            return errors

        if stage == "Classify":
            model = BedrockJSONModel()
            result = classify_stage(
                event,
                load_golden=load_golden_manifest,
                retrieve_evidence=make_evidence_retriever(),
                classify=make_classifier(model, repair=True, audit=audit),
                audit=audit,
            )
            if result.get("status") != "READY":
                checkpoint.update({"stage": "ESCALATED", "escalation": result})
                callback = {"status": "ESCALATE", "reason": result.get("reason", "classification requires review")}
            else:
                checkpoint.update({
                    "stage": "CLASSIFIED",
                    "classification": result["classification"],
                    "evidence": result["evidence"],
                    "decision": result["decision"],
                })
                callback = {"status": "READY", "decision": result["decision"]}
            next_stage = "Synthesize" if callback["status"] == "READY" else "Escalate"
        elif stage == "Synthesize":
            model = BedrockJSONModel()
            feedback = checkpoint.get("feedback", [])
            if not isinstance(feedback, list) or any(not isinstance(item, str) for item in feedback):
                raise ValueError("workflow retry feedback is invalid")
            proposal = synthesize_stage(
                event,
                checkpoint["classification"],
                checkpoint["evidence"],
                feedback,
                load_golden=load_golden_manifest,
                generate_proposal=make_proposal_generator(model),
            )
            checkpoint.update({"stage": "PROPOSED", "proposal": proposal})
            callback = {"status": "PROPOSED"}
            next_stage = "Validate"
        elif stage == "Validate":
            revision = event["revision"]
            golden = load_golden_manifest(revision)
            proposal = checkpoint.get("proposal")
            errors = validate_stage(
                proposal,
                checkpoint["classification"],
                golden,
                revision,
                patch_gate=patch_gate,
            )
            proposal_patch = proposal.get("patch") if isinstance(proposal, dict) else None
            patch_sha256 = (
                hashlib.sha256(proposal_patch.encode("utf-8")).hexdigest()
                if isinstance(proposal_patch, str)
                else ""
            )
            if last_validation_report is None:
                last_validation_report = {
                    "status": "FAIL" if errors else "PASS",
                    "checks": {
                        "source": "SKIP",
                        "git_apply": "SKIP",
                        "render": "SKIP",
                        "kubeconform": "SKIP",
                        "conftest": "SKIP",
                        "checkov": "SKIP",
                        "server_side": "SKIP",
                    },
                    "reasons": errors,
                    "duration_ms": 0,
                }
            validation_report = {
                **last_validation_report,
                "revision": revision,
                "patch_sha256": patch_sha256,
            }
            checkpoint["validation_report"] = validation_report
            if errors:
                retries = checkpoint.get("retry_count", 0)
                if isinstance(retries, bool) or not isinstance(retries, int):
                    raise ValueError("workflow retry counter is invalid")
                checkpoint.update({
                    "stage": "RETRY_READY",
                    "retry_count": retries + 1,
                    "feedback": errors[:20],
                    "proposal": None,
                })
                callback = {"status": "INVALID", "retry_count": retries + 1}
                next_stage = "Synthesize"
            else:
                checkpoint.update({"stage": "VALIDATED", "feedback": []})
                callback = {"status": "VALIDATED", "decision": checkpoint["proposal"]["decision"]}
                next_stage = "Route"
        else:
            proposal = checkpoint.get("proposal")
            if checkpoint.get("stage") != "VALIDATED" or not isinstance(proposal, dict):
                raise ValueError("only validated proposals may be routed")
            patch = proposal.get("patch")
            if not isinstance(patch, str):
                raise ValueError("validated proposal has no patch")
            patch_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
            checkpoint.update({"stage": "ROUTED", "proposal_patch_sha256": patch_digest})
            make_audit_writer()({
                "event_id": event["event_id"],
                "stage": "proposal_routing",
                "verdict": "ROUTED",
                "decision": proposal["decision"],
                "patch_sha256": patch_digest,
            })
            callback = {
                "status": "ROUTED",
                "decision": proposal["decision"],
                "event_id": event["event_id"],
                "input_key": input_key,
                "input_sha256": input_sha256,
                "state_key": state_key,
                "patch_sha256": patch_digest,
            }
            next_stage = "RouteProposal"

        checkpoint.update(
            {
                "last_completed_stage": stage,
                "last_stage_callback": callback,
            }
        )
        save_checkpoint(state_key, checkpoint, next_stage=next_stage, expected_etag=etag)
        sfn.send_task_success(taskToken=token, output=json.dumps(callback, separators=(",", ":")))
    except Exception as exc:
        try:
            sfn.send_task_failure(
                taskToken=token,
                error=type(exc).__name__[:256],
                cause="Treatment stage failed; inspect the bounded workflow failure record.",
            )
        except Exception:
            LOGGER.exception("Could not report workflow stage failure")
        raise


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(description="Run a DRIFT treatment worker")
    parser.add_argument(
        "--workflow-event",
        action="store_true",
        help="process one event using S3 workflow references instead of polling SQS",
    )
    parser.add_argument(
        "--workflow-stage",
        choices=("Classify", "Synthesize", "Validate", "Route"),
        help="run one callback-based Step Functions stage",
    )
    args = parser.parse_args(argv)
    if args.workflow_stage:
        run_workflow_stage(args.workflow_stage)
        return 0
    if args.workflow_event:
        result = run_workflow_event()
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0

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
