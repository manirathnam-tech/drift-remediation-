"""Provider-neutral, fail-closed treatment flow for one enriched DriftEvent."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from contracts import (
    decision_for_classification,
    validate_classification,
    validate_proposal,
)
from errors import TreatmentDependencyError
from retry import generate_with_bounded_retries

JsonObject = dict[str, Any]
Evidence = list[JsonObject]
AuditWriter = Callable[[JsonObject], None]
GoldenLoader = Callable[[str], JsonObject]
EvidenceRetriever = Callable[[JsonObject, JsonObject], Evidence]
Classifier = Callable[[JsonObject, JsonObject, Evidence], JsonObject]
ProposalGenerator = Callable[
    [JsonObject, JsonObject, Evidence, JsonObject, list[str]], JsonObject
]
PatchGate = Callable[[str, JsonObject, str], list[str]]


def _audit(audit: AuditWriter, event_id: str, **fields: Any) -> None:
    audit({"event_id": event_id, **fields})


def _checkout_revision(checkout: str | Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode:
        raise TreatmentDependencyError("could not read GitOps checkout revision")
    return result.stdout.strip()


def _escalate(
    audit: AuditWriter,
    event_id: str,
    reason: str,
    *,
    classification: JsonObject | None = None,
    attempts: list[JsonObject] | None = None,
) -> JsonObject:
    result = {
        "status": "ESCALATE",
        "decision": "escalate",
        "reason": reason,
        "classification": classification,
        "proposal": None,
        "attempts": attempts or [],
    }
    _audit(audit, event_id, stage="treatment", verdict="ESCALATE", reason=reason)
    return result


def run_treatment(
    event: JsonObject,
    *,
    gitops_checkout: str | Path | None = None,
    load_golden: GoldenLoader,
    retrieve_evidence: EvidenceRetriever,
    classify: Classifier,
    generate_proposal: ProposalGenerator,
    patch_gate: PatchGate,
    audit: AuditWriter,
    max_retries: int = 2,
) -> JsonObject:
    """Classify, propose, and independently validate; never apply or publish patches.

    Adapters must raise `TreatmentDependencyError` for expected provider or
    retrieval failures. Every classifier verdict, proposal attempt, and final
    decision is sent to the caller's durable audit writer.
    """
    event_id = event.get("event_id")
    revision = event.get("revision")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event.event_id is required")
    if not isinstance(revision, str) or not revision:
        return _escalate(audit, event_id, "event revision is missing")

    if gitops_checkout is not None:
        try:
            checked_out_revision = _checkout_revision(gitops_checkout)
        except (OSError, subprocess.TimeoutExpired, TreatmentDependencyError) as exc:
            _audit(
                audit,
                event_id,
                stage="revision_check",
                verdict="ERROR",
                reasons=[type(exc).__name__],
            )
            return _escalate(
                audit,
                event_id,
                f"cannot confirm GitOps revision: {type(exc).__name__}",
            )
        if checked_out_revision != revision:
            _audit(
                audit,
                event_id,
                stage="revision_check",
                verdict="REJECT",
                reasons=["GitOps checkout does not match the event revision"],
            )
            return _escalate(
                audit,
                event_id,
                "GitOps checkout does not match the event revision",
            )

    try:
        golden = load_golden(revision)
    except TreatmentDependencyError as exc:
        _audit(
            audit,
            event_id,
            stage="golden_manifest",
            verdict="ERROR",
            reasons=[type(exc).__name__],
        )
        return _escalate(
            audit,
            event_id,
            f"golden manifest lookup failed: {type(exc).__name__}",
        )
    manifest_text = golden.get("rendered_yaml") if isinstance(golden, dict) else None
    golden_digest = golden.get("sha256") if isinstance(golden, dict) else None
    metadata = golden.get("metadata") if isinstance(golden, dict) else None
    source_archive = golden.get("source_archive") if isinstance(golden, dict) else None
    source_digest = golden.get("source_sha256") if isinstance(golden, dict) else None
    golden_valid = (
        isinstance(manifest_text, str)
        and bool(manifest_text.strip())
        and isinstance(golden_digest, str)
        and hashlib.sha256(manifest_text.encode("utf-8")).hexdigest() == golden_digest
        and isinstance(metadata, dict)
        and metadata.get("revision") == revision
        and golden.get("revision") == revision
        and metadata.get("repository") == "manirathnam-tech/drift-gitops"
        and isinstance(source_archive, bytes)
        and isinstance(source_digest, str)
        and hashlib.sha256(source_archive).hexdigest() == source_digest
    )
    if not golden_valid:
        _audit(
            audit,
            event_id,
            stage="golden_manifest",
            verdict="REJECT",
            reasons=["golden artifact identity or checksum is invalid"],
        )
        return _escalate(
            audit,
            event_id,
            "golden artifact identity or checksum is invalid",
        )
    _audit(
        audit,
        event_id,
        stage="golden_manifest",
        verdict="PASS",
        sha256=golden_digest,
        source_sha256=source_digest,
    )

    try:
        evidence = retrieve_evidence(event, golden)
    except TreatmentDependencyError as exc:
        _audit(
            audit,
            event_id,
            stage="evidence_retrieval",
            verdict="ERROR",
            reasons=[type(exc).__name__],
        )
        return _escalate(
            audit,
            event_id,
            f"evidence lookup failed: {type(exc).__name__}",
        )

    if not isinstance(evidence, list):
        _audit(
            audit,
            event_id,
            stage="evidence_retrieval",
            verdict="REJECT",
            reasons=["evidence adapter returned an invalid package"],
        )
        return _escalate(audit, event_id, "evidence adapter returned an invalid package")
    evidence_ids = {
        item.get("evidence_id")
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
    }
    if len(evidence_ids) != len(evidence):
        _audit(
            audit,
            event_id,
            stage="evidence_retrieval",
            verdict="REJECT",
            reasons=["evidence package contains a missing or duplicate ID"],
        )
        return _escalate(audit, event_id, "evidence package contains a missing or duplicate ID")
    _audit(
        audit,
        event_id,
        stage="evidence_retrieval",
        verdict="PASS",
        evidence_ids=sorted(evidence_ids),
        evidence_sources=[
            {
                "evidence_id": item["evidence_id"],
                "source": item.get("source", ""),
                "excerpt": item.get("excerpt", ""),
            }
            for item in evidence
        ],
    )

    try:
        classification = classify(event, golden, evidence)
    except TreatmentDependencyError as exc:
        _audit(
            audit,
            event_id,
            stage="classification",
            verdict="ERROR",
            reasons=[type(exc).__name__],
        )
        return _escalate(
            audit,
            event_id,
            f"classification provider failed: {type(exc).__name__}",
        )

    classification_errors = validate_classification(classification)
    if not classification_errors:
        referenced = set(classification["evidence_refs"])
        if not referenced.issubset(evidence_ids):
            classification_errors.append("classification cites evidence outside the supplied package")
    if classification_errors:
        _audit(
            audit,
            event_id,
            stage="classification",
            verdict="REJECT",
            reasons=classification_errors,
            output=classification,
        )
        return _escalate(
            audit,
            event_id,
            "classification failed its schema or evidence checks",
            attempts=[
                {
                    "stage": "classification",
                    "verdict": "REJECT",
                    "reasons": classification_errors,
                }
            ],
        )

    expected_decision = decision_for_classification(classification)
    _audit(
        audit,
        event_id,
        stage="classification",
        verdict="PASS",
        decision=expected_decision,
        output=classification,
    )
    if expected_decision == "escalate":
        return _escalate(
            audit,
            event_id,
            "classification requires human review",
            classification=classification,
        )

    def generate(feedback: list[str]) -> JsonObject:
        attempt_number = len(generation_attempts) + 1
        try:
            proposal = generate_proposal(event, golden, evidence, classification, feedback)
        except TreatmentDependencyError as exc:
            failed_attempt = {
                "attempt": attempt_number,
                "verdict": "ERROR",
                "reasons": [f"proposal provider failed: {type(exc).__name__}"],
                "proposal": None,
            }
            generation_attempts.append(failed_attempt)
            _audit(
                audit,
                event_id,
                stage="patch_proposal",
                attempt=attempt_number,
                verdict="ERROR",
                reasons=failed_attempt["reasons"],
            )
            raise
        generation_attempts.append({"attempt": attempt_number})
        return proposal

    def validate(proposal: JsonObject) -> list[str]:
        errors = validate_proposal(proposal, gitops_checkout)
        if not errors and proposal["classification"] != classification:
            errors.append("proposal classification must exactly match the validated classification")
        if not errors:
            errors.extend(patch_gate(proposal["patch"], golden, revision))
        return errors

    generation_attempts: list[JsonObject] = []
    def record_attempt(attempt: JsonObject) -> None:
        _audit(
            audit,
            event_id,
            stage="patch_proposal",
            attempt=attempt["attempt"],
            verdict=attempt["verdict"],
            reasons=attempt["reasons"],
            output=attempt["proposal"],
        )

    try:
        retry_result = generate_with_bounded_retries(
            generate,
            validate,
            max_retries=max_retries,
            on_attempt=record_attempt,
        )
    except TreatmentDependencyError as exc:
        return _escalate(
            audit,
            event_id,
            f"proposal provider failed: {type(exc).__name__}",
            classification=classification,
            attempts=generation_attempts,
        )

    if retry_result["status"] != "VALIDATED":
        return _escalate(
            audit,
            event_id,
            retry_result["escalation_reason"],
            classification=classification,
            attempts=retry_result["attempts"],
        )

    proposal = retry_result["proposal"]
    result = {
        "status": "VALIDATED",
        "decision": proposal["decision"],
        "classification": classification,
        "proposal": proposal,
        "attempts": retry_result["attempts"],
    }
    _audit(
        audit,
        event_id,
        stage="treatment",
        verdict="VALIDATED",
        decision=proposal["decision"],
        reasons=[],
    )
    return result
