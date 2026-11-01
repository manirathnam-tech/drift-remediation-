"""Contracts and deterministic safety checks for LLM treatment proposals."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATION_SCHEMA = ROOT / "schemas" / "classification-v1.schema.json"
PROPOSAL_SCHEMA = ROOT / "schemas" / "patch-proposal-v1.schema.json"
PROMPT_DIR = ROOT / "prompts"
ALLOWED_GITOPS_PREFIX = "apps/online-boutique/"
ALLOWED_MANIFEST_SUFFIXES = {".yaml", ".yml"}
MAX_PATCH_BYTES = 128_000
_DIFF_PATH = re.compile(r"^(?:\+\+\+|---) (?:[ab]/)?(.+)$")


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest used to identify a prompt or policy artifact."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prompt_provenance(prompt_name: str, model_id: str) -> dict[str, str | int]:
    """Build auditable model and prompt identifiers for a treatment response."""
    prompt_path = PROMPT_DIR / prompt_name
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt does not exist: {prompt_path}")
    if not model_id.strip():
        raise ValueError("model_id must be configured")
    return {
        "model_id": model_id,
        "prompt_version": prompt_name.removesuffix(".md"),
        "prompt_sha256": sha256_file(prompt_path),
        "temperature": 0,
    }


def _schema_errors(value: dict[str, Any], schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in sorted(
            validator.iter_errors(value),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    ]


def validate_classification(result: dict[str, Any]) -> list[str]:
    """Validate the classifier JSON and the project's severity/class rules."""
    errors = _schema_errors(result, CLASSIFICATION_SCHEMA)
    if errors:
        return errors

    expected_class = {0: "benign", 1: "operational", 2: "operational", 3: "critical"}[
        result["severity"]
    ]
    if result["drift_class"] != expected_class:
        errors.append("drift_class must match the approved severity rubric")
    if result["intent"] == "malicious" and not result["review_required"]:
        errors.append("malicious intent must require human review")
    if result["drift_class"] == "critical" and not result["review_required"]:
        errors.append("critical drift must require human review")
    if result["confidence"] < 0.75 and not result["review_required"]:
        errors.append("confidence below 0.75 must require human review")
    if result["drift_class"] == "operational" and result["intent"] == "intentional-benign":
        errors.append("operational drift with intentional-benign intent must be re-adjudicated")
    return errors


def decision_for_classification(classification: dict[str, Any]) -> str:
    """Return the deterministic action permitted by a valid classification."""
    if (
        classification["review_required"]
        or classification["confidence"] < 0.75
        or classification["drift_class"] == "critical"
        or classification["intent"] == "malicious"
    ):
        return "escalate"
    if (
        classification["drift_class"] == "benign"
        and classification["intent"] == "intentional-benign"
    ):
        return "accept_live_into_git"
    return "revert_to_git"


def _paths_from_diff(patch: str) -> set[str]:
    paths: set[str] = set()
    for line in patch.splitlines():
        match = _DIFF_PATH.match(line)
        if match and match.group(1) != "/dev/null":
            paths.add(match.group(1))
    return paths


def _validate_paths(paths: set[str]) -> list[str]:
    errors = []
    if not paths:
        return ["patch must contain at least one manifest path"]
    for raw_path in sorted(paths):
        path = PurePosixPath(raw_path)
        if (
            "\\" in raw_path
            or path.is_absolute()
            or ".." in path.parts
            or not raw_path.startswith(ALLOWED_GITOPS_PREFIX)
            or path.suffix.lower() not in ALLOWED_MANIFEST_SUFFIXES
        ):
            errors.append(f"patch path is outside the allowed GitOps manifest area: {raw_path}")
    return errors


def _git_apply_check(patch: str, checkout: str | Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "apply", "--check"],
            cwd=checkout,
            input=patch,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"git apply --check could not run: {type(exc).__name__}"]
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "patch check failed"
        return [f"git apply --check: {detail[:1000]}"]
    return []


def validate_proposal(
    proposal: dict[str, Any], gitops_checkout: str | Path | None = None
) -> list[str]:
    """Return independent contract and patch-application failures; never applies a patch."""
    errors = _schema_errors(proposal, PROPOSAL_SCHEMA)
    if errors:
        return errors

    classification = proposal["classification"]
    errors.extend(validate_classification(classification))
    if errors:
        return errors

    expected_decision = decision_for_classification(classification)
    if proposal["decision"] != expected_decision:
        errors.append(f"decision must be {expected_decision} for the supplied classification")

    patch = proposal["patch"]
    if proposal["decision"] == "escalate":
        if patch or proposal["files"]:
            errors.append("escalation proposals must not contain a patch or changed files")
        return errors

    if not patch:
        return errors + ["non-escalation proposals require a Git patch"]
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        return errors + [f"patch exceeds {MAX_PATCH_BYTES} bytes"]

    paths = _paths_from_diff(patch)
    errors.extend(_validate_paths(paths))
    if sorted(paths) != sorted(proposal["files"]):
        errors.append("files must exactly match the paths in the unified diff")
    if errors:
        return errors

    if gitops_checkout is not None:
        errors.extend(_git_apply_check(patch, gitops_checkout))
    return errors
