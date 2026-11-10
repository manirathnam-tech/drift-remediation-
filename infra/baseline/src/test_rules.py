"""Twenty representative collector events for the baseline ruleset."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from rules import RULESET_VERSION, classify_event

REVISION = "e6941d7b11734ea2df18db7c91ff737f947f2380"
UTC_TIME = "2026-11-05T12:00:00Z"


def _deployment() -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"annotations": {"demo": "before"}},
        "spec": {
            "replicas": 1,
            "template": {
                "metadata": {"annotations": {"demo": "before"}},
                "spec": {
                    "containers": [
                        {
                            "name": "cartservice",
                            "image": "example/cartservice:v1",
                            "resources": {"limits": {"cpu": "100m"}},
                            "env": [{"name": "MODE", "value": "safe"}],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                            },
                            "volumeMounts": [
                                {"name": "data", "mountPath": "/data"},
                            ],
                        }
                    ],
                    "securityContext": {"runAsNonRoot": True},
                    "hostNetwork": False,
                    "volumes": [{"name": "data", "configMap": {"name": "config-a"}}],
                },
            },
        },
    }


def _changed_deployment(path: str, value: Any) -> dict[str, Any]:
    result = deepcopy(_deployment())
    current: Any = result
    parts = path.split(".")
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    key = parts[-1]
    current[int(key) if isinstance(current, list) else key] = value
    return result


def _service() -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"annotations": {"demo": "before"}},
        "spec": {
            "type": "ClusterIP",
            "externalIPs": [],
            "ports": [{"port": 8080, "nodePort": 30080}],
        },
    }


def _event(kind: str, name: str, live: Any, target: Any) -> dict[str, Any]:
    return {
        "event_type": "configuration_drift_detected",
        "event_id": "a" * 64,
        "application": {"name": "online-boutique", "namespace": "online-boutique"},
        "revision": REVISION,
        "t_detect": UTC_TIME,
        "received_at": UTC_TIME,
        "reconciled_at": UTC_TIME,
        "resources": [
            {
                "kind": kind,
                "name": name,
                "namespace": "online-boutique",
                "status": "OutOfSync",
                "live_state": live,
                "target_state": target,
            }
        ],
    }


CASES = [
    pytest.param(
        "Deployment", "cartservice", _deployment(), _changed_deployment("spec.replicas", 2),
        1, "REPLICA_COUNT", id="replica-count",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("metadata.annotations.demo", "after"),
        1, "DEPLOYMENT_ANNOTATION", id="deployment-annotation",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.metadata.annotations.demo", "after"),
        2, "POD_ANNOTATION", id="pod-annotation",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.spec.containers.0.image", "example/cartservice:v2"),
        1, "CONTAINER_IMAGE", id="container-image",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.spec.containers.0.resources.limits.cpu", "200m"),
        1, "CONTAINER_RESOURCES", id="container-resources",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.spec.containers.0.env.0.value", "changed"),
        1, "CONTAINER_ENV", id="container-environment",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.spec.containers.0.securityContext.allowPrivilegeEscalation", True),
        3, "CONTAINER_SECURITY_CONTEXT", id="container-security-context",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.spec.securityContext.runAsNonRoot", False),
        3, "POD_SECURITY_CONTEXT", id="pod-security-context",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.spec.hostNetwork", True),
        3, "HOST_NAMESPACE", id="host-network",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.spec.containers.0.volumeMounts.0.mountPath", "/host"),
        3, "CONTAINER_VOLUME_MOUNT", id="container-volume-mount",
    ),
    pytest.param(
        "Deployment", "cartservice", _deployment(),
        _changed_deployment("spec.template.spec.volumes.0.configMap.name", "config-b"),
        3, "VOLUME_SET", id="pod-volume-source",
    ),
    pytest.param(
        "Deployment", "cartservice", None, _deployment(),
        2, "RESOURCE_PRESENCE", id="resource-created-live-missing",
    ),
    pytest.param(
        "Service", "frontend-external", _service(), None,
        2, "RESOURCE_PRESENCE", id="resource-deleted-from-target",
    ),
    pytest.param(
        "Service", "frontend-external", _service(),
        {**_service(), "spec": {**_service()["spec"], "type": "NodePort"}},
        3, "SERVICE_TYPE", id="service-type",
    ),
    pytest.param(
        "Service", "frontend-external", _service(),
        {**_service(), "spec": {**_service()["spec"], "externalIPs": ["203.0.113.10"]}},
        3, "SERVICE_EXTERNAL_IP", id="service-external-ip",
    ),
    pytest.param(
        "Service", "frontend-external", _service(),
        {**_service(), "spec": {**_service()["spec"], "ports": [{"port": 8080, "nodePort": 30081}]}},
        3, "SERVICE_NODE_PORT", id="service-node-port",
    ),
    pytest.param(
        "Service", "frontend-external", _service(),
        {**_service(), "spec": {**_service()["spec"], "ports": [{"port": 8443, "nodePort": 30080}]}},
        1, "SERVICE_PORT", id="service-port",
    ),
    pytest.param(
        "Role", "drift-observer",
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "rules": [{"verbs": ["get"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "rules": [{"verbs": ["get", "list"]}]},
        3, "RBAC_RULES", id="role-rules",
    ),
    pytest.param(
        "RoleBinding", "drift-observer",
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "subjects": [{"name": "reader"}], "roleRef": {"name": "read"}},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "subjects": [{"name": "admin"}], "roleRef": {"name": "read"}},
        3, "RBAC_SUBJECTS", id="role-binding-subjects",
    ),
    pytest.param(
        "NetworkPolicy", "allow-cartservice-to-redis-cart",
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "spec": {"podSelector": {"matchLabels": {"app": "cart"}}}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "spec": {"podSelector": {"matchLabels": {"app": "frontend"}}}},
        3, "NETWORK_POLICY", id="network-policy",
    ),
]


@pytest.mark.parametrize(
    ("kind", "name", "live", "target", "severity", "rule_id"), CASES
)
def test_twenty_representative_drift_events(
    kind: str,
    name: str,
    live: Any,
    target: Any,
    severity: int,
    rule_id: str,
):
    result = classify_event(_event(kind, name, live, target))

    assert result["severity"] == severity
    assert result["resources"][0]["findings"][0]["rule_id"] == rule_id
    assert result["arm"] == "baseline"
    assert result["ruleset_version"] == RULESET_VERSION
    assert result["revision"] == REVISION
    assert result["t_detect"] == UTC_TIME


def test_rejects_resource_outside_allowlist():
    event = _event("Deployment", "unapproved", _deployment(), _deployment())

    with pytest.raises(ValueError, match="resource name"):
        classify_event(event)


def test_rejects_resource_not_marked_out_of_sync():
    event = _event("Deployment", "cartservice", _deployment(), _deployment())
    event["resources"][0]["status"] = "Synced"

    with pytest.raises(ValueError, match="not marked OutOfSync"):
        classify_event(event)


def test_rejects_timestamp_without_timezone():
    event = _event("Deployment", "cartservice", _deployment(), _deployment())
    event["received_at"] = "2026-11-05T12:00:00"

    with pytest.raises(ValueError, match="include a timezone"):
        classify_event(event)
