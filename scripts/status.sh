#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${AWS_PROFILE:-thesis-project}"
REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
echo '=== EKS cluster ==='
aws eks describe-cluster --name drift-thesis-eks --region "$REGION" --query 'cluster.status' --output text 2>&1 || true
echo '=== Kubernetes nodes ==='
kubectl get nodes -o wide 2>&1 || true
echo '=== Online Boutique pods ==='
kubectl get pods -n online-boutique 2>&1 || true
echo '=== Argo CD application ==='
kubectl get application online-boutique -n argocd 2>&1 || true
echo '=== Platform Terraform state ==='
terraform -chdir="$ROOT/infra/platform" state list 2>&1 || true
echo '=== Infra Terraform state ==='
terraform -chdir="$ROOT/infra" state list 2>&1 || true
