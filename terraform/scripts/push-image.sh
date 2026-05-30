#!/usr/bin/env bash
# Build the slim bot image and push it to the ECR repository created by Terraform.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TF_DIR}/.." && pwd)"

cd "${TF_DIR}"

if ! terraform output ecr_repository_url >/dev/null 2>&1; then
  echo "ERROR: Run 'terraform apply' in ${TF_DIR} first." >&2
  exit 1
fi

ECR_URL="$(terraform output -raw ecr_repository_url)"
AWS_REGION="$(terraform output -raw aws_region)"
IMAGE_TAG="${IMAGE_TAG:-slim}"

echo "Logging in to ECR (${ECR_URL})..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_URL%%/*}"

echo "Building slim image..."
cd "${REPO_ROOT}"
docker build --build-arg BOT_PROFILE=slim -t "${ECR_URL}:${IMAGE_TAG}" .

echo "Pushing ${ECR_URL}:${IMAGE_TAG}..."
docker push "${ECR_URL}:${IMAGE_TAG}"

echo "Done. Restart the bot with:"
echo "  cd ${TF_DIR} && terraform apply -var=\"deploy_revision=1\""
echo "(increment deploy_revision each time you push a new image)"
