#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${AWS_PROFILE:-thesis-project}"
REGION="${AWS_REGION:-ap-southeast-2}"
EXPECTED_ACCOUNT="218733194774"
STATE_FILE="$ROOT/.drift-session"
LOG_FILE="$ROOT/docs/cost-log.md"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short=8 HEAD)-$(date -u +%Y%m%d%H%M%S)}"
export IMAGE_TAG
PLATFORM_DIR="$ROOT/infra/platform"
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

account="$(aws sts get-caller-identity --query Account --output text)"
[[ "$account" == "$EXPECTED_ACCOUNT" ]] || { echo "Refusing to deploy to AWS account $account (expected $EXPECTED_ACCOUNT)." >&2; exit 1; }
public_ip="$(curl -4fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
[[ "$public_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "Could not determine a valid public IPv4 address." >&2; exit 1; }
admin_cidr="$public_ip/32"
if [[ -f "$STATE_FILE" ]]; then
  [[ "${DRIFT_RESUME:-0}" == "1" ]] || { echo "An unfinished session record exists at $STATE_FILE. Set DRIFT_RESUME=1 to continue it, or run make down." >&2; exit 1; }
  mapfile -t session_record < "$STATE_FILE"
  session="${session_record[0]:-}"
  started="${session_record[1]:-}"
  [[ -n "$session" && -n "$started" ]] || { echo "The unfinished session record is invalid; inspect $STATE_FILE." >&2; exit 1; }
  echo "Resuming DRIFT session $session with refreshed EKS API CIDR $admin_cidr."
else
  session="$(date -u +%Y%m%dT%H%M%SZ)"
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s\n%s\n%s\n' "$session" "$started" "$admin_cidr" > "$STATE_FILE"
  printf '\n| %s | %s | — | In progress | Pending Cost Explorer |\n' "$session" "$started" >> "$LOG_FILE"
fi

on_error() {
  code=$?
  trap - ERR
  printf '\nSession %s failed during startup (exit %s); partial resources may remain. Run `make status`, then `make down`.\n' "$session" "$code" >&2
  exit "$code"
}
trap on_error ERR

echo "Starting DRIFT session $session in $REGION for account $account."
echo "The startup creates AWS infrastructure; treatment events remain disabled until runtime checks pass."

# Phase 1: provision AWS resources with the workflow pipe stopped so images can
# be published before any event can launch the treatment task.
terraform -chdir="$ROOT/infra" init -input=false
terraform -chdir="$ROOT/infra" apply -input=false -auto-approve \
  -var="admin_cidr=$admin_cidr" \
  -var="treatment_worker_image_tag=$IMAGE_TAG" \
  -var='treatment_workflow_enabled=false'

bash "$ROOT/scripts/publish-images.sh"

aws eks wait cluster-active --name drift-thesis-eks --region "$REGION"
aws eks update-kubeconfig --region "$REGION" --name drift-thesis-eks --alias drift-thesis-eks
nodegroup="$(aws eks list-nodegroups --cluster-name drift-thesis-eks --region "$REGION" --query 'nodegroups[0]' --output text)"
[[ -n "$nodegroup" && "$nodegroup" != "None" ]] || { echo "No managed node group found." >&2; false; }
aws eks wait nodegroup-active --cluster-name drift-thesis-eks --nodegroup-name "$nodegroup" --region "$REGION"
kubectl wait --for=condition=Ready nodes --all --timeout=15m

# Existing Secrets Manager metadata may outlive the cluster. Import it into the
# new empty platform state; secret values remain managed out of Terraform state.
terraform -chdir="$PLATFORM_DIR" init -input=false
ensure_secret() {
  local name="$1" seed_value="${2:-false}" arn deleted temp
  arn="$(aws secretsmanager describe-secret --secret-id "$name" --region "$REGION" --query ARN --output text 2>/dev/null || true)"
  if [[ -z "$arn" || "$arn" == "None" ]]; then
    arn="$(aws secretsmanager create-secret --name "$name" --description "DRIFT project runtime secret" --tags Key=project,Value=drift-thesis --region "$REGION" --query ARN --output text)"
  else
    deleted="$(aws secretsmanager describe-secret --secret-id "$name" --region "$REGION" --query DeletedDate --output text 2>/dev/null || true)"
    if [[ -n "$deleted" && "$deleted" != "None" ]]; then
      aws secretsmanager restore-secret --secret-id "$name" --region "$REGION" >/dev/null
    fi
  fi
  if [[ "$seed_value" == "true" ]] && ! aws secretsmanager get-secret-value --secret-id "$arn" --region "$REGION" --query SecretString --output text >/dev/null 2>&1; then
    temp="$(mktemp)"
    openssl rand -hex 32 > "$temp"
    aws secretsmanager put-secret-value --secret-id "$arn" --secret-string "file://$temp" --region "$REGION" >/dev/null
    rm -f "$temp"
  fi
}
ensure_secret drift-thesis-argocd-webhook true
ensure_secret drift-thesis-argocd-collector-api-token
for address in argocd_webhook argocd_collector_api_token; do
  if ! terraform -chdir="$PLATFORM_DIR" state list | grep -Fq "aws_secretsmanager_secret.$address"; then
    name="drift-thesis-$([[ "$address" == argocd_webhook ]] && echo argocd-webhook || echo argocd-collector-api-token)"
    arn="$(aws secretsmanager describe-secret --secret-id "$name" --region "$REGION" --query ARN --output text 2>/dev/null || true)"
    if [[ -n "$arn" && "$arn" != "None" ]]; then
      terraform -chdir="$PLATFORM_DIR" import "aws_secretsmanager_secret.$address" "$arn"
    fi
  fi
done

collector_url="$(terraform -chdir="$ROOT/infra" output -raw drift_collector_api_url)"
# Install the CRD provider before Terraform plans resources using ExternalSecret.
# Kubernetes provider schema discovery otherwise fails on a fresh cluster.
terraform -chdir="$PLATFORM_DIR" apply -input=false -auto-approve \
  -target=helm_release.argocd \
  -target=helm_release.external_secrets \
  -var="drift_collector_webhook_url=$collector_url"
kubectl -n external-secrets rollout status deployment/external-secrets --timeout=10m
kubectl -n argocd rollout status deployment/argocd-server --timeout=10m
kubectl wait --for=condition=Established \
  crd/externalsecrets.external-secrets.io --timeout=5m

terraform -chdir="$PLATFORM_DIR" apply -input=false -auto-approve \
  -var="drift_collector_webhook_url=$collector_url"
kubectl -n external-secrets rollout status deployment/external-secrets --timeout=10m
kubectl -n argocd rollout status deployment/argocd-server --timeout=10m
kubectl apply -f "$PLATFORM_DIR/repo-access.yaml"

for attempt in $(seq 1 60); do
  ready="$(kubectl -n argocd get externalsecret drift-gitops-repo -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  [[ "$ready" == "True" ]] && break
  sleep 10
done
[[ "${ready:-}" == "True" ]] || { echo "ExternalSecret drift-gitops-repo did not become Ready; check the SecretStore and deploy-key permissions." >&2; false; }

# Recreate the Argo CD TLS certificate needed by the in-cluster collector.
cert_dir="$(mktemp -d)"
port_forward_pid=""
cleanup() {
  [[ -z "$port_forward_pid" ]] || kill "$port_forward_pid" 2>/dev/null || true
  rm -rf "$cert_dir"
}
trap cleanup EXIT
kubectl get secret argocd-secret -n argocd -o jsonpath='{.data.tls\.crt}' | base64 --decode > "$cert_dir/argocd.crt"
kubectl create configmap drift-argocd-server-ca -n argocd \
  --from-file=argocd.crt="$cert_dir/argocd.crt" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f "$PLATFORM_DIR/online-boutique-application.yaml"
for attempt in $(seq 1 60); do
  sync="$(kubectl -n argocd get application online-boutique -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
  health="$(kubectl -n argocd get application online-boutique -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
  [[ "$sync" == "Synced" && "$health" == "Healthy" ]] && break
  sleep 10
done
[[ "${sync:-}" == "Synced" && "${health:-}" == "Healthy" ]] || { echo "Online Boutique did not become Synced/Healthy within 10 minutes." >&2; false; }
kubectl -n online-boutique rollout status deployment/frontend --timeout=10m

# Create a fresh read-only Argo CD token and store it directly in Secrets Manager.
# The CLI token is never printed or written to Terraform state.
if ! command -v argocd >/dev/null; then
  tool_dir="${TMPDIR:-/tmp}/drift-tools"
  mkdir -p "$tool_dir"
  curl -fsSL https://github.com/argoproj/argo-cd/releases/latest/download/argocd-linux-amd64 -o "$tool_dir/argocd"
  chmod 700 "$tool_dir/argocd"
  PATH="$tool_dir:$PATH"
fi
kubectl -n argocd port-forward svc/argocd-server 8443:443 > "$cert_dir/port-forward.log" 2>&1 &
port_forward_pid=$!
for attempt in $(seq 1 30); do
  curl -kfsS https://127.0.0.1:8443/healthz >/dev/null 2>&1 && break
  sleep 2
done
admin_password="$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 --decode)"
argocd login localhost:8443 --insecure --username admin --password "$admin_password" --grpc-web >/dev/null
collector_token="$(argocd account generate-token --account drift-collector --grpc-web)"
printf '%s' "$collector_token" > "$cert_dir/token"
token_secret_arn="$(aws secretsmanager describe-secret --secret-id drift-thesis-argocd-collector-api-token --region "$REGION" --query ARN --output text)"
aws secretsmanager put-secret-value --secret-id "$token_secret_arn" --secret-string "file://$cert_dir/token" --region "$REGION" >/dev/null
unset admin_password collector_token
kill "$port_forward_pid" 2>/dev/null || true
port_forward_pid=""
trap - EXIT
cleanup

webhook_secret_arn="$(aws secretsmanager describe-secret --secret-id drift-thesis-argocd-webhook --region "$REGION" --query ARN --output text)"
token_secret_arn="$(aws secretsmanager describe-secret --secret-id drift-thesis-argocd-collector-api-token --region "$REGION" --query ARN --output text)"
rendered_collector="$(mktemp)"
rendered_baseline="$(mktemp)"
IMAGE_TAG="$IMAGE_TAG" WEBHOOK_SECRET_ARN="$webhook_secret_arn" COLLECTOR_TOKEN_SECRET_ARN="$token_secret_arn" python3 - "$PLATFORM_DIR/collector-runtime.yaml" "$rendered_collector" <<'PY'
import os
import pathlib
import sys

source, destination = map(pathlib.Path, sys.argv[1:])
text = source.read_text(encoding="utf-8")
for key in ("IMAGE_TAG", "WEBHOOK_SECRET_ARN", "COLLECTOR_TOKEN_SECRET_ARN"):
    text = text.replace(f"__{key}__", os.environ[key])
destination.write_text(text, encoding="utf-8")
PY
IMAGE_TAG="$IMAGE_TAG" python3 - "$PLATFORM_DIR/baseline-runtime.yaml" "$rendered_baseline" <<'PY'
import os
import pathlib
import sys

source, destination = map(pathlib.Path, sys.argv[1:])
destination.write_text(source.read_text(encoding="utf-8").replace("__IMAGE_TAG__", os.environ["IMAGE_TAG"]), encoding="utf-8")
PY
kubectl apply -f "$rendered_collector"
kubectl apply -f "$PLATFORM_DIR/baseline-rbac.yaml"
kubectl apply -f "$rendered_baseline"
rm -f "$rendered_collector" "$rendered_baseline"
kubectl -n argocd rollout status deployment/drift-collector --timeout=10m

# Enable event processing only after the complete runtime is available.
knowledge_base_id="$(terraform -chdir="$ROOT/infra" output -raw treatment_knowledge_base_id)"
knowledge_base_arn="arn:aws:bedrock:${REGION}:${account}:knowledge-base/${knowledge_base_id}"
bedrock_access="$(aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::${account}:role/drift-thesis-treatment-task" \
  --action-names bedrock:Retrieve \
  --resource-arns "$knowledge_base_arn" \
  --query 'EvaluationResults[0].[EvalDecision,OrganizationsDecisionDetail.AllowedByOrganizations]' \
  --output text)"
read -r bedrock_decision organizations_allowed <<< "$bedrock_access"
if [[ "$bedrock_decision" != "allowed" || "$organizations_allowed" != "True" ]]; then
  echo "Treatment pipe remains stopped: bedrock:Retrieve is not allowed by IAM and Organizations (decision=$bedrock_decision, organizations=$organizations_allowed). Resolve access, then resume with DRIFT_RESUME=1 make up." >&2
  false
fi

terraform -chdir="$ROOT/infra" apply -input=false -auto-approve \
  -var="admin_cidr=$admin_cidr" \
  -var="treatment_worker_image_tag=$IMAGE_TAG" \
  -var='treatment_workflow_enabled=true'

finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
elapsed="$(( $(date -u +%s) - $(date -u -d "$started" +%s) ))"
printf '\n| %s | %s | %s | Up completed in %s seconds | Pending Cost Explorer |\n' "$session" "$started" "$finished" "$elapsed" >> "$LOG_FILE"
echo "DRIFT is ready. The collector is active, the baseline consumer remains at zero replicas, and the treatment pipe is running."
trap - ERR
