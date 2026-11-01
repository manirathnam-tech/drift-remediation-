"""Checks that retries and omitted admission checks cannot inflate validity."""

import pytest
import run_patch_eval
from eval_patch_validity import score


def test_repair_does_not_count_as_first_pass():
    rows = [
        {"id": "a", "attempt": 1, "checks": {"git_apply": "FAIL"}},
        {"id": "a", "attempt": 2, "checks": {"git_apply": "PASS"}},
    ]
    result = score(rows, {"a": {}})
    assert result["per_check"]["git_apply"]["pass_rate"] == 0
    assert result["first_attempt_records"] == 1


def test_skipped_admission_is_not_full_gate_pass():
    checks = {name: "PASS" for name in ("git_apply", "kubeconform", "conftest", "checkov")}
    result = score([{"id": "a", "attempt": 1, "checks": checks}], {"a": {}})
    assert result["static_first_pass_rate"] == 1
    assert result["full_gate_first_pass_rate"] == 0
    assert result["per_check"]["server_side"]["pass_rate"] is None


def test_held_out_result_is_rejected():
    with pytest.raises(ValueError, match="non-dev"):
        score([{"id": "test-case", "attempt": 1, "checks": {}}], {"dev-case": {}})


def test_runner_rejects_all_foreign_ids_before_validation(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("validation ran before split identities were checked")
    monkeypatch.setattr(run_patch_eval, "validate_proposal", unexpected)
    rows = [{"id": "dev", "attempt": 1}, {"id": "test", "attempt": 1}]
    with pytest.raises(ValueError, match="non-dev"):
        list(run_patch_eval.validate_rows(rows, {"dev": {}}, b"archive", "a" * 40))


def test_runner_rejects_revision_mismatch(monkeypatch):
    monkeypatch.setattr(run_patch_eval, "validate_proposal", lambda _: [])
    row = {"id": "dev", "attempt": 1, "revision": "b" * 40,
           "proposal": {"decision": "accept_live_into_git", "patch": "patch"}}
    result = next(run_patch_eval.validate_rows([row], {"dev": {}}, b"archive", "a" * 40))
    assert result["status"] == "FAIL"
    assert "revision" in result["reasons"][0]


def test_runner_preserves_measured_gate_results(monkeypatch):
    monkeypatch.setattr(run_patch_eval, "validate_proposal", lambda _: [])
    def validate(*args, report, **kwargs):
        report.update(checks={"git_apply": "PASS", "conftest": "FAIL", "server_side": "SKIP"},
                      status="FAIL", reasons=["privileged container"], duration_ms=42)
        return report["reasons"]
    monkeypatch.setattr(run_patch_eval, "validate_rendered_snapshot", validate)
    row = {"id": "dev", "attempt": 1, "revision": "a" * 40,
           "proposal": {"decision": "accept_live_into_git", "patch": "patch"}}
    result = next(run_patch_eval.validate_rows([row], {"dev": {}}, b"archive", "a" * 40))
    assert result["duration_ms"] == 42
    assert result["checks"]["conftest"] == "FAIL"
    assert result["checks"]["server_side"] == "SKIP"
