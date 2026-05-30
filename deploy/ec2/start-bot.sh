#!/usr/bin/env bash
# Pull the slim image from ECR and (re)start the bot container.
# Reads config + secrets from /etc/silencer-bot.env — see silencer-bot.env.example.

set -euo pipefail

CONFIG="${SILENCER_CONFIG:-/etc/silencer-bot.env}"
if [[ -f "$CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG"
fi

: "${AWS_REGION:?Set AWS_REGION in $CONFIG}"
: "${ECR_REPO:?Set ECR_REPO in $CONFIG}"
: "${DISCORD_TOKEN:?Set DISCORD_TOKEN in $CONFIG}"
: "${OLLAMA_API_KEY:?Set OLLAMA_API_KEY in $CONFIG}"

CONTAINER_NAME="${CONTAINER_NAME:-silencer-bot}"
IMAGE_TAG="${IMAGE_TAG:-slim}"
MEMORY_LIMIT="${MEMORY_LIMIT:-768m}"
CHAT_OLLAMA_MODEL="${CHAT_OLLAMA_MODEL:-gpt-oss:120b}"
MUSIC_MAX_QUEUE="${MUSIC_MAX_QUEUE:-}"

if [[ "$DISCORD_TOKEN" == "your-discord-token-here" ]] \
    || [[ "$OLLAMA_API_KEY" == "your-ollama-api-key-here" ]]; then
  echo "ERROR: Replace placeholder values in $CONFIG before starting." >&2
  exit 1
fi

if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
fi

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG}"

echo "Logging in to ECR (${ECR_REGISTRY})..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"

echo "Pulling ${IMAGE}..."
docker pull "$IMAGE"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  echo "Stopping existing container ${CONTAINER_NAME}..."
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

echo "Starting ${CONTAINER_NAME}..."
DOCKER_ENV=(
  -e BOT_PROFILE=slim
  -e CHAT_ENABLED=true
  -e CHAT_PROVIDER=ollama_cloud
  -e CHAT_OLLAMA_MODEL="$CHAT_OLLAMA_MODEL"
  -e DISCORD_TOKEN="$DISCORD_TOKEN"
  -e OLLAMA_API_KEY="$OLLAMA_API_KEY"
)
if [[ -n "$MUSIC_MAX_QUEUE" ]]; then
  DOCKER_ENV+=(-e "MUSIC_MAX_QUEUE=$MUSIC_MAX_QUEUE")
fi

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --memory "$MEMORY_LIMIT" \
  "${DOCKER_ENV[@]}" \
  "$IMAGE"

echo "Container started. Recent logs:"
docker logs --tail 20 "$CONTAINER_NAME"
