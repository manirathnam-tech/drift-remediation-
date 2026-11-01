"""Configurable JSON-only model adapters using Amazon Bedrock Converse."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from contracts import (
    CLASSIFICATION_SCHEMA,
    PROMPT_DIR,
    PROPOSAL_SCHEMA,
    prompt_provenance,
    validate_classification,
)
from errors import TreatmentDependencyError
from source_context import manifest_sources

MAX_PROMPT_BYTES = 180_000
MAX_RESPONSE_BYTES = 128_000
MAX_OUTPUT_TOKENS = 4096
_SENSITIVE_NAME = re.compile(
    r"password|passwd|token|secret|credential|private.?key|access.?key|api.?key|authorization",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)([\"']?[^\s\"']*(?:password|passwd|token|secret|credential|"
    r"private[_-]?key|access[_-]?key|api[_-]?key|authorization)[^\s\"']*[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,}\]]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")


class BedrockInvocationError(TreatmentDependencyError):
    """Raised when Bedrock cannot return a model response."""


class BedrockJSONModel:
    """Use the shared Converse API with a runtime-selected model ID."""

    def __init__(
        self,
        model_id: str | None = None,
        *,
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model_id = model_id or os.environ.get("BEDROCK_MODEL_ID", "")
        if not self.model_id.strip():
            raise ValueError("BEDROCK_MODEL_ID is required")
        selected_region = region or os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
        self.client = client or boto3.client("bedrock-runtime", region_name=selected_region)

    def generate_json(self, prompt_name: str, context: dict[str, Any]) -> dict[str, Any]:
        """Call a versioned prompt and parse exactly one JSON object."""
        if prompt_name not in {"classify_v1.md", "patch_v1.md"}:
            raise ValueError("prompt_name must identify an approved treatment prompt")
        prompt_path = PROMPT_DIR / prompt_name
        prompt = prompt_path.read_text(encoding="utf-8")
        schema_path = CLASSIFICATION_SCHEMA if prompt_name == "classify_v1.md" else PROPOSAL_SCHEMA
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        prompt += "\n\nRequired output JSON schema:\n" + json.dumps(schema, sort_keys=True)
        user_payload = json.dumps(context, sort_keys=True, separators=(",", ":"))
        if len(prompt.encode("utf-8")) + len(user_payload.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise TreatmentDependencyError("model prompt exceeds the configured 180 KiB limit")

        try:
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_payload}],
                    }
                ],
                inferenceConfig={"temperature": 0.0, "maxTokens": MAX_OUTPUT_TOKENS},
            )
        except (BotoCoreError, ClientError) as exc:
            raise BedrockInvocationError("Bedrock Converse request failed") from exc

        try:
            content = response["output"]["message"]["content"]
            text = "".join(block["text"] for block in content if isinstance(block, dict) and "text" in block)
        except (KeyError, TypeError):
            return {"_model_output_error": "response did not contain text content"}
        if not text or len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            return {"_model_output_error": "response was empty or exceeded 128 KiB"}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            return {"_model_output_error": f"response was not JSON: {exc.msg[:300]}"}
        if not isinstance(value, dict):
            return {"_model_output_error": "response JSON root was not an object"}
        return value


def _redact(value: Any, parent_name: str = "") -> Any:
    """Remove common credential fields before sending observed state to a model."""
    if isinstance(value, str):
        value = _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", value)
        value = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
        return _AWS_ACCESS_KEY.sub("[REDACTED]", value)
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_NAME.search(key_text):
                cleaned[key] = "[REDACTED]"
            elif key_text == "env" and isinstance(item, list):
                cleaned[key] = [
                    {
                        **entry,
                        "value": "[REDACTED]",
                    }
                    if isinstance(entry, dict)
                    and _SENSITIVE_NAME.search(str(entry.get("name", "")))
                    and "value" in entry
                    else _redact(entry, key_text)
                    for entry in item
                ]
            else:
                cleaned[key] = _redact(item, key_text)
        return cleaned
    if isinstance(value, list):
        return [_redact(item, parent_name) for item in value]
    return value


def _model_context(
    event: dict[str, Any],
    golden: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    safe_resources = []
    for resource in event.get("resources", []):
        if isinstance(resource, dict):
            safe_resources.append(_redact(resource))
    safe_evidence = []
    for item in evidence:
        safe_evidence.append(
            {
                "evidence_id": item.get("evidence_id"),
                "source": item.get("source"),
                "excerpt": _redact(item.get("excerpt", "")),
            }
        )
    return {
        "event": {
            key: event.get(key)
            for key in (
                "event_id",
                "event_type",
                "application",
                "revision",
                "t_detect",
                "reconciled_at",
            )
        },
        "resources": safe_resources,
        "golden_manifest": {
            "revision": golden.get("revision"),
            "sha256": golden.get("sha256"),
        },
        "evidence": safe_evidence,
    }


def _attach_provenance(
    output: dict[str, Any], provenance: dict[str, str | int]
) -> dict[str, Any]:
    if output.get("provenance") != provenance:
        return {**output, "_provenance_error": "model changed or omitted caller provenance"}
    return output


def make_classifier(model: BedrockJSONModel, *, repair: bool = False, audit=None):
    """Adapt the configurable model to the treatment pipeline classifier contract."""

    def classify(event, golden, evidence):
        provenance = prompt_provenance("classify_v1.md", model.model_id)
        context = _model_context(event, golden, evidence)
        context["provenance"] = provenance
        for attempt in range(1, 3 if repair else 2):
            output = _attach_provenance(
                model.generate_json("classify_v1.md", context), provenance
            )
            errors = validate_classification(output)
            if not errors:
                supplied_ids = {item["evidence_id"] for item in evidence}
                if not set(output["evidence_refs"]).issubset(supplied_ids):
                    errors.append("classification cites evidence outside the supplied package")
            if audit is not None:
                audit({
                    "event_id": event["event_id"],
                    "stage": "classification_attempt",
                    "attempt": attempt,
                    "verdict": "REJECT" if errors else "PASS",
                    "reasons": errors,
                    "output": output,
                })
            if not errors:
                return output
            context["repair_feedback"] = errors
            context["previous_output"] = _redact(output)
        return output

    return classify


def make_proposal_generator(model: BedrockJSONModel):
    """Adapt the configurable model to the bounded proposal retry contract."""

    def generate(event, golden, evidence, classification, feedback):
        provenance = prompt_provenance("patch_v1.md", model.model_id)
        context = _model_context(event, golden, evidence)
        context.update(
            {
                "classification": classification,
                "gitops_sources": _redact(manifest_sources(golden)),
                "retry_feedback": feedback,
                "provenance": provenance,
            }
        )
        output = model.generate_json("patch_v1.md", context)
        return _attach_provenance(output, provenance)

    return generate
