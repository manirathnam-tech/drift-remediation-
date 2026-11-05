#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_REGION:-ap-southeast-2}"
PROFILE_ARGS=()
if [[ -n "${AWS_PROFILE:-}" ]]; then
  PROFILE_ARGS=(--profile "$AWS_PROFILE")
fi

ACCOUNT_ID="$(aws sts get-caller-identity "${PROFILE_ARGS[@]}" --query Account --output text)"
REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short=12 HEAD)}"

aws ecr get-login-password --region "$REGION" "${PROFILE_ARGS[@]}" |
  docker login --username AWS --password-stdin "$REGISTRY"

publish() {
  local name="$1" dockerfile="$2"
  local image="$REGISTRY/$name:$IMAGE_TAG"
  docker build --file "$ROOT/$dockerfile" --tag "$image" "$ROOT"
  docker push "$image"
  printf '%s\n' "$image"
}

publish drift-thesis-injector infra/injector/Dockerfile
publish drift-thesis-collector infra/collector/Dockerfile
publish drift-thesis-treatment-worker infra/orchestrator/Dockerfile
publish drift-thesis-baseline infra/baseline/Dockerfile

printf 'Published tag: %s\n' "$IMAGE_TAG"
