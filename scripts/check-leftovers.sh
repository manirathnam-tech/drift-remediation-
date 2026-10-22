#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s inherit_errexit
PROFILE="${AWS_PROFILE:-thesis-project}"
REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
failed=0
report() {
  local label="$1" result="$2"
  result="$(printf '%s' "$result" | tr -d '[:space:]')"
  if [[ -n "$result" && "$result" != "None" ]]; then
    printf '\nLEFTOVER %s:\n%s\n' "$label" "$2"
    failed=1
  else
    printf 'clear: %s\n' "$label"
  fi
}
report 'project-tagged NAT gateways' "$(aws ec2 describe-nat-gateways --region "$REGION" --filter Name=tag:project,Values=drift-thesis Name=state,Values=pending,available,deleting --query 'NatGateways[].NatGatewayId' --output text)"
report 'project-tagged Elastic IPs' "$(aws ec2 describe-addresses --region "$REGION" --filters Name=tag:project,Values=drift-thesis --query 'Addresses[].AllocationId' --output text)"
report 'project-tagged EBS volumes' "$(aws ec2 describe-volumes --region "$REGION" --filters Name=tag:project,Values=drift-thesis --query 'Volumes[].VolumeId' --output text)"
report 'project-tagged network interfaces' "$(aws ec2 describe-network-interfaces --region "$REGION" --filters Name=tag:project,Values=drift-thesis --query 'NetworkInterfaces[].NetworkInterfaceId' --output text)"
report 'project-tagged EC2 instances' "$(aws ec2 describe-instances --region "$REGION" --filters Name=tag:project,Values=drift-thesis Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)"
report 'drift-thesis EKS cluster' "$(aws eks list-clusters --region "$REGION" --query 'clusters[?contains(@, `drift-thesis`)]' --output text)"
report 'project-tagged load balancers' "$(aws resourcegroupstaggingapi get-resources --region "$REGION" --resource-type-filters elasticloadbalancing:loadbalancer --tag-filters Key=project,Values=drift-thesis --query 'ResourceTagMappingList[].ResourceARN' --output text)"
if (( failed )); then
  echo 'Some project-tagged resources remain. Check AWS service state and rerun make check-leftovers.' >&2
  exit 1
fi
echo 'No matching project-tagged leftovers found. Untagged resources are outside this inventory.'