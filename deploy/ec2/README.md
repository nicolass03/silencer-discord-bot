# AWS EC2 deployment (slim profile, $0 AWS cost)

Run the **slim** bot (voice + music + Ollama Cloud chat) on a **t3.micro**
in **us-east-1** using Docker directly on the instance. This path avoids
paid AWS services (no Secrets Manager, no Fargate, no NAT Gateway, no load
balancer).

**Secrets** live in `/etc/silencer-bot.env` on the instance (`chmod 600`) —
free, with the tradeoff that anyone with root on the box can read them.

For Fargate/ECS (paid), see [../ecs/README.md](../ecs/README.md).

## AWS cost (this guide)

| Service | Cost on free tier |
| --- | --- |
| **EC2 t3.micro** | $0 — 750 hrs/mo × 12 months (new accounts) |
| **EBS (default 8 GB root)** | $0 — within 30 GB free tier |
| **ECR** | $0 — within 500 MB storage/mo free tier |
| **IAM, VPC, security groups** | $0 |
| **Data transfer (typical bot traffic)** | $0 — usually negligible |
| **Secrets Manager** | **Not used** (~$0.40/secret/mo if you did) |
| **Ollama Cloud** | **Not AWS** — billed by Ollama when chat is used |

After the 12-month EC2 free tier, a t3.micro is ~$8–10/mo unless you stop the instance.

## What you need

| Tool | Where |
| --- | --- |
| **AWS CLI** | Your Mac — `brew install awscli` then `aws configure` (region: `us-east-1`) |
| **Docker** | Your Mac — build and push the image to ECR once |
| **Discord bot token** | [Discord Developer Portal](https://discord.com/developers/applications) |
| **Ollama API key** | [ollama.com/settings/keys](https://ollama.com/settings/keys) |

Test locally first (repo root):

```bash
docker compose -f docker-compose.slim.yml up -d --build
docker compose -f docker-compose.slim.yml logs -f
```

## Files in this directory

| File | Purpose |
| --- | --- |
| [`start-bot.sh`](start-bot.sh) | Pull ECR image, read `/etc/silencer-bot.env`, run container |
| [`silencer-bot.service`](silencer-bot.service) | systemd unit — start bot on boot |
| [`silencer-bot.env.example`](silencer-bot.env.example) | Config + secrets template |
| [`install-on-instance.sh`](install-on-instance.sh) | Copy scripts to `/opt/silencer`, enable systemd |
| [`bootstrap-user-data.sh`](bootstrap-user-data.sh) | EC2 launch user-data (Docker + AWS CLI) |

---

## Part A — One-time setup from your Mac

### 1. Variables

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPO=silencer-discord-bot
```

### 2. Build and push slim image to ECR

```bash
cd /path/to/silencer-discord-bot

aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" 2>/dev/null || true

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin \
    "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build --build-arg BOT_PROFILE=slim -t "$ECR_REPO:slim" .
docker tag "$ECR_REPO:slim" \
  "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:slim"
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:slim"
```

### 3. IAM role for the EC2 instance (ECR pull only)

No Secrets Manager — the instance only needs permission to pull from ECR.

```bash
cat > /tmp/ec2-trust.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

aws iam create-role \
  --role-name silencer-ec2-role \
  --assume-role-policy-document file:///tmp/ec2-trust.json 2>/dev/null || true

aws iam attach-role-policy \
  --role-name silencer-ec2-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly

aws iam create-instance-profile --instance-profile-name silencer-ec2-profile 2>/dev/null || true
aws iam add-role-to-instance-profile \
  --instance-profile-name silencer-ec2-profile \
  --role-name silencer-ec2-role 2>/dev/null || true
```

Wait ~10 seconds for IAM propagation.

### 4. Security group (egress only)

```bash
export VPC_ID=$(aws ec2 describe-vpcs \
  --filters Name=isDefault,Values=true \
  --query "Vpcs[0].VpcId" --output text --region "$AWS_REGION")

export SG_ID=$(aws ec2 create-security-group \
  --group-name silencer-bot-sg \
  --description "Silencer bot egress only" \
  --vpc-id "$VPC_ID" \
  --region "$AWS_REGION" \
  --query GroupId --output text 2>/dev/null \
  || aws ec2 describe-security-groups \
       --filters Name=group-name,Values=silencer-bot-sg \
       --query "SecurityGroups[0].GroupId" --output text --region "$AWS_REGION")
```

No inbound rules are required (the bot only makes outbound connections).

### 5. Launch t3.micro

Use the default VPC public subnet so the instance gets a public IP without a
NAT Gateway (NAT would add ~$32/mo).

```bash
export AMI_ID=$(aws ssm get-parameters \
  --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --region "$AWS_REGION" \
  --query "Parameters[0].Value" --output text)

export SUBNET_ID=$(aws ec2 describe-subnets \
  --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true \
  --query "Subnets[0].SubnetId" --output text --region "$AWS_REGION")

aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t3.micro \
  --iam-instance-profile Name=silencer-ec2-profile \
  --security-group-ids "$SG_ID" \
  --subnet-id "$SUBNET_ID" \
  --associate-public-ip-address \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=silencer-bot}]' \
  --user-data file://deploy/ec2/bootstrap-user-data.sh \
  --region "$AWS_REGION"
```

Get the public IP:

```bash
export INSTANCE_ID=$(aws ec2 describe-instances \
  --filters Name=tag:Name,Values=silencer-bot Name=instance-state-name,Values=running,pending \
  --query "Reservations[0].Instances[0].InstanceId" --output text --region "$AWS_REGION")

aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$AWS_REGION"

export PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].PublicIpAddress" --output text --region "$AWS_REGION")

echo "SSH: ssh ec2-user@${PUBLIC_IP}"
```

Wait ~2 minutes for user-data to install Docker.

---

## Part B — Install on the instance

SSH in:

```bash
ssh ec2-user@"$PUBLIC_IP"
```

Option **1** — clone the repo:

```bash
git clone https://github.com/nicolass03/silencer-discord-bot.git
cd silencer-discord-bot
git checkout feat/dual-config   # or your deploy branch
sudo deploy/ec2/install-on-instance.sh
```

Option **2** — copy only `deploy/ec2/` from your Mac:

```bash
scp -r deploy/ec2 ec2-user@"$PUBLIC_IP":~/silencer-ec2
ssh ec2-user@"$PUBLIC_IP" 'sudo ~/silencer-ec2/install-on-instance.sh'
```

**Set your secrets** (required before first start):

```bash
sudo nano /etc/silencer-bot.env
```

```env
DISCORD_TOKEN=your-real-discord-token
OLLAMA_API_KEY=your-real-ollama-key
```

Ensure permissions stay tight:

```bash
sudo chmod 600 /etc/silencer-bot.env
```

Start the bot:

```bash
sudo systemctl start silencer-bot
sudo systemctl status silencer-bot
sudo docker logs -f silencer-bot
```

On success you should see `Bot profile: slim` and `Logged in as ...`.

---

## Updates

**On your Mac** — rebuild and push:

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

docker build --build-arg BOT_PROFILE=slim -t silencer-discord-bot:slim .
docker tag silencer-discord-bot:slim \
  "$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/silencer-discord-bot:slim"
docker push "$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/silencer-discord-bot:slim"
```

**On the instance**:

```bash
sudo systemctl restart silencer-bot
```

---

## systemd commands

```bash
sudo systemctl start silencer-bot    # pull + start
sudo systemctl stop silencer-bot     # stop container
sudo systemctl restart silencer-bot  # pull + recreate (deploy)
sudo systemctl status silencer-bot
sudo journalctl -u silencer-bot -f
sudo docker logs -f silencer-bot
```

---

## Sizing

| Setting | Default | Notes |
| --- | --- | --- |
| **Container memory** | `768m` | Lower to `640m` if the instance OOMs |
| **CPU** | t3.micro burstable | Heavy `/play` use can exhaust CPU credits |

---

## Troubleshooting

**`Cannot pull container` / ECR 403** — instance profile missing
`AmazonEC2ContainerRegistryReadOnly`, or wrong `AWS_REGION` in
`/etc/silencer-bot.env`.

**`Set DISCORD_TOKEN in /etc/silencer-bot.env`** — edit the file and replace
placeholders.

**Container exits immediately** — `sudo docker logs silencer-bot`. Common causes:
wrong token, slim profile misconfig, or OOM (reduce `MEMORY_LIMIT`).

**Bot offline after reboot** — `install-on-instance.sh` runs
`systemctl enable silencer-bot`.

---

## Optional: free SSM Parameter Store (instead of env file)

If you prefer not to store tokens in a flat file, **SSM Standard SecureString
parameters are free** (unlike Secrets Manager). That requires extra IAM and
script changes; the env-file path above is the simplest $0 setup.

---

## Environment reference

All values in `/etc/silencer-bot.env`:

| Variable | Required |
| --- | --- |
| `AWS_REGION` | yes |
| `ECR_REPO` | yes |
| `DISCORD_TOKEN` | yes |
| `OLLAMA_API_KEY` | yes |
| `IMAGE_TAG` | default `slim` |
| `MEMORY_LIMIT` | default `768m` |
| `CHAT_OLLAMA_MODEL` | default `gpt-oss:120b` |

Whisper, llama, and moderation settings are not used in the slim profile.
