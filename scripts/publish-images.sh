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

# WSL can inherit Docker Desktop's Windows credential helper, which may fail
# while storing the ECR login token. Use a private, short-lived config so the
# token is not written to the user's persistent Docker configuration.
DOCKER_CONFIG_DIR=""
if [[ -z "${DOCKER_CONFIG:-}" ]]; then
  DOCKER_CONFIG_DIR="$(mktemp -d)"
  chmod 700 "$DOCKER_CONFIG_DIR"
  export DOCKER_CONFIG="$DOCKER_CONFIG_DIR"
  trap 'rm -rf "$DOCKER_CONFIG_DIR"' EXIT
fi

aws ecr get-login-password --region "$REGION" "${PROFILE_ARGS[@]}" |
  docker login --username AWS --password-stdin "$REGISTRY"

publish() {
  local name="$1" dockerfile="$2"
  local image="$REGISTRY/$name:$IMAGE_TAG"
  if aws ecr describe-images \
    --repository-name "$name" \
    --image-ids "imageTag=$IMAGE_TAG" \
    --region "$REGION" "${PROFILE_ARGS[@]}" >/dev/null 2>&1; then
    echo "Reusing immutable image already published: $image"
    printf '%s\n' "$image"
    return 0
  fi
  docker build --file "$ROOT/$dockerfile" --tag "$image" "$ROOT"
  local attempt
  for attempt in 1 2 3; do
    if docker push "$image"; then
      break
    fi
    if [[ "$attempt" == 3 ]]; then
      echo "Failed to push $image after $attempt attempts." >&2
      return 1
    fi
    echo "Push attempt $attempt failed for $image; retrying in 5 seconds." >&2
    sleep 5
  done
  printf '%s\n' "$image"
}

publish drift-thesis-injector infra/injector/Dockerfile
publish drift-thesis-collector infra/collector/Dockerfile
publish drift-thesis-treatment-worker infra/orchestrator/Dockerfile
publish drift-thesis-baseline infra/baseline/Dockerfile

printf 'Published tag: %s\n' "$IMAGE_TAG"
