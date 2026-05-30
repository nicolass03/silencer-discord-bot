# Terraform: AWS EC2 deployment (slim profile)

Deploy the **slim** Silencer Discord bot on a **t3.micro** EC2 instance using Docker,
ECR, and SSM Parameter Store. This replaces the manual CLI steps in
[`../deploy/ec2/README.md`](../deploy/ec2/README.md) with repeatable infrastructure.

**Cost:** Same as the manual EC2 guide — $0 on AWS free tier for the first 12 months
(EC2 + EBS + ECR within free limits). SSM Standard parameters are free.

## What Terraform creates

| Resource | Purpose |
| --- | --- |
| ECR repository | Stores the slim Docker image |
| SSM parameters | Secrets + bot config (`/silencer/*`) |
| IAM role + instance profile | ECR pull, SSM read, SSM Run Command |
| Security group | Egress-only (no inbound rules) |
| EC2 t3.micro | Runs Docker via systemd (`silencer-bot.service`) |

On first boot, user-data installs Docker, writes scripts to `/opt/silencer/`, fetches
config from SSM into `/etc/silencer-bot.env`, and starts the bot.

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured (`aws configure`)
- [Docker](https://docs.docker.com/get-docker/) (local, for building images)
- Discord bot token and [Ollama Cloud API key](https://ollama.com/settings/keys)

## First-time deploy

### 1. Configure variables

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set `discord_token` and `ollama_api_key`.

### 2. Provision infrastructure

```bash
terraform init
terraform apply
```

This creates ECR, SSM parameters, and the EC2 instance. User-data bootstraps the
instance and attempts to start the bot (it will fail until an image exists in ECR).

### 3. Build and push the Docker image

From the repo root:

```bash
chmod +x terraform/scripts/push-image.sh
./terraform/scripts/push-image.sh
```

Optional: set a custom tag with `IMAGE_TAG=slim-v2 ./terraform/scripts/push-image.sh`
and matching `image_tag` in `terraform.tfvars`.

### 4. Start / restart the bot

If the instance booted before the image was pushed, trigger a redeploy:

```bash
cd terraform
terraform apply -var="deploy_revision=1"
```

Terraform waits for the SSM agent, re-fetches config from SSM, and runs
`systemctl restart silencer-bot` on the instance.

Verify:

```bash
terraform output ssh_command
terraform output log_command
```

## Update the app (new code)

```bash
# 1. Rebuild and push
./terraform/scripts/push-image.sh

# 2. Restart the container on EC2 (no SSH required)
cd terraform
terraform apply -var="deploy_revision=2"   # increment each deploy
```

Alternatively, bump `deploy_revision` in `terraform.tfvars` and run `terraform apply`.

## Update environment variables

Edit `terraform.tfvars` (e.g. change `chat_ollama_model`, `music_max_queue`, or
secrets), then:

```bash
terraform apply
```

Terraform updates SSM parameters and automatically redeploys when any tracked value
changes (including secret rotations via `discord_token` / `ollama_api_key`).

Tracked redeploy triggers:

- `deploy_revision`
- `image_tag`, `memory_limit`, `chat_ollama_model`, `music_max_queue`
- SSM parameter versions for `discord_token` and `ollama_api_key`

## Useful outputs

```bash
terraform output ecr_repository_url
terraform output instance_public_ip
terraform output ssh_command
terraform output log_command
```

## Debugging over SSH

```bash
$(terraform output -raw ssh_command)

# On the instance:
sudo systemctl status silencer-bot
sudo docker logs -f silencer-bot
sudo cat /etc/silencer-bot.env   # contains secrets — handle carefully
```

## Destroy

```bash
terraform destroy
```

Removes the EC2 instance, security group, IAM resources, SSM parameters, and ECR
repository (images are deleted when `ecr_force_delete = true`, the default).

## Variables reference

See [`variables.tf`](variables.tf) and [`terraform.tfvars.example`](terraform.tfvars.example).

| Variable | Default | Description |
| --- | --- | --- |
| `discord_token` | (required) | Discord bot token |
| `ollama_api_key` | (required) | Ollama Cloud API key |
| `aws_region` | `us-east-1` | AWS region |
| `instance_type` | `t3.micro` | EC2 instance type |
| `image_tag` | `slim` | Docker tag to deploy |
| `memory_limit` | `768m` | Container memory limit |
| `deploy_revision` | `0` | Bump to force restart |

## Troubleshooting

**SSM redeploy skipped / timed out** — wait ~2 minutes after first boot for the SSM
agent to register, then run `terraform apply -var="deploy_revision=N"` again.

**Container exits immediately** — check logs via `terraform output log_command`.
Common causes: wrong token, missing ECR image, or OOM (lower `memory_limit` or use a
larger instance).

**ECR pull 403** — instance profile must have `AmazonEC2ContainerRegistryReadOnly`;
re-run `terraform apply` if IAM was changed manually.

**User-data changed** — `user_data_replace_on_change = true` recreates the instance
when bootstrap scripts change. Plan carefully before editing templates.

## Manual EC2 guide

For step-by-step CLI instructions without Terraform, see
[`../deploy/ec2/README.md`](../deploy/ec2/README.md).
