#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${AWS_PROFILE:-thesis-project}"
REGION="${AWS_REGION:-ap-southeast-2}"
EXPECTED_ACCOUNT="218733194774"
STATE_FILE="$ROOT/.drift-session"
LOG_FILE="$ROOT/docs/cost-log.md"

export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
account="$(aws sts get-caller-identity --query Account --output text)"
if [[ "$account" != "$EXPECTED_ACCOUNT" ]]; then
  echo "Refusing to deploy to AWS account $account (expected $EXPECTED_ACCOUNT)." >&2
  exit 1
fi
if [[ -f "$STATE_FILE" ]]; then
  echo "An unfinished session record exists at $STATE_FILE. Run make status and make down first." >&2
  exit 1
fi

public_ip="$(curl -4fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
[[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "Could not determine a valid public IPv4 address." >&2; exit 1; }
admin_cidr="$public_ip/32"
session="$(date -u +%Y%m%dT%H%M%SZ)"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s\n%s\n%s\n' "$session" "$started" "$admin_cidr" > "$STATE_FILE"
printf '\n| %s | %s | — | In progress | Pending Cost Explorer |\n' "$session" "$started" >> "$LOG_FILE"

on_error() {
  code=$?
  trap - ERR
  printf '\nSession %s failed during startup (exit %s); partial resources may remain. Run `make status`, then `make down`.\n' "$session" "$code" >&2
  exit "$code"
}
trap on_error ERR

echo "Starting session $session in $REGION; EKS API allowed CIDR: $admin_cidr"
terraform -chdir="$ROOT/infra" init -input=false
terraform -chdir="$ROOT/infra" apply -input=false -auto-approve -var="admin_cidr=$admin_cidr"
aws eks wait cluster-active --name drift-thesis-eks --region "$REGION"
aws eks update-kubeconfig --region "$REGION" --name drift-thesis-eks --alias drift-thesis-eks

# Wait for the EKS node group and Kubernetes API to be ready.
nodegroup="$(aws eks list-nodegroups --cluster-name drift-thesis-eks --region "$REGION" --query 'nodegroups[0]' --output text)"
[[ -n "$nodegroup" && "$nodegroup" != "None" ]] || { echo "No managed node group found." >&2; false; }
aws eks wait nodegroup-active --cluster-name drift-thesis-eks --nodegroup-name "$nodegroup" --region "$REGION"
kubectl wait --for=condition=Ready nodes --all --timeout=15m

terraform -chdir="$ROOT/infra/platform" init -input=false
terraform -chdir="$ROOT/infra/platform" apply -input=false -auto-approve
kubectl -n external-secrets rollout status deployment/external-secrets --timeout=10m
kubectl -n argocd rollout status deployment/argocd-server --timeout=10m
kubectl apply -f "$ROOT/infra/platform/repo-access.yaml"

# Wait until External Secrets has created Argo CD's Git repository credential.
for attempt in $(seq 1 60); do
  ready="$(kubectl -n argocd get externalsecret drift-gitops-repo -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  [[ "$ready" == "True" ]] && break
  sleep 10
done
[[ "${ready:-}" == "True" ]] || { echo "ExternalSecret drift-gitops-repo did not become Ready." >&2; false; }
kubectl apply -f "$ROOT/infra/platform/online-boutique-application.yaml"

# Wait for Argo CD to sync and report the application healthy.
for attempt in $(seq 1 60); do
  sync="$(kubectl -n argocd get application online-boutique -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
  health="$(kubectl -n argocd get application online-boutique -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
  [[ "$sync" == "Synced" && "$health" == "Healthy" ]] && break
  sleep 10
done
[[ "${sync:-}" == "Synced" && "${health:-}" == "Healthy" ]] || { echo "Online Boutique did not reach Synced/Healthy within 10 minutes." >&2; false; }
kubectl -n online-boutique rollout status deployment/frontend --timeout=10m

finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
elapsed="$(( $(date -u +%s) - $(date -u -d "$started" +%s) ))"
printf '\n| %s | %s | %s | Up completed in %s seconds | Pending Cost Explorer |\n' "$session" "$started" "$finished" "$elapsed" >> "$LOG_FILE"
trap - ERR
