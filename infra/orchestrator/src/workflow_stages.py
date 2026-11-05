"""Single-step treatment stages used by the Step Functions workflow."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from contracts import (
    decision_for_classification,
    validate_classification,
    validate_proposal,
)
from errors import TreatmentDependencyError

JsonObject = dict[str, Any]
AuditWriter = Callable[[JsonObject], None]


def _audit(audit: AuditWriter, event_id: str, **fields: Any) -> None:
    audit({"event_id": event_id, **fields})


def _golden_is_valid(golden: JsonObject, revision: str) -> bool:
    manifest = golden.get("rendered_yaml")
    manifest_sha = golden.get("sha256")
    metadata = golden.get("metadata")
    archive = golden.get("source_archive")
    archive_sha = golden.get("source_sha256")
    return (
        isinstance(manifest, str)
        and bool(manifest.strip())
        and isinstance(manifest_sha, str)
        and hashlib.sha256(manifest.encode("utf-8")).hexdigest() == manifest_sha
        and isinstance(metadata, dict)
        and metadata.get("revision") == revision
        and golden.get("revision") == revision
        and metadata.get("repository") == "manirathnam-tech/drift-gitops"
        and isinstance(archive, bytes)
        and isinstance(archive_sha, str)
        and hashlib.sha256(archive).hexdigest() == archive_sha
    )


def classify_stage(
    event: JsonObject,
    *,
    load_golden: Callable[[str], JsonObject],
    retrieve_evidence: Callable[[JsonObject, JsonObject], list[JsonObject]],
    classify: Callable[[JsonObject, JsonObject, list[JsonObject]], JsonObject],
    audit: AuditWriter,
) -> JsonObject:
    """Load trusted context and return a validated classification checkpoint."""
    event_id = event.get("event_id")
    revision = event.get("revision")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event.event_id is required")
    if not isinstance(revision, str) or not revision:
        return {
            "status": "ESCALATE",
            "decision": "escalate",
            "reason": "event revision is missing",
        }

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
        return {
            "status": "ESCALATE",
            "decision": "escalate",
            "reason": f"golden manifest lookup failed: {type(exc).__name__}",
        }

    if not _golden_is_valid(golden, revision):
        _audit(
            audit,
            event_id,
            stage="golden_manifest",
            verdict="REJECT",
            reasons=["golden artifact identity or checksum is invalid"],
        )
        return {
            "status": "ESCALATE",
            "decision": "escalate",
            "reason": "golden artifact identity or checksum is invalid",
        }
    _audit(
        audit,
        event_id,
        stage="golden_manifest",
        verdict="PASS",
        sha256=golden["sha256"],
        source_sha256=golden["source_sha256"],
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
        return {
            "status": "ESCALATE",
            "decision": "escalate",
            "reason": f"evidence lookup failed: {type(exc).__name__}",
        }

    if not isinstance(evidence, list):
        evidence = []
        evidence_error = ["evidence adapter returned an invalid package"]
    else:
        evidence_ids = {
            item.get("evidence_id")
            for item in evidence
            if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
        }
        evidence_error = (
            ["evidence package contains a missing or duplicate ID"]
            if len(evidence_ids) != len(evidence)
            else []
        )
    if evidence_error:
        _audit(
            audit,
            event_id,
            stage="evidence_retrieval",
            verdict="REJECT",
            reasons=evidence_error,
        )
        return {
            "status": "ESCALATE",
            "decision": "escalate",
            "reason": evidence_error[0],
        }

    evidence_ids = {item["evidence_id"] for item in evidence}
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
        return {
            "status": "ESCALATE",
            "decision": "escalate",
            "reason": f"classification provider failed: {type(exc).__name__}",
        }

    errors = validate_classification(classification)
    if not errors and not set(classification["evidence_refs"]).issubset(evidence_ids):
        errors.append("classification cites evidence outside the supplied package")
    if errors:
        _audit(
            audit,
            event_id,
            stage="classification",
            verdict="REJECT",
            reasons=errors,
            output=classification,
        )
        return {
            "status": "ESCALATE",
            "decision": "escalate",
            "reason": "classification failed its schema or evidence checks",
            "classification": classification,
        }

    decision = decision_for_classification(classification)
    _audit(
        audit,
        event_id,
        stage="classification",
        verdict="PASS",
        decision=decision,
        output=classification,
    )
    if decision == "escalate":
        return {
            "status": "ESCALATE",
            "decision": decision,
            "reason": "classification requires human review",
            "classification": classification,
        }
    return {
        "status": "READY",
        "decision": decision,
        "classification": classification,
        "evidence": evidence,
    }


def synthesize_stage(
    event: JsonObject,
    classification: JsonObject,
    evidence: list[JsonObject],
    feedback: list[str],
    *,
    load_golden: Callable[[str], JsonObject],
    generate_proposal: Callable[
        [JsonObject, JsonObject, list[JsonObject], JsonObject, list[str]], JsonObject
    ],
) -> JsonObject:
    """Generate one proposal; validation and retry decisions are separate stages."""
    revision = event.get("revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("event revision is required for synthesis")
    golden = load_golden(revision)
    if not _golden_is_valid(golden, revision):
        raise TreatmentDependencyError("golden artifact identity or checksum is invalid")
    return generate_proposal(event, golden, evidence, classification, feedback)


def validate_stage(
    proposal: JsonObject,
    classification: JsonObject,
    golden: JsonObject,
    revision: str,
    *,
    patch_gate: Callable[[str, JsonObject, str], list[str]],
) -> list[str]:
    """Validate one proposal independently and return reasons for the retry state."""
    errors = validate_proposal(proposal)
    if not errors and proposal["classification"] != classification:
        errors.append("proposal classification must exactly match the validated classification")
    if not errors:
        errors.extend(patch_gate(proposal["patch"], golden, revision))
    return errors
