#!/usr/bin/env bash
set -Eeuo pipefail

image_uri="${1:?image URI is required}"
aws_region="${2:?AWS region is required}"
aws_account_id="${3:?AWS account ID is required}"
deploy_dir="${4:?deployment directory is required}"
registry="${aws_account_id}.dkr.ecr.${aws_region}.amazonaws.com"

for command_name in aws docker; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is missing on EC2: $command_name" >&2
    exit 1
  fi
done
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required on EC2." >&2
  exit 1
fi

cd "$deploy_dir"

previous_container="$(docker compose ps -q app)"
previous_image=""
if [[ -n "$previous_container" ]]; then
  previous_image="$(docker inspect --format '{{.Config.Image}}' "$previous_container")"
fi

aws ecr get-login-password --region "$aws_region" |
  docker login --username AWS --password-stdin "$registry"
docker pull "$image_uri"

wait_for_healthy() {
  local timeout_seconds="${1:-300}"
  local container_id
  local health
  local deadline=$((SECONDS + timeout_seconds))

  while (( SECONDS < deadline )); do
    container_id="$(docker compose ps -q app)"
    if [[ -n "$container_id" ]]; then
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
      if [[ "$health" == "healthy" ]]; then
        return 0
      fi
      if [[ "$health" == "unhealthy" || "$health" == "exited" || "$health" == "dead" ]]; then
        return 1
      fi
    fi
    sleep 5
  done
  return 1
}

rollback() {
  if [[ -z "$previous_image" ]]; then
    echo "No previous app image is available for rollback." >&2
    return 1
  fi

  echo "Restoring previous app image: $previous_image"
  if ! docker image inspect "$previous_image" >/dev/null 2>&1; then
    docker pull "$previous_image"
  fi
  DOCQA_IMAGE="$previous_image" docker compose up -d --no-deps app
  if wait_for_healthy 300; then
    echo "Previous app image is healthy again."
    return 0
  fi

  echo "Rollback app did not become healthy." >&2
  docker compose logs --tail=100 app >&2 || true
  return 1
}

echo "Starting app image: $image_uri"
DOCQA_IMAGE="$image_uri" docker compose up -d --no-deps app
if wait_for_healthy 300; then
  echo "Deployment is healthy: $image_uri"
  exit 0
fi

echo "New app image did not become healthy; attempting rollback." >&2
docker compose logs --tail=100 app >&2 || true
rollback
exit 1
