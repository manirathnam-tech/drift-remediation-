"""Fetch and verify a revision-addressed golden manifest from S3."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from errors import TreatmentDependencyError

MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_SOURCE_ARCHIVE_BYTES = 32 * 1024 * 1024
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")


class GoldenManifestError(TreatmentDependencyError):
    """Raised when the golden artifact is missing or fails integrity checks."""


def load_golden_manifest(
    revision: str,
    *,
    bucket: str | None = None,
    region: str | None = None,
    s3_client: Any | None = None,
) -> dict[str, Any]:
    """Return verified manifest text and metadata for one full Git commit SHA."""
    selected_bucket = bucket or os.environ.get("GOLDEN_MANIFESTS_BUCKET")
    if not selected_bucket:
        raise GoldenManifestError("GOLDEN_MANIFESTS_BUCKET is required")
    if not isinstance(revision, str) or not _GIT_REVISION.fullmatch(revision):
        raise GoldenManifestError("revision must be a full 40-character lowercase Git SHA")

    client = s3_client or boto3.client(
        "s3",
        region_name=region or os.environ.get("AWS_REGION_NAME", "ap-southeast-2"),
    )
    prefix = f"golden/{revision}/"

    def fetch(key: str, limit: int) -> bytes:
        try:
            response = client.get_object(Bucket=selected_bucket, Key=prefix + key)
            stream = response["Body"]
            try:
                body = stream.read(limit + 1)
            finally:
                stream.close()
        except (BotoCoreError, ClientError, KeyError) as exc:
            raise GoldenManifestError(f"could not read golden artifact {key}") from exc
        if len(body) > limit:
            raise GoldenManifestError(f"golden artifact {key} exceeds the size limit")
        return body

    manifest_bytes = fetch("rendered.yaml", MAX_ARTIFACT_BYTES)
    source_archive = fetch("source.tar.gz", MAX_SOURCE_ARCHIVE_BYTES)
    checksum_bytes = fetch("SHA256SUMS", 2048)
    metadata_bytes = fetch("metadata.json", 16 * 1024)

    try:
        checksum_lines = checksum_bytes.decode("ascii").splitlines()
        checksums = {}
        for line in checksum_lines:
            digest, filename = line.split(maxsplit=1)
            checksums[filename.lstrip("* ")] = digest
        metadata = json.loads(metadata_bytes)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise GoldenManifestError("golden checksum or metadata is malformed") from exc

    actual_digest = hashlib.sha256(manifest_bytes).hexdigest()
    source_digest = hashlib.sha256(source_archive).hexdigest()
    if checksums != {
        "rendered.yaml": actual_digest,
        "source.tar.gz": source_digest,
    }:
        raise GoldenManifestError("golden manifest checksum does not match")
    if not isinstance(metadata, dict) or metadata.get("revision") != revision:
        raise GoldenManifestError("golden metadata revision does not match the event")
    if metadata.get("repository") != "manirathnam-tech/drift-gitops":
        raise GoldenManifestError("golden artifact repository identity is unexpected")

    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldenManifestError("golden manifest is not UTF-8") from exc
    if not manifest_text.strip():
        raise GoldenManifestError("golden manifest is empty")

    return {
        "revision": revision,
        "rendered_yaml": manifest_text,
        "sha256": actual_digest,
        "source_archive": source_archive,
        "source_sha256": source_digest,
        "metadata": metadata,
    }
