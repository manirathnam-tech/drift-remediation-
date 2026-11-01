"""Regression tests for measured gate outcomes and fail-closed reporting."""

import hashlib
import io
import json
import subprocess
import tarfile
from unittest.mock import Mock

import pytest
import validate_manifest
import validate_patch


def snapshot():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        body = b"kind: Deployment\n"
        member = tarfile.TarInfo("apps/online-boutique/app.yaml")
        member.size = len(body)
        bundle.addfile(member, io.BytesIO(body))
    return buffer.getvalue()


def test_checksum_failure_skips_all_execution(monkeypatch):
    run = Mock()
    monkeypatch.setattr(validate_patch, "_run", run)
    report = {}
    errors = validate_patch.validate_rendered_snapshot("patch", snapshot(), "a" * 40, "0" * 64, report=report)
    assert errors
    assert report["checks"]["source"] == "FAIL"
    assert report["checks"]["git_apply"] == "SKIP"
    run.assert_not_called()


def test_invalid_patch_is_reported_before_rendering():
    archive = snapshot()
    report = {}
    errors = validate_patch.validate_rendered_snapshot(
        "not a diff", archive, "a" * 40, hashlib.sha256(archive).hexdigest(), report=report,
    )
    assert errors
    assert report["checks"]["git_apply"] == "FAIL"
    assert report["checks"]["render"] == "SKIP"


def test_new_file_outside_manifest_scope_is_rejected():
    archive = snapshot()
    patch = "diff --git a/escape.yaml b/escape.yaml\nnew file mode 100644\n--- /dev/null\n+++ b/escape.yaml\n@@ -0,0 +1 @@\n+kind: Deployment\n"
    report = {}
    errors = validate_patch.validate_rendered_snapshot(
        patch, archive, "a" * 40, hashlib.sha256(archive).hexdigest(), report=report,
    )
    assert any("scope" in error for error in errors)
    assert report["checks"]["git_apply"] == "FAIL"


def test_archive_cannot_supply_git_configuration():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        member = tarfile.TarInfo(".git/config")
        bundle.addfile(member, io.BytesIO())
    archive = buffer.getvalue()
    report = {}
    errors = validate_patch.validate_rendered_snapshot(
        "patch", archive, "a" * 40, hashlib.sha256(archive).hexdigest(), report=report,
    )
    assert errors
    assert report["checks"]["source"] == "FAIL"
    assert report["checks"]["git_apply"] == "SKIP"


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("tool", 1)])
def test_tool_failure_is_an_error_not_policy_rejection(monkeypatch, failure):
    monkeypatch.setattr(validate_manifest.subprocess, "run", Mock(side_effect=failure))
    verdict, reason = validate_manifest._run("tool", ["tool"], 1)
    assert verdict == "ERROR"
    assert reason


@pytest.mark.parametrize("write_report", [False, True])
def test_missing_report_cannot_be_a_success(monkeypatch, write_report):
    def run(command, cwd, **kwargs):
        if "--json-report" in command and write_report:
            path = cwd / "validation-results.json"
            path.write_text(json.dumps({"checks": {
                "kubeconform": "PASS", "conftest": "PASS", "checkov": "PASS", "server_side": "SKIP",
            }}))
        return True, ""

    monkeypatch.setattr(validate_patch, "_run", run)
    monkeypatch.setattr(validate_patch.subprocess, "run", Mock(return_value=subprocess.CompletedProcess(
        ["kubectl"], 0, stdout=b"kind: Deployment\n", stderr=b"",
    )))
    archive = snapshot()
    report = {}
    errors = validate_patch.validate_rendered_snapshot(
        "patch", archive, "a" * 40, hashlib.sha256(archive).hexdigest(), report=report,
    )
    assert bool(errors) is not write_report
    assert report["checks"]["server_side"] == "SKIP"
    assert report["duration_ms"] >= 0
