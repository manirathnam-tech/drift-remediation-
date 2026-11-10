"""Deterministic severity rules for the DRIFT baseline control arm."""

from __future__ import annotations

import fnmatch
import itertools
import json
import re
from datetime import datetime
from typing import Any

APPLICATION_NAME = "online-boutique"
APPLICATION_NAMESPACE = "online-boutique"
RULESET_VERSION = "baseline-rules-v1"
ALLOWED_RESOURCES = {
    "Deployment": {"frontend", "cartservice", "currencyservice"},
    "Service": {"frontend-external", "cartservice", "productcatalogservice"},
    "Role": {"drift-observer"},
    "RoleBinding": {"drift-observer"},
    "NetworkPolicy": {
        "allow-callers-to-productcatalogservice",
        "allow-cartservice-to-redis-cart",
    },
}

# Ordered from the most specific policy-relevant fields to broader rules.
# Paths use '*' for one JSON path segment, including a list index.
RULES: tuple[tuple[str, str, int, str, str], ...] = (
    ("Deployment", "metadata.annotations", 1, "DEPLOYMENT_ANNOTATION", "deployment metadata changed"),
    ("Deployment", "metadata.annotations.*", 1, "DEPLOYMENT_ANNOTATION", "deployment metadata changed"),
    ("Deployment", "spec.template.metadata.annotations", 2, "POD_ANNOTATION", "pod annotation may affect runtime behavior"),
    ("Deployment", "spec.template.metadata.annotations.*", 2, "POD_ANNOTATION", "pod annotation may affect runtime behavior"),
    ("Deployment", "spec.replicas", 1, "REPLICA_COUNT", "replica-count change; impact not measured"),
    ("Deployment", "spec.template.spec.containers.*", 3, "CONTAINER_SET", "container added or removed; inspect security posture"),
    ("Deployment", "spec.template.spec.containers.*.image", 1, "CONTAINER_IMAGE", "container image changed"),
    ("Deployment", "spec.template.spec.containers.*.resources.*", 1, "CONTAINER_RESOURCES", "container resource settings changed"),
    ("Deployment", "spec.template.spec.containers.*.env.*", 1, "CONTAINER_ENV", "container environment changed"),
    ("Deployment", "spec.template.spec.containers.*.securityContext", 3, "CONTAINER_SECURITY_CONTEXT", "container security control changed"),
    ("Deployment", "spec.template.spec.containers.*.securityContext.*", 3, "CONTAINER_SECURITY_CONTEXT", "container security control changed"),
    ("Deployment", "spec.template.spec.securityContext", 3, "POD_SECURITY_CONTEXT", "pod security control changed"),
    ("Deployment", "spec.template.spec.securityContext.*", 3, "POD_SECURITY_CONTEXT", "pod security control changed"),
    ("Deployment", "spec.template.spec.host*", 3, "HOST_NAMESPACE", "host namespace setting changed"),
    ("Deployment", "spec.template.spec.volumes.*", 3, "VOLUME_SET", "volume added or removed; inspect mount safety"),
    ("Deployment", "spec.template.spec.volumes.*.hostPath", 3, "HOST_PATH", "host path mount changed"),
    ("Deployment", "spec.template.spec.volumes.*.hostPath.*", 3, "HOST_PATH", "host path mount changed"),
    ("Service", "metadata.annotations", 2, "SERVICE_ANNOTATION", "service annotation may affect exposure or routing"),
    ("Service", "metadata.annotations.*", 2, "SERVICE_ANNOTATION", "service annotation may affect exposure or routing"),
    ("Service", "spec.type", 3, "SERVICE_TYPE", "service exposure type changed"),
    ("Service", "spec.externalIPs", 3, "SERVICE_EXTERNAL_IP", "external service address changed"),
    ("Service", "spec.externalIPs.*", 3, "SERVICE_EXTERNAL_IP", "external service address changed"),
    ("Service", "spec.ports.*.nodePort", 3, "SERVICE_NODE_PORT", "node port exposure changed"),
    ("Service", "spec.ports.*.port", 1, "SERVICE_PORT", "service port changed"),
    ("Role", "rules", 3, "RBAC_RULES", "effective role permissions changed"),
    ("Role", "rules.*", 3, "RBAC_RULES", "effective role permissions changed"),
    ("RoleBinding", "subjects", 3, "RBAC_SUBJECTS", "role binding subjects changed"),
    ("RoleBinding", "subjects.*", 3, "RBAC_SUBJECTS", "role binding subjects changed"),
    ("RoleBinding", "roleRef", 3, "RBAC_ROLE_REF", "role binding target changed"),
    ("RoleBinding", "roleRef.*", 3, "RBAC_ROLE_REF", "role binding target changed"),
    ("NetworkPolicy", "spec", 3, "NETWORK_POLICY", "network isolation policy changed"),
    ("NetworkPolicy", "spec.*", 3, "NETWORK_POLICY", "network isolation policy changed"),
)

_IGNORED_PATHS = {
    "metadata.creationTimestamp",
    "metadata.generation",
    "metadata.managedFields",
    "metadata.resourceVersion",
    "metadata.uid",
    "status",
}


def _changed_paths(live: Any, target: Any, prefix: tuple[str, ...] = ()) -> list[str]:
    """Return stable dotted paths that differ between Argo CD live and target state."""
    if isinstance(live, dict) and isinstance(target, dict):
        paths: list[str] = []
        for key in sorted(set(live) | set(target)):
            child = prefix + (str(key),)
            dotted = ".".join(child)
            if dotted in _IGNORED_PATHS or child[0] == "status":
                continue
            if key not in live or key not in target:
                paths.append(dotted)
            else:
                paths.extend(_changed_paths(live[key], target[key], child))
        return paths

    if isinstance(live, list) and isinstance(target, list):
        paths = []
        for index in range(max(len(live), len(target))):
            child = prefix + ("*",)
            if index >= len(live) or index >= len(target):
                paths.append(".".join(child))
            else:
                paths.extend(_changed_paths(live[index], target[index], child))
        return paths

    return [] if live == target else [".".join(prefix) or "$object"]


def _matches_path(path: str, pattern: str) -> bool:
    path_parts = path.split(".")
    pattern_parts = pattern.split(".")
    return len(path_parts) == len(pattern_parts) and all(
        fnmatch.fnmatchcase(value, rule_part)
        for value, rule_part in zip(path_parts, pattern_parts, strict=True)
    )


def _role_rules_expand(live: Any, target: Any) -> bool:
    """Return whether live Role rules grant permissions absent from the target."""
    if not isinstance(live, dict):
        return False
    if not isinstance(target, dict):
        return True
    live_spec = live.get("spec", live)
    target_spec = target.get("spec", target)
    if not isinstance(live_spec, dict) or not isinstance(target_spec, dict):
        return True
    live_rules = live_spec.get("rules", [])
    target_rules = target_spec.get("rules", [])
    if not isinstance(live_rules, list) or not isinstance(target_rules, list):
        return True

    groups = {"__drift_other_api_group__"}
    resources = {"__drift_other_resource__"}
    verbs = {"__drift_other_verb__"}
    names = {"__drift_other_name__"}
    for rule in live_rules + target_rules:
        if not isinstance(rule, dict):
            return True
        if rule.get("nonResourceURLs"):
            return True
        for field, domain in (
            ("apiGroups", groups),
            ("resources", resources),
            ("verbs", verbs),
        ):
            values = rule.get(field)
            if not isinstance(values, list) or not values:
                return True
            if any(not isinstance(value, str) for value in values):
                return True
            domain.update(value for value in values if value != "*")
        resource_names = rule.get("resourceNames", [])
        if not isinstance(resource_names, list) or any(
            not isinstance(value, str) for value in resource_names
        ):
            return True
        names.update(resource_names)

    candidates = itertools.product(groups, resources, verbs, names)
    if len(groups) * len(resources) * len(verbs) * len(names) > 100_000:
        return True

    def permits(
        rules: list[dict[str, Any]],
        api_group: str,
        resource: str,
        verb: str,
        name: str,
    ) -> bool:
        for rule in rules:
            resource_names = rule.get("resourceNames", [])
            if resource_names and name not in resource_names:
                continue
            if all(
                "*" in rule[field] or value in rule[field]
                for field, value in (
                    ("apiGroups", api_group),
                    ("resources", resource),
                    ("verbs", verb),
                )
            ):
                return True
        return False

    return any(
        permits(live_rules, *candidate) and not permits(target_rules, *candidate)
        for candidate in candidates
    )


def _role_binding_expands(live: Any, target: Any) -> bool:
    """Return whether a live RoleBinding grants a role to additional subjects."""
    if not isinstance(live, dict):
        return False
    if not isinstance(target, dict):
        return True
    live_spec = live.get("spec", live)
    target_spec = target.get("spec", target)
    if not isinstance(live_spec, dict) or not isinstance(target_spec, dict):
        return True
    if live_spec.get("roleRef") != target_spec.get("roleRef"):
        return True
    live_subjects = live_spec.get("subjects", [])
    target_subjects = target_spec.get("subjects", [])
    if not isinstance(live_subjects, list) or not isinstance(target_subjects, list):
        return True
    try:
        live_set = {json.dumps(item, sort_keys=True) for item in live_subjects}
        target_set = {json.dumps(item, sort_keys=True) for item in target_subjects}
    except (TypeError, ValueError):
        return True
    return bool(live_set - target_set)


def _rule_for(
    kind: str,
    path: str,
    live: Any = None,
    target: Any = None,
) -> tuple[int, str, str]:
    if path == "$object":
        if kind in {"Role", "RoleBinding", "NetworkPolicy"}:
            return (
                3,
                "SECURITY_OBJECT_PRESENCE",
                "security policy or authority object added or removed",
            )
        return 2, "RESOURCE_PRESENCE", "resource added or removed; impact is not measured"
    if kind == "Deployment" and (
        path == "metadata.annotations" or path.startswith("metadata.annotations.")
    ):
        return 1, "DEPLOYMENT_ANNOTATION", "deployment metadata changed"
    if kind == "Service" and (
        path == "metadata.annotations" or path.startswith("metadata.annotations.")
    ):
        return 2, "SERVICE_ANNOTATION", "service annotation may affect exposure or routing"
    if kind == "Role" and (
        path == "rules"
        or path.startswith(("rules.", "spec.rules."))
        or path == "spec.rules"
    ):
        if path.startswith("spec.rules"):
            if _role_rules_expand(live, target):
                return 3, "RBAC_RULES", "effective role permissions expanded"
            return 1, "RBAC_RULES", "effective role permissions narrowed or unchanged"
        return 3, "RBAC_RULES", "effective role permissions changed"
    if kind == "RoleBinding" and (
        path in {"subjects", "roleRef", "spec.subjects", "spec.roleRef"}
        or path.startswith(
            ("subjects.", "roleRef.", "spec.subjects.", "spec.roleRef.")
        )
    ):
        rule_id = (
            "RBAC_SUBJECTS"
            if path in {"subjects", "spec.subjects"}
            or path.startswith(("subjects.", "spec.subjects."))
            else "RBAC_ROLE_REF"
        )
        if path.startswith("spec."):
            if _role_binding_expands(live, target):
                return 3, rule_id, "role binding authority expanded"
            return 1, rule_id, "role binding authority narrowed or unchanged"
        return 3, rule_id, "role binding authority changed"
    if kind == "NetworkPolicy" and (path == "spec" or path.startswith("spec.")):
        return 3, "NETWORK_POLICY", "network isolation policy changed"
    if kind == "Deployment" and path.startswith("spec.template.spec.containers."):
        if ".securityContext" in path:
            return 3, "CONTAINER_SECURITY_CONTEXT", "container security control changed"
        if ".volumeMounts." in path:
            return 3, "CONTAINER_VOLUME_MOUNT", "container volume mount changed"
        if path.endswith(".resources") or ".resources." in path:
            return 1, "CONTAINER_RESOURCES", "container resource settings changed"
        if path.endswith(".env") or ".env." in path:
            return 1, "CONTAINER_ENV", "container environment changed"
    if kind == "Deployment" and path.startswith("spec.template.spec.volumes."):
        return 3, "VOLUME_SET", "pod volume configuration changed"
    for rule_kind, pattern, severity, rule_id, rationale in RULES:
        if kind == rule_kind and _matches_path(path, pattern):
            return severity, rule_id, rationale
    return 2, "UNMAPPED_FIELD", "field has no explicit baseline rule; classify conservatively"


def classify_event(event: dict[str, Any]) -> dict[str, Any]:
    """Classify an enriched collector event without model calls or side effects."""
    if (
        not isinstance(event, dict)
        or event.get("event_type") != "configuration_drift_detected"
    ):
        raise ValueError("unsupported baseline event type")
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not re.fullmatch(r"[a-f0-9]{64}", event_id):
        raise ValueError("event_id must be a SHA-256 hex digest")
    application = event.get("application")
    if not isinstance(application, dict) or (
        application.get("name") != APPLICATION_NAME
        or application.get("namespace") != APPLICATION_NAMESPACE
    ):
        raise ValueError("event application is outside the baseline allowlist")
    revision = event.get("revision")
    if not isinstance(revision, str) or not revision or len(revision) > 128:
        raise ValueError("event revision is required")
    timestamps = {}
    for field in ("t_detect", "received_at", "reconciled_at"):
        value = event.get(field)
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError(f"event {field} timestamp is required")
        try:
            parsed_timestamp = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"event {field} timestamp is invalid") from exc
        if parsed_timestamp.tzinfo is None:
            raise ValueError(f"event {field} timestamp must include a timezone")
        timestamps[field] = value
    resources = event.get("resources")
    if not isinstance(resources, list) or not resources:
        raise ValueError("event must contain at least one changed resource")

    results = []
    for resource in resources:
        if not isinstance(resource, dict):
            raise TypeError("resource entries must be objects")
        kind = resource.get("kind")
        name = resource.get("name")
        if kind not in ALLOWED_RESOURCES:
            raise ValueError("resource kind is outside the baseline allowlist")
        if not isinstance(name, str) or name not in ALLOWED_RESOURCES[kind]:
            raise ValueError("resource name is outside the baseline allowlist")
        if resource.get("namespace") != APPLICATION_NAMESPACE:
            raise ValueError("resource namespace is outside the baseline allowlist")
        if resource.get("status") != "OutOfSync":
            raise ValueError("resource is not marked OutOfSync")
        live = resource.get("live_state")
        target = resource.get("target_state")
        if live is not None and not isinstance(live, dict):
            raise TypeError("baseline live state must be an object or null")
        if target is not None and not isinstance(target, dict):
            raise TypeError("baseline target state must be an object or null")
        if live is None and target is None:
            raise ValueError("baseline resource must have a live or target object")

        paths = _changed_paths(live, target)
        if not paths:
            raise ValueError("resource has no comparable changed fields")
        findings = []
        for path in paths:
            severity, rule_id, rationale = _rule_for(kind, path, live, target)
            findings.append(
                {
                    "path": path,
                    "severity": severity,
                    "rule_id": rule_id,
                    "rationale": rationale,
                }
            )
        results.append(
            {
                "kind": kind,
                "name": resource.get("name"),
                "namespace": resource["namespace"],
                "findings": findings,
                "severity": max(item["severity"] for item in findings),
            }
        )

    severity = max(item["severity"] for item in results)
    drift_class = {0: "benign", 1: "operational", 2: "operational", 3: "critical"}[
        severity
    ]
    return {
        "event_id": event_id,
        "arm": "baseline",
        "ruleset_version": RULESET_VERSION,
        "application": application,
        "revision": revision,
        **timestamps,
        "severity": severity,
        "drift_class": drift_class,
        "decision": "hard_revert",
        "review_required": severity == 3,
        "resources": results,
    }
