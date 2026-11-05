"""Bounded, fail-closed evidence retrieval from a Bedrock Knowledge Base."""

from __future__ import annotations

import hashlib
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from errors import TreatmentDependencyError

MAX_RESULTS = 5
MAX_QUERY_CHARS = 1200
MAX_EXCERPT_CHARS = 2400


class EvidenceRetrievalError(TreatmentDependencyError):
    """Raised when the configured knowledge base cannot supply evidence."""


def make_evidence_retriever(
    knowledge_base_id: str | None = None,
    *,
    region: str | None = None,
    client: Any | None = None,
):
    """Build a retriever that returns bounded excerpts with stable citations."""
    selected_id = knowledge_base_id or os.environ.get("TREATMENT_KNOWLEDGE_BASE_ID", "")
    if not selected_id.strip():
        raise EvidenceRetrievalError("TREATMENT_KNOWLEDGE_BASE_ID is required")
    selected_region = region or os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
    runtime = client or boto3.client("bedrock-agent-runtime", region_name=selected_region)

    def retrieve(event: dict[str, Any], golden: dict[str, Any]) -> list[dict[str, Any]]:
        resources = event.get("resources", [])
        resource_names = []
        for resource in resources[:20] if isinstance(resources, list) else []:
            if not isinstance(resource, dict):
                continue
            # Avoid placing raw live values or arbitrary manifest text in the
            # retrieval query. The model receives bounded values separately.
            identity = " ".join(
                str(resource.get(key, ""))[:160]
                for key in ("kind", "namespace", "name", "status")
            )
            if identity.strip():
                resource_names.append(identity)
        query = (
            "Project DRIFT policy and validation evidence for Kubernetes drift. "
            f"Application {str(event.get('application', ''))[:160]}. "
            f"Resource observations: {'; '.join(resource_names)}. "
            "GitOps baseline revision "
            f"{str(golden.get('revision', ''))[:40]}. Cite relevant decision "
            "rules, required objective observations, and authorization evidence."
        )[:MAX_QUERY_CHARS]
        try:
            response = runtime.retrieve(
                knowledgeBaseId=selected_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "managedSearchConfiguration": {"numberOfResults": MAX_RESULTS}
                },
            )
        except (BotoCoreError, ClientError, KeyError, TypeError) as exc:
            raise EvidenceRetrievalError("Bedrock Knowledge Base retrieval failed") from exc

        results = response.get("retrievalResults", [])
        if not isinstance(results, list):
            raise EvidenceRetrievalError("Knowledge Base returned an invalid result list")
        evidence = []
        seen = set()
        for result in results[:MAX_RESULTS]:
            content = result.get("content", {}) if isinstance(result, dict) else {}
            excerpt = content.get("text") if isinstance(content, dict) else None
            if not isinstance(excerpt, str) or not excerpt.strip():
                continue
            excerpt = excerpt[:MAX_EXCERPT_CHARS]
            location = result.get("location", {})
            s3_location = location.get("s3Location", {}) if isinstance(location, dict) else {}
            uri = s3_location.get("uri", "") if isinstance(s3_location, dict) else ""
            if isinstance(uri, str) and uri.startswith("s3://"):
                pass
            elif isinstance(uri, str) and uri.startswith("https://"):
                parsed_uri = urlsplit(uri)
                host = parsed_uri.hostname or ""
                if host.endswith(".amazonaws.com") and ".s3." in host:
                    # Managed Knowledge Base retrieval may return an HTTPS
                    # S3 location. Drop any query string before auditing it.
                    uri = urlunsplit(
                        (parsed_uri.scheme, parsed_uri.netloc, parsed_uri.path, "", "")
                    )
                else:
                    uri = "bedrock-knowledge-base"
            else:
                uri = "bedrock-knowledge-base"
            source_id = hashlib.sha256((uri + "\0" + excerpt).encode("utf-8")).hexdigest()
            if source_id in seen:
                continue
            seen.add(source_id)
            score = result.get("score")
            evidence.append(
                {
                    "evidence_id": f"kb:{source_id[:32]}",
                    "source": uri[:500],
                    "excerpt": excerpt,
                    "relevance_score": score if isinstance(score, (int, float)) else None,
                }
            )
        if not evidence:
            raise EvidenceRetrievalError("Knowledge Base returned no usable evidence")
        return evidence

    return retrieve
