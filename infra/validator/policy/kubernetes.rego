package main

import rego.v1

pod_spec := input.spec if input.kind == "Pod"
pod_spec := input.spec.template.spec if input.kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"}
pod_spec := input.spec.jobTemplate.spec.template.spec if input.kind == "CronJob"
pod_spec := input.spec.template.spec if input.kind == "Job"

pod_containers contains container if {
	some field in {"containers", "initContainers", "ephemeralContainers"}
	some container in object.get(pod_spec, field, [])
}

deny contains message if {
	some container in pod_containers
	object.get(object.get(container, "securityContext", {}), "privileged", false) == true
	message := sprintf("%s %s has a privileged container", [input.kind, input.metadata.name])
}

deny contains message if {
	spec := pod_spec
	some volume in object.get(spec, "volumes", [])
	object.get(volume, "hostPath", null) != null
	message := sprintf("%s %s uses a hostPath volume", [input.kind, input.metadata.name])
}

deny contains message if {
	spec := pod_spec
	object.get(spec, "hostNetwork", false) == true
	message := sprintf("%s %s enables hostNetwork", [input.kind, input.metadata.name])
}

deny contains message if {
	spec := pod_spec
	object.get(spec, "hostPID", false) == true
	message := sprintf("%s %s enables hostPID", [input.kind, input.metadata.name])
}

deny contains message if {
	spec := pod_spec
	object.get(spec, "hostIPC", false) == true
	message := sprintf("%s %s enables hostIPC", [input.kind, input.metadata.name])
}

deny contains message if {
	spec := pod_spec
	object.get(object.get(spec, "securityContext", {}), "runAsNonRoot", false) != true
	message := sprintf("%s %s must set pod-level runAsNonRoot: true", [input.kind, input.metadata.name])
}

deny contains message if {
	input.kind in {"Role", "ClusterRole"}
	some rule in object.get(input, "rules", [])
	some field in {"verbs", "resources", "apiGroups"}
	some value in object.get(rule, field, [])
	value == "*"
	message := sprintf("%s %s contains a wildcard RBAC grant", [input.kind, input.metadata.name])
}

deny contains message if {
	input.kind in {"RoleBinding", "ClusterRoleBinding"}
	input.roleRef.kind == "ClusterRole"
	input.roleRef.name == "cluster-admin"
	message := sprintf("%s %s binds cluster-admin", [input.kind, input.metadata.name])
}

deny contains message if {
	input.kind == "Service"
	input.spec.type in {"NodePort", "LoadBalancer"}
	message := sprintf("Service %s uses unapproved public-capable type %s", [input.metadata.name, input.spec.type])
}

deny contains message if {
	spec := pod_spec
	object.get(spec, "automountServiceAccountToken", false) == true
	message := sprintf("%s %s explicitly mounts a service-account token", [input.kind, input.metadata.name])
}

deny contains message if {
	some container in pod_containers
	resources := object.get(container, "resources", {})
	object.get(resources, "requests", null) == null
	message := sprintf("%s %s container %s must declare resource requests", [input.kind, input.metadata.name, container.name])
}

deny contains message if {
	some container in pod_containers
	resources := object.get(container, "resources", {})
	object.get(resources, "limits", null) == null
	message := sprintf("%s %s container %s must declare resource limits", [input.kind, input.metadata.name, container.name])
}

deny contains message if {
	input.kind == "NetworkPolicy"
	object.get(input.spec, "podSelector", null) == null
	message := sprintf("NetworkPolicy %s must set podSelector and policyTypes explicitly", [input.metadata.name])
}

deny contains message if {
	input.kind == "NetworkPolicy"
	object.get(input.spec, "policyTypes", []) == []
	message := sprintf("NetworkPolicy %s must set podSelector and policyTypes explicitly", [input.metadata.name])
}
