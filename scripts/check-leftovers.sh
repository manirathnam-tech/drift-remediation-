#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s inherit_errexit
PROFILE="${AWS_PROFILE:-thesis-project}"
REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
export AWS_PAGER="" AWS_CLI_AUTO_PROMPT=off
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
report_advisory() {
  local label="$1" result="$2"
  result="$(printf '%s' "$result" | tr -d '[:space:]')"
  if [[ -n "$result" && "$result" != "None" ]]; then
    printf '\nADVISORY %s (verify with the service-specific checks below):\n%s\n' "$label" "$2"
  else
    printf 'clear: %s\n' "$label"
  fi
}
inventory() {
  local mode="$1" label="$2" result status
  shift 2
  if result="$("$@" 2>&1)"; then
    if [[ "$mode" == "advisory" ]]; then
      report_advisory "$label" "$result"
    else
      report "$label" "$result"
    fi
  else
    status=$?
    printf '\nERROR inventory for %s; command failed (exit %s):\n%s\n' \
      "$label" "$status" "$result" >&2
    failed=1
  fi
}

inventory required 'project-tagged NAT gateways' aws ec2 describe-nat-gateways --region "$REGION" --filter Name=tag:project,Values=drift-thesis Name=state,Values=pending,available,deleting --query 'NatGateways[].NatGatewayId' --output text
inventory required 'project-tagged Elastic IPs' aws ec2 describe-addresses --region "$REGION" --filters Name=tag:project,Values=drift-thesis --query 'Addresses[].AllocationId' --output text
inventory required 'project-tagged EBS volumes' aws ec2 describe-volumes --region "$REGION" --filters Name=tag:project,Values=drift-thesis --query 'Volumes[].VolumeId' --output text
inventory required 'project-tagged network interfaces' aws ec2 describe-network-interfaces --region "$REGION" --filters Name=tag:project,Values=drift-thesis --query 'NetworkInterfaces[].NetworkInterfaceId' --output text
inventory required 'project-tagged EC2 instances' aws ec2 describe-instances --region "$REGION" --filters Name=tag:project,Values=drift-thesis Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text
inventory required 'drift-thesis EKS cluster' aws eks list-clusters --region "$REGION" --query 'clusters[?contains(@, `drift-thesis`)]' --output text
inventory advisory 'project-tagged load balancers' aws resourcegroupstaggingapi get-resources --region "$REGION" --resource-type-filters elasticloadbalancing:loadbalancer --tag-filters Key=project,Values=drift-thesis --query 'ResourceTagMappingList[].ResourceARN' --output text
inventory advisory 'all project-tagged resources supported by Resource Groups Tagging API' aws resourcegroupstaggingapi get-resources --region "$REGION" --tag-filters Key=project,Values=drift-thesis --query 'ResourceTagMappingList[].ResourceARN' --output text

# Prefix-based checks cover billable or security-sensitive services even when a
# resource is untagged or its service is not supported by the tagging API.
inventory required 'project SQS queues' aws sqs list-queues --region "$REGION" --queue-name-prefix drift-thesis- --query 'QueueUrls[]' --output text
inventory required 'project DynamoDB tables' aws dynamodb list-tables --region "$REGION" --query 'TableNames[?starts_with(@, `drift-thesis-`)]' --output text
inventory required 'project ECR repositories' aws ecr describe-repositories --region "$REGION" --query 'repositories[?starts_with(repositoryName, `drift-thesis-`)].repositoryArn' --output text
inventory required 'project Lambda functions' aws lambda list-functions --region "$REGION" --query 'Functions[?starts_with(FunctionName, `drift-thesis-`)].FunctionName' --output text
inventory advisory 'retained project Secrets Manager secrets' aws secretsmanager list-secrets --region "$REGION" --query 'SecretList[?starts_with(Name, `drift-`)].Name' --output text
inventory required 'project SNS topics' aws sns list-topics --region "$REGION" --query 'Topics[?contains(TopicArn, `drift-thesis-`)].TopicArn' --output text
inventory required 'project API Gateway APIs' aws apigatewayv2 get-apis --region "$REGION" --query 'Items[?starts_with(Name, `drift-thesis-`)].ApiId' --output text
inventory required 'project Step Functions state machines' aws stepfunctions list-state-machines --region "$REGION" --query 'stateMachines[?starts_with(name, `drift-thesis-`)].stateMachineArn' --output text
inventory required 'project EventBridge Pipes' aws pipes list-pipes --region "$REGION" --query 'Pipes[?starts_with(Name, `drift-thesis-`)].Arn' --output text
inventory required 'project ECS clusters' aws ecs list-clusters --region "$REGION" --query 'clusterArns[?contains(@, `drift-thesis-`)]' --output text
inventory required 'project CloudWatch log groups' aws logs describe-log-groups --region "$REGION" --query 'logGroups[?contains(logGroupName, `drift-thesis-`)].logGroupName' --output text
inventory required 'project S3 buckets' aws s3api list-buckets --region "$REGION" --query 'Buckets[?starts_with(Name, `drift-thesis-`)].Name' --output text
inventory required 'project Bedrock knowledge bases' aws bedrock-agent list-knowledge-bases --region "$REGION" --query 'knowledgeBaseSummaries[?starts_with(name, `drift-thesis-`)].knowledgeBaseId' --output text
inventory required 'project IAM roles' aws iam list-roles --query 'Roles[?starts_with(RoleName, `drift-thesis-`)].RoleName' --output text
if (( failed )); then
  echo 'Some project resources remain. Review each item before deciding whether to retain or remove it.' >&2
  exit 1
fi
echo 'No matching project-tagged resources or known project-name prefixes found. Untagged resources with unrelated names may remain.'
