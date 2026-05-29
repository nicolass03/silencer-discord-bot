# AWS ECS deployment (slim profile)

Deploy the **slim** bot image (voice + music + Ollama Cloud chat, no local
STT/LLM) on Amazon ECS. This guide targets **Fargate**; a note at the end
covers the **EC2 free-tier** path.

## Prerequisites

- AWS account with CLI configured (`aws configure`)
- Docker installed locally
- Discord bot token and [Ollama Cloud API key](https://ollama.com/settings/keys)
- Replace placeholders in `task-definition.slim.json`:
  - `ACCOUNT_ID` — your AWS account ID
  - `REGION` — e.g. `us-east-1`
  - Secret ARNs (see step 3)

## 1. Build and push the slim image to ECR

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPO=silencer-discord-bot

aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" 2>/dev/null || true

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin \
    "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build --build-arg BOT_PROFILE=slim -t "$ECR_REPO:slim" .
docker tag "$ECR_REPO:slim" \
  "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:slim"
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:slim"
```

## 2. Store secrets

Create Secrets Manager entries (or SSM Parameter Store SecureString) for:

| Secret | Example name |
| --- | --- |
| Discord bot token | `silencer/discord-token` |
| Ollama API key | `silencer/ollama-api-key` |

```bash
aws secretsmanager create-secret \
  --name silencer/discord-token \
  --secret-string "YOUR_DISCORD_TOKEN" \
  --region "$AWS_REGION"

aws secretsmanager create-secret \
  --name silencer/ollama-api-key \
  --secret-string "YOUR_OLLAMA_API_KEY" \
  --region "$AWS_REGION"
```

Update the `valueFrom` ARNs in
[`task-definition.slim.json`](task-definition.slim.json) to match your secret
ARNs (include the random suffix Secrets Manager appends).

## 3. IAM roles

**Execution role** (`ecsTaskExecutionRole`): attach the AWS managed policy
`AmazonECSTaskExecutionRolePolicy` so ECS can pull from ECR, write logs, and
read secrets.

**Task role** (`silencerBotTaskRole`): optional for this bot (no AWS API calls
from the container). You can use a minimal empty role or omit `taskRoleArn` if
your organization allows it.

Grant the execution role `secretsmanager:GetSecretValue` on your two secrets.

## 4. CloudWatch log group

```bash
aws logs create-log-group \
  --log-group-name /ecs/silencer-discord-bot-slim \
  --region "$AWS_REGION"
```

## 5. Register the task definition

Edit `task-definition.slim.json` with your account ID, region, image URI, and
secret ARNs, then:

```bash
aws ecs register-task-definition \
  --cli-input-json file://deploy/ecs/task-definition.slim.json \
  --region "$AWS_REGION"
```

## 6. Create the ECS service

Recommended networking for a Discord bot (outbound-only, no load balancer):

1. Create a VPC with **public subnets** (or use the default VPC).
2. Create an ECS cluster: `aws ecs create-cluster --cluster-name silencer`
3. Create a security group allowing **egress** to `0.0.0.0/0` (HTTPS for
   Discord, Ollama Cloud, YouTube). No inbound rules required.
4. Create a Fargate service with:
   - Launch type: **FARGATE**
   - Task definition: `silencer-discord-bot-slim`
   - Desired count: **1**
   - **Assign public IP: ENABLED** (avoids NAT Gateway cost)
   - Subnets: public subnets from step 1

Example (adjust subnet and security group IDs):

```bash
aws ecs create-service \
  --cluster silencer \
  --service-name silencer-bot-slim \
  --task-definition silencer-discord-bot-slim \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}" \
  --region "$AWS_REGION"
```

View logs:

```bash
aws logs tail /ecs/silencer-discord-bot-slim --follow --region "$AWS_REGION"
```

## Task sizing

The task definition uses **0.5 vCPU (512)** and **1024 MB** memory — a
comfortable default for voice + music + Ollama chat.

For tighter budgets, try **768 MB** on Fargate (minimum for 0.5 vCPU is 1024 on
some platforms — check current Fargate valid CPU/memory pairs in your region).

## EC2 free tier (optional)

Instead of Fargate:

1. Launch a **t3.micro** (1 vCPU, 1 GB) in the free tier.
2. Install the ECS agent and register the instance to a cluster.
3. Use the same slim image with a task memory reservation around **768 MB**.
4. Account for the ECS agent using ~100–200 MB of host RAM.

Fargate has no free tier; EC2 free tier lasts 12 months for new accounts.

## Environment reference

| Variable | Value (slim) |
| --- | --- |
| `BOT_PROFILE` | `slim` |
| `CHAT_ENABLED` | `true` |
| `CHAT_PROVIDER` | `ollama_cloud` |
| `OLLAMA_API_KEY` | from Secrets Manager |
| `DISCORD_TOKEN` | from Secrets Manager |

Whisper, llama, and moderation env vars are not used in the slim profile.

## Updating the bot

Rebuild and push the image, then force a new deployment:

```bash
docker build --build-arg BOT_PROFILE=slim -t "$ECR_REPO:slim" .
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:slim"

aws ecs update-service \
  --cluster silencer \
  --service silencer-bot-slim \
  --force-new-deployment \
  --region "$AWS_REGION"
```

To edit personality text without rebuilding, bake `prompts/personality.txt` into
the image at build time or mount from EFS (optional, not included in the task
definition template).
