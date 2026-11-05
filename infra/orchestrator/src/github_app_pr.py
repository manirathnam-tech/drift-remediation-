"""Create a draft GitOps pull request after an explicit human approval."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import boto3
import jwt
from contracts import validate_proposal

API_ROOT = "https://api.github.com"
APPROVED_OWNER = "manirathnam-tech"
APPROVED_REPO = "drift-gitops"
MAX_RESPONSE_BYTES = 1_000_000
_SECRET = None


class GitHubAppError(RuntimeError):
    """Raised when a draft PR cannot be created safely."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise GitHubAppError(f"{name} is required")
    return value


def _private_key() -> str:
    global _SECRET
    if _SECRET is None:
        arn = _required_env("GITHUB_APP_PRIVATE_KEY_SECRET_ARN")
        response = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION_NAME", "ap-southeast-2"),
        ).get_secret_value(SecretId=arn)
        secret = response.get("SecretString")
        if not isinstance(secret, str) or not secret:
            raise GitHubAppError("GitHub App secret has no SecretString")
        try:
            decoded = json.loads(secret)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            secret = decoded.get("private_key", "")
        if not isinstance(secret, str) or (
            "BEGIN RSA PRIVATE KEY" not in secret and "BEGIN PRIVATE KEY" not in secret
        ):
            raise GitHubAppError("GitHub App secret must contain a PEM private key")
        _SECRET = secret
    return _SECRET


def _api(method: str, path: str, *, token: str | None = None, body: dict | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "drift-thesis-treatment",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(f"{API_ROOT}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise GitHubAppError("GitHub API response exceeded the size limit")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raise GitHubAppError(
            f"GitHub API {method} {path} failed with HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GitHubAppError(f"GitHub API {method} {path} failed") from exc


def _installation_token() -> str:
    app_id = _required_env("GITHUB_APP_ID")
    installation_id = _required_env("GITHUB_APP_INSTALLATION_ID")
    now = int(time.time())
    app_jwt = jwt.encode(
        {"iat": now - 30, "exp": now + 8 * 60, "iss": app_id},
        _private_key(),
        algorithm="RS256",
    )
    response = _api(
        "POST",
        f"/app/installations/{quote(installation_id, safe='')}/access_tokens",
        token=app_jwt,
        body={
            "repositories": [APPROVED_REPO],
            "permissions": {
                "contents": "write",
                "pull_requests": "write",
                "issues": "write",
            },
        },
    )
    token = response.get("token") if isinstance(response, dict) else None
    if not isinstance(token, str) or not token:
        raise GitHubAppError("GitHub did not return an installation token")
    return token


def _git(args: list[str], *, cwd: str, env: dict[str, str], input_text: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubAppError(f"git {' '.join(args[:2])} failed") from exc
    if result.returncode:
        raise GitHubAppError(f"git {' '.join(args[:2])} failed: {result.stderr[:1000]}")
    return result.stdout.strip()


def _require_ancestor(revision: str, base: str, *, cwd: str, env: dict[str, str]) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, base],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise GitHubAppError("event revision is not an ancestor of the GitOps patch base")


def _tree_after_patch(
    patch: str, base: str, *, cwd: str, env: dict[str, str]
) -> str:
    with tempfile.TemporaryDirectory(prefix="drift-patch-index-") as index_dir:
        index_env = env.copy()
        index_env["GIT_INDEX_FILE"] = os.path.join(index_dir, "index")
        _git(["read-tree", base], cwd=cwd, env=index_env)
        _git(
            ["apply", "--cached", "--check"],
            cwd=cwd,
            env=index_env,
            input_text=patch,
        )
        _git(
            ["apply", "--cached"],
            cwd=cwd,
            env=index_env,
            input_text=patch,
        )
        return _git(["write-tree"], cwd=cwd, env=index_env)


def _opened_at(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise GitHubAppError("GitHub pull request has no creation timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GitHubAppError("GitHub pull request creation timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise GitHubAppError("GitHub pull request creation timestamp has no timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_report(
    report: dict[str, Any], *, revision: str, patch_sha256: str
) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise GitHubAppError("a successful validator report is required")
    checks = report.get("checks")
    required_checks = (
        "source",
        "git_apply",
        "render",
        "kubeconform",
        "conftest",
        "checkov",
    )
    if (
        report.get("status") != "PASS"
        or report.get("revision") != revision
        or report.get("patch_sha256") != patch_sha256
        or not isinstance(checks, dict)
        or any(checks.get(name) != "PASS" for name in required_checks)
    ):
        raise GitHubAppError("validator report does not prove this patch passed every required gate")
    return report


def _apply_severity_label(token: str, pull_number: int, severity: int) -> str:
    label = f"drift-severity-{severity}"
    colors = {0: "1f883d", 1: "0969da", 2: "bf8700", 3: "cf222e"}
    description = f"DRIFT classifier severity {severity}; see the PR report for justification."
    try:
        _api(
            "POST",
            f"/repos/{APPROVED_OWNER}/{APPROVED_REPO}/labels",
            token=token,
            body={"name": label, "color": colors[severity], "description": description},
        )
    except GitHubAppError:
        # A prior run may already have created this repository label. Confirm
        # that it exists before continuing; permission and transport errors
        # still fail closed here.
        existing = _api(
            "GET",
            f"/repos/{APPROVED_OWNER}/{APPROVED_REPO}/labels/{quote(label, safe='')}",
            token=token,
        )
        if not isinstance(existing, dict) or existing.get("name") != label:
            raise
    _api(
        "POST",
        f"/repos/{APPROVED_OWNER}/{APPROVED_REPO}/issues/{pull_number}/labels",
        token=token,
        body={"labels": [label]},
    )
    return label


def _validator_report_markdown(report: dict[str, Any]) -> str:
    checks = report["checks"]
    rows = [
        "| Check | Result |",
        "| --- | --- |",
    ]
    for name in (
        "source",
        "git_apply",
        "render",
        "kubeconform",
        "conftest",
        "checkov",
        "server_side",
    ):
        value = checks.get(name, "NOT_REPORTED")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z_]{1,24}", value):
            value = "INVALID"
        rows.append(f"| {name} | {value} |")
    duration = report.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        duration = "not recorded"
    else:
        duration = f"{duration} ms"
    rows.append(f"\nValidation time: {duration}.")
    return "\n".join(rows)


def create_draft_pr(
    event: dict[str, Any],
    proposal: dict[str, Any],
    *,
    approved: bool,
    approval_reference: str,
    validator_report: dict[str, Any],
    audit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Push only a revalidated patch to a deterministic branch and open a draft PR.

    Call this only after a reviewer has approved the exact event and proposal.
    The GitHub App is restricted to the project's GitOps repository. This helper
    never merges a pull request.
    """
    if (
        approved is not True
        or not isinstance(approval_reference, str)
        or not approval_reference.strip()
    ):
        raise GitHubAppError("an explicit approval and review reference are required")
    if os.environ.get("GITHUB_REPOSITORY_OWNER", APPROVED_OWNER) != APPROVED_OWNER or os.environ.get(
        "GITHUB_REPOSITORY_NAME", APPROVED_REPO
    ) != APPROVED_REPO:
        raise GitHubAppError("configured repository is outside the approved GitOps target")
    event_id = event.get("event_id")
    revision = event.get("revision")
    if (
        not isinstance(event_id, str)
        or not re.fullmatch(r"[a-f0-9]{64}", event_id)
        or not isinstance(revision, str)
        or not re.fullmatch(r"[a-f0-9]{40}", revision)
    ):
        raise GitHubAppError("event identity or source revision is invalid")
    if proposal.get("decision") == "escalate":
        raise GitHubAppError("escalation decisions cannot create a pull request")
    errors = validate_proposal(proposal)
    if errors:
        raise GitHubAppError("proposal validation failed: " + "; ".join(errors[:10]))

    patch = proposal["patch"]
    patch_sha = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    event_sha = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    report = _validate_report(
        validator_report,
        revision=revision,
        patch_sha256=patch_sha,
    )
    severity = proposal["classification"]["severity"]
    if isinstance(severity, bool) or not isinstance(severity, int) or severity not in range(4):
        raise GitHubAppError("proposal severity is invalid")
    instance_id = event.get("instance_id", event_id)
    if not isinstance(instance_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", instance_id
    ):
        raise GitHubAppError("event instance_id is invalid for a branch name")
    # The current collector event contract has no experiment instance_id, so
    # deterministic event_id is the safe fallback until that identity is added.
    branch = f"drift/{instance_id}"
    marker = f"DRIFT-EVENT-SHA256: {event_sha}\nDRIFT-PATCH-SHA256: {patch_sha}"
    base = os.environ.get("GITHUB_BASE_BRANCH", "main")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", base)
        or ".." in base
        or "//" in base
        or base.endswith("/")
    ):
        raise GitHubAppError("configured base branch is invalid")
    token = _installation_token()
    query = urlencode({"state": "all", "head": f"{APPROVED_OWNER}:{branch}", "base": base})
    existing = _api("GET", f"/repos/{APPROVED_OWNER}/{APPROVED_REPO}/pulls?{query}", token=token)
    existing_pr = None
    existing_pr_head_sha = None
    if isinstance(existing, list) and existing:
        existing_pr = existing[0]
        head_sha = existing_pr.get("head", {}).get("sha")
        if not isinstance(head_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", head_sha):
            raise GitHubAppError("existing pull request has no valid head commit")
        existing_pr_head_sha = head_sha
        commit = _api(
            "GET",
            f"/repos/{APPROVED_OWNER}/{APPROVED_REPO}/commits/{head_sha}",
            token=token,
        )
        if marker not in commit.get("commit", {}).get("message", ""):
            raise GitHubAppError("existing deterministic pull request does not match this proposal")

    token_header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    git_env = os.environ.copy()
    git_env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {token_header}",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    remote = f"https://github.com/{APPROVED_OWNER}/{APPROVED_REPO}.git"
    with tempfile.TemporaryDirectory(prefix="drift-github-pr-") as checkout:
        _git(["init", "--quiet"], cwd=checkout, env=git_env)
        _git(["remote", "add", "origin", remote], cwd=checkout, env=git_env)
        _git(["fetch", "origin", base], cwd=checkout, env=git_env)
        base_ref = _git(["rev-parse", "FETCH_HEAD"], cwd=checkout, env=git_env)
        _require_ancestor(revision, base_ref, cwd=checkout, env=git_env)
        remote_branch = _git(
            ["ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
            cwd=checkout,
            env=git_env,
        )
        if existing_pr is not None and not remote_branch:
            raise GitHubAppError(
                "existing pull request branch is missing; refusing to recreate it automatically"
            )
        if remote_branch:
            branch_ref = f"refs/remotes/origin/{branch}"
            _git(
                ["fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"],
                cwd=checkout,
                env=git_env,
            )
            _git(
                ["checkout", "--quiet", "-B", branch, branch_ref],
                cwd=checkout,
                env=git_env,
            )
            branch_head = _git(["rev-parse", branch_ref], cwd=checkout, env=git_env)
            if existing_pr_head_sha and branch_head != existing_pr_head_sha:
                raise GitHubAppError("existing pull request head changed during inspection")
            message = _git(["log", "-1", "--format=%B"], cwd=checkout, env=git_env)
            if marker not in message:
                raise GitHubAppError("deterministic PR branch exists with different content")
            patch_base = _git(["merge-base", base_ref, branch_ref], cwd=checkout, env=git_env)
            _require_ancestor(revision, patch_base, cwd=checkout, env=git_env)
            expected_tree = _tree_after_patch(
                patch, patch_base, cwd=checkout, env=git_env
            )
            actual_tree = _git(
                ["rev-parse", f"{branch_ref}^{{tree}}"], cwd=checkout, env=git_env
            )
            if actual_tree != expected_tree:
                raise GitHubAppError(
                    "existing deterministic branch does not contain exactly the reviewed patch"
                )
        else:
            _git(["checkout", "--quiet", "-b", branch, base_ref], cwd=checkout, env=git_env)
            validation_errors = validate_proposal(proposal, checkout)
            if validation_errors:
                raise GitHubAppError(
                    "patch does not apply cleanly to current GitOps base: "
                    + "; ".join(validation_errors[:10])
                )
            _git(["apply", "--index"], cwd=checkout, env=git_env, input_text=patch)
            _git(
                ["config", "user.name", "drift-thesis-gitops[bot]"],
                cwd=checkout,
                env=git_env,
            )
            _git(
                ["config", "user.email", "drift-thesis-gitops[bot]@users.noreply.github.com"],
                cwd=checkout,
                env=git_env,
            )
            _git(
                ["commit", "-m", f"Propose drift remediation for {event_id}", "-m", marker],
                cwd=checkout,
                env=git_env,
            )
            expected_tree = _tree_after_patch(
                patch, base_ref, cwd=checkout, env=git_env
            )
            actual_tree = _git(["rev-parse", "HEAD^{tree}"], cwd=checkout, env=git_env)
            if actual_tree != expected_tree:
                raise GitHubAppError("created branch tree does not match the reviewed patch")
        _git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=checkout, env=git_env)

    if existing_pr is not None:
        number = existing_pr.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise GitHubAppError("existing pull request has no valid number")
        label = _apply_severity_label(token, number, severity)
        opened_at = _opened_at(existing_pr.get("created_at"))
        result = {
            "status": "already_exists",
            "number": number,
            "url": existing_pr.get("html_url"),
            "branch": branch,
            "severity_label": label,
            "t_pr_opened": opened_at,
        }
        if audit:
            audit(
                {
                    "event_id": event_id,
                    "stage": "github_draft_pr",
                    "verdict": "ALREADY_EXISTS",
                    "patch_sha256": patch_sha,
                    "pr_number": number,
                    "severity": severity,
                    "severity_label": label,
                    "t_pr_opened": opened_at,
                }
            )
        return result

    title = re.sub(r"\s+", " ", proposal["summary"]).strip()[:240]
    approval_reference_html = html.escape(approval_reference.strip()[:200])
    justification_html = html.escape(proposal["classification"]["justification"][:2000])
    body = (
        "## DRIFT remediation proposal\n\n"
        f"- Event: `{event_id}`\n- Source revision: `{revision}`\n"
        f"- Decision: `{proposal['decision']}`\n- Severity: `{severity}`\n"
        f"- Patch SHA-256: `{patch_sha}`\n"
        f"- Files: {', '.join(f'<code>{html.escape(path)}</code>' for path in proposal['files'])}\n"
        f"- Human approval reference: <code>{approval_reference_html}</code>\n\n"
        "### Classification justification\n\n"
        f"<blockquote>{justification_html}</blockquote>\n\n"
        "### Validator report\n\n"
        f"{_validator_report_markdown(report)}\n\n"
        "This is a draft for review. It is not merged or applied automatically."
    )
    pr = _api(
        "POST",
        f"/repos/{APPROVED_OWNER}/{APPROVED_REPO}/pulls",
        token=token,
        body={
            "title": title or "Review drift remediation proposal",
            "head": branch,
            "base": base,
            "body": body,
            "draft": True,
        },
    )
    pull_number = pr.get("number")
    if isinstance(pull_number, bool) or not isinstance(pull_number, int) or pull_number < 1:
        raise GitHubAppError("GitHub did not return a valid pull request number")
    label = _apply_severity_label(token, pull_number, severity)
    opened_at = _opened_at(pr.get("created_at"))
    result = {
        "status": "created",
        "number": pull_number,
        "url": pr.get("html_url"),
        "branch": branch,
        "patch_sha256": patch_sha,
        "severity_label": label,
        "t_pr_opened": opened_at,
    }
    if audit:
        audit({
            "event_id": event_id,
            "stage": "github_draft_pr",
            "verdict": "CREATED",
            "patch_sha256": patch_sha,
            "pr_number": result["number"],
            "severity": severity,
            "severity_label": label,
            "t_pr_opened": opened_at,
            "approval_reference": approval_reference.strip()[:200],
        })
    return result
