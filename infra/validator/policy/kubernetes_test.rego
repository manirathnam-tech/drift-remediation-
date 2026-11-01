package main

import rego.v1

safe_pod := {
    "kind": "Pod", "metadata": {"name": "test"},
    "spec": {
        "securityContext": {"runAsNonRoot": true},
        "automountServiceAccountToken": false,
        "containers": [{"name": "app", "image": "example:v1",
            "resources": {"requests": {"cpu": "10m"}, "limits": {"cpu": "100m"}}}],
    },
}

test_safe_pod if {
    count(deny) == 0 with input as safe_pod
}

test_host_flags if {
    every flag in ["hostNetwork", "hostPID", "hostIPC", "automountServiceAccountToken"] {
        changed := object.union(safe_pod, {"spec": object.union(safe_pod.spec, {flag: true})})
        count(deny) == 1 with input as changed
    }
}

test_root if {
    changed := json.patch(safe_pod, [{"op": "remove", "path": "/spec/securityContext"}])
    count(deny) == 1 with input as changed
}

test_privileged if {
    container := object.union(safe_pod.spec.containers[0], {"securityContext": {"privileged": true}})
    changed := object.union(safe_pod, {"spec": object.union(safe_pod.spec, {"containers": [container]})})
    count(deny) == 1 with input as changed
}

test_host_path if {
    changed := object.union(safe_pod, {"spec": object.union(safe_pod.spec, {"volumes": [{"hostPath": {"path": "/"}}]})})
    count(deny) == 1 with input as changed
}

test_privileged_init_and_ephemeral_containers if {
    every field in ["initContainers", "ephemeralContainers"] {
        container := object.union(safe_pod.spec.containers[0], {"securityContext": {"privileged": true}})
        changed := object.union(safe_pod, {"spec": object.union(safe_pod.spec, {field: [container]})})
        count(deny) == 1 with input as changed
    }
}

test_resource_requirements if {
    every key in ["requests", "limits"] {
        changed := json.patch(safe_pod, [{"op": "remove", "path": sprintf("/spec/containers/0/resources/%s", [key])}])
        count(deny) == 1 with input as changed
    }
}

test_workload_templates if {
    every kind in ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"] {
        changed := {"kind": kind, "metadata": {"name": "test"}, "spec": {"template": {"spec": safe_pod.spec}}}
        count(deny) == 0 with input as changed
    }
}

test_cronjob_template if {
    changed := {"kind": "CronJob", "metadata": {"name": "test"}, "spec": {"jobTemplate": {"spec": {"template": {"spec": safe_pod.spec}}}}}
    count(deny) == 0 with input as changed
}

test_rbac_wildcards if {
    every field in ["verbs", "resources", "apiGroups"] {
        changed := {"kind": "Role", "metadata": {"name": "test"}, "rules": [{field: ["*"]}]}
        count(deny) == 1 with input as changed
    }
}

test_admin_binding if {
    changed := {"kind": "RoleBinding", "metadata": {"name": "test"}, "roleRef": {"kind": "ClusterRole", "name": "cluster-admin"}}
    count(deny) == 1 with input as changed
}

test_public_services if {
    every kind in ["NodePort", "LoadBalancer"] {
        changed := {"kind": "Service", "metadata": {"name": "test"}, "spec": {"type": kind}}
        count(deny) == 1 with input as changed
    }
    count(deny) == 0 with input as {"kind": "Service", "metadata": {"name": "test"}, "spec": {"type": "ClusterIP"}}
}

test_network_policy if {
    count(deny) > 0 with input as {"kind": "NetworkPolicy", "metadata": {"name": "test"}, "spec": {}}
    count(deny) == 0 with input as {"kind": "NetworkPolicy", "metadata": {"name": "test"}, "spec": {"podSelector": {}, "policyTypes": ["Ingress"]}}
}
