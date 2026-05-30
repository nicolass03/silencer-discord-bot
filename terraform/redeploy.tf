locals {
  redeploy_trigger = join("|", [
    tostring(var.deploy_revision),
    var.image_tag,
    var.memory_limit,
    var.chat_ollama_model,
    var.music_max_queue,
    tostring(aws_ssm_parameter.discord_token.version),
    tostring(aws_ssm_parameter.ollama_api_key.version),
  ])
}

resource "terraform_data" "redeploy" {
  triggers_replace = [
    local.redeploy_trigger,
  ]

  depends_on = [aws_instance.bot]

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      INSTANCE_ID="${aws_instance.bot.id}"
      REGION="${var.aws_region}"

      echo "Waiting for SSM agent on ${aws_instance.bot.id}..."
      for i in $(seq 1 36); do
        STATUS=$(aws ssm describe-instance-information \
          --region "$REGION" \
          --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
          --query "InstanceInformationList[0].PingStatus" \
          --output text 2>/dev/null || echo "")
        if [ "$STATUS" = "Online" ]; then
          echo "SSM agent online."
          break
        fi
        if [ "$i" -eq 36 ]; then
          echo "WARNING: SSM agent not online after 6 minutes; skipping redeploy." >&2
          echo "Run: terraform apply -var=\"deploy_revision=${var.deploy_revision + 1}\"" >&2
          exit 0
        fi
        sleep 10
      done

      COMMAND_ID=$(aws ssm send-command \
        --region "$REGION" \
        --instance-ids "$INSTANCE_ID" \
        --document-name "AWS-RunShellScript" \
        --comment "silencer-bot redeploy" \
        --parameters 'commands=["/opt/silencer/fetch-env.sh && systemctl restart silencer-bot"]' \
        --query "Command.CommandId" \
        --output text)

      echo "SSM command started: $COMMAND_ID"
      aws ssm wait command-executed \
        --region "$REGION" \
        --command-id "$COMMAND_ID" \
        --instance-id "$INSTANCE_ID" || true
    EOT
  }
}
