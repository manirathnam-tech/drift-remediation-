"""Render and validate a proposal in a disposable checkout at its exact base."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 180


def _run(
    command: list[str], cwd: Path, *, input_text: str | None = None,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
    full_output: bool = False,
) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{command[0]} could not complete: {type(exc).__name__}"
    detail = result.stderr.strip() or result.stdout.strip()
    return result.returncode == 0, detail if full_output else detail[:2000]


def validate_rendered_patch(
    patch: str,
    gitops_checkout: str | Path,
    revision: str,
    *,
    server_side: bool = False,
) -> list[str]:
    """Apply only in a temporary copy, render, and run all manifest gates."""
    checkout = Path(gitops_checkout).resolve()
    ok, actual_revision = _run(
        ["git", "rev-parse", "HEAD"], checkout
    )
    if not ok:
        return [f"could not read GitOps checkout revision: {actual_revision}"]
    if actual_revision != revision:
        return ["GitOps checkout revision differs from the event revision"]

    try:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", revision],
            cwd=checkout,
            capture_output=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"could not export GitOps revision: {type(exc).__name__}"]
    if archive.returncode:
        detail = archive.stderr.decode("utf-8", "replace").strip()
        return [f"could not export GitOps revision: {detail[:1000]}"]
    if len(archive.stdout) > MAX_ARCHIVE_BYTES:
        return ["GitOps revision archive exceeds the 128 MiB safety limit"]

    return _validate_archive(patch, archive.stdout, server_side=server_side)


def validate_rendered_snapshot(
    patch: str,
    source_archive: bytes,
    revision: str,
    expected_sha256: str,
    *,
    server_side: bool = False,
    report: dict | None = None,
) -> list[str]:
    """Validate using the checksum-verified source snapshot published for a SHA."""
    started = time.monotonic()
    details = report if report is not None else {}
    details.clear()
    details["checks"] = dict.fromkeys(
        ("source", "git_apply", "render", "kubeconform", "conftest", "checkov", "server_side"),
        "SKIP",
    )
    errors = _validate_snapshot(
        patch, source_archive, revision, expected_sha256,
        server_side=server_side, report=details,
    )
    details.update(status="FAIL" if errors else "PASS", reasons=errors,
                   duration_ms=round((time.monotonic() - started) * 1000))
    return errors


def _validate_snapshot(
    patch: str, source_archive: bytes, revision: str, expected_sha256: str,
    *, server_side: bool, report: dict,
) -> list[str]:
    report["checks"]["source"] = "FAIL"
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return ["revision must be a full lowercase Git SHA"]
    if not isinstance(source_archive, bytes) or not source_archive:
        return ["source snapshot is missing or invalid"]
    if hashlib.sha256(source_archive).hexdigest() != expected_sha256:
        return ["source snapshot checksum does not match the golden metadata"]
    if len(source_archive) > MAX_ARCHIVE_BYTES:
        return ["GitOps source snapshot exceeds the 128 MiB safety limit"]
    report["checks"]["source"] = "PASS"
    return _validate_archive(patch, source_archive, server_side=server_side, report=report)


def _validate_archive(
    patch: str,
    archive_bytes: bytes,
    *,
    server_side: bool,
    report: dict | None = None,
) -> list[str]:
    """Run the patch and manifest gates inside a disposable baseline archive."""

    checks = report["checks"] if report is not None else {}
    with tempfile.TemporaryDirectory(prefix="drift-patch-check-") as temp_dir:
        candidate = Path(temp_dir)
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as bundle:
                size = 0
                names = set()
                for member in bundle:
                    path = PurePosixPath(member.name)
                    if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                            or ".git" in path.parts or member.name in names
                            or not (member.isfile() or member.isdir())):
                        raise ValueError("unsafe archive member")
                    names.add(member.name)
                    size += member.size
                    if size > MAX_ARCHIVE_BYTES or len(names) > 10000:
                        raise ValueError("expanded source archive exceeds limits")
                bundle.extractall(candidate, filter="data")
        except (tarfile.TarError, OSError, ValueError) as exc:
            checks["source"] = "FAIL"
            return [f"could not safely unpack GitOps revision: {type(exc).__name__}"]

        for command in (["git", "init", "--quiet"], ["git", "add", "--all"]):
            ok, detail = _run(command, candidate)
            if not ok:
                return [f"temporary checkout setup failed: {detail}"]
        ok, detail = _run(
            [
                "git",
                "-c",
                "user.name=Drift Validator",
                "-c",
                "user.email=drift-validator.invalid",
                "commit",
                "--quiet",
                "-m",
                "golden baseline",
            ],
            candidate,
        )
        if not ok:
            return [f"temporary baseline commit failed: {detail}"]

        checks["git_apply"] = "FAIL"
        ok, detail = _run(["git", "apply", "--check"], candidate, input_text=patch)
        if not ok:
            return [f"candidate patch does not apply to the exact revision: {detail}"]
        ok, detail = _run(["git", "apply"], candidate, input_text=patch)
        if not ok:
            return [f"candidate patch could not be applied in the temporary checkout: {detail}"]
        # Inspect Git's actual result too: diff headers alone cannot establish
        # scope for renames, mode changes, and binary patch metadata.
        ok, detail = _run(["git", "add", "--all"], candidate)
        if not ok:
            return [f"could not stage temporary patch for scope verification: {detail}"]
        ok, changed = _run(
            ["git", "diff", "--cached", "--name-only", "-z", "HEAD"], candidate,
            full_output=True,
        )
        if not ok:
            return ["could not verify changed-file scope"]
        for name in changed.split("\0"):
            if not name:
                continue
            path = PurePosixPath(name)
            target = candidate / name
            if (not name.startswith("apps/online-boutique/")
                    or path.suffix.lower() not in {".yaml", ".yml"}
                    or path.is_absolute() or ".." in path.parts
                    or target.is_symlink()):
                return ["candidate changes a file outside the allowed manifest scope"]
        checks["git_apply"] = "PASS"
        checks["render"] = "ERROR"

        try:
            rendered = subprocess.run(
                ["kubectl", "kustomize", "apps/online-boutique"],
                cwd=candidate,
                capture_output=True,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"Kustomize rendering could not complete: {type(exc).__name__}"]
        if rendered.returncode:
            checks["render"] = "FAIL"
            detail = rendered.stderr.decode("utf-8", "replace").strip()
            return [f"Kustomize rendering failed: {detail[:2000]}"]
        if not rendered.stdout.strip():
            checks["render"] = "FAIL"
            return ["Kustomize produced an empty manifest"]
        checks["render"] = "PASS"

        manifest = candidate / "candidate-rendered.yaml"
        manifest.write_bytes(rendered.stdout)
        command = [
            sys.executable,
            str(Path(__file__).with_name("validate_manifest.py")),
            str(manifest),
            "--json-report", str(candidate / "validation-results.json"),
        ]
        if server_side:
            command.append("--server-side")
        ok, detail = _run(command, candidate, timeout=510 if server_side else 390)
        try:
            gate_report = json.loads((candidate / "validation-results.json").read_text())
            for name in ("kubeconform", "conftest", "checkov", "server_side"):
                verdict = gate_report["checks"][name]
                if verdict not in {"PASS", "FAIL", "SKIP", "ERROR"}:
                    raise ValueError("invalid validator verdict")
                checks[name] = verdict
            if report is not None:
                report["gate_reasons"] = gate_report.get("reasons", {})
        except (OSError, ValueError, KeyError, TypeError):
            checks["kubeconform"] = "ERROR"
            return ["manifest validator did not produce a complete structured report"]
        required = ("kubeconform", "conftest", "checkov")
        if server_side:
            required += ("server_side",)
        if not ok or any(checks[name] != "PASS" for name in required):
            return [f"rendered manifest validation failed: {detail}"]
    return []
