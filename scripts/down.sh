#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${AWS_PROFILE:-thesis-project}"
REGION="${AWS_REGION:-ap-southeast-2}"
EXPECTED_ACCOUNT="218733194774"
STATE_FILE="$ROOT/.drift-session"
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
account="$(aws sts get-caller-identity --query Account --output text)"
[[ "$account" == "$EXPECTED_ACCOUNT" ]] || { echo "Refusing teardown in AWS account $account (expected $EXPECTED_ACCOUNT)." >&2; exit 1; }

# Destroy Kubernetes add-ons before EKS, while the API and kubeconfig still work.
if cluster_status="$(aws eks describe-cluster --name drift-thesis-eks --region "$REGION" --query 'cluster.status' --output text 2>&1)"; then
  terraform -chdir="$ROOT/infra/platform" init -input=false
  terraform -chdir="$ROOT/infra/platform" destroy -input=false -auto-approve
elif [[ "$cluster_status" == *ResourceNotFoundException* ]]; then
  echo "EKS cluster is absent; skipping platform Helm teardown."
else
  echo "$cluster_status" >&2
  echo "Could not confirm EKS cluster state; stopping before infrastructure teardown." >&2
  exit 1
fi
terraform -chdir="$ROOT/infra" init -input=false
terraform -chdir="$ROOT/infra" destroy -input=false -auto-approve

if [[ -f "$STATE_FILE" ]]; then
  mapfile -t record < "$STATE_FILE"
  session="${record[0]}"
  started="${record[1]}"
  ended="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '| %s | %s | %s | Down completed | Pending Cost Explorer |
' "$session" "$started" "$ended" >> "$ROOT/docs/cost-log.md"
  printf 'Session %s: Terraform bootstrap state, experiment ledger and Secrets Manager deploy key were retained.\n' "$session" >> "$ROOT/docs/cost-log.md"
  rm -f "$STATE_FILE"
fi

bash "$ROOT/scripts/check-leftovers.sh"
