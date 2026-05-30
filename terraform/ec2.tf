data "aws_ssm_parameter" "amazon_linux_2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  start_bot_sh         = file("${path.module}/../deploy/ec2/start-bot.sh")
  silencer_bot_service = file("${path.module}/../deploy/ec2/silencer-bot.service")

  fetch_env_sh = templatefile("${path.module}/templates/fetch-env.sh.tftpl", {
    aws_region     = var.aws_region
    ssm_prefix     = var.ssm_parameter_prefix
  })

  user_data = base64encode(templatefile("${path.module}/templates/user_data.sh.tftpl", {
    fetch_env_sh_b64         = base64encode(local.fetch_env_sh)
    start_bot_sh_b64         = base64encode(local.start_bot_sh)
    silencer_bot_service_b64 = base64encode(local.silencer_bot_service)
  }))
}

resource "aws_instance" "bot" {
  ami                         = data.aws_ssm_parameter.amazon_linux_2023.value
  instance_type               = var.instance_type
  subnet_id                   = tolist(data.aws_subnets.default_public.ids)[0]
  vpc_security_group_ids      = [aws_security_group.bot.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2.name
  associate_public_ip_address = true
  user_data_replace_on_change = true
  user_data                   = local.user_data

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  tags = {
    Name = var.instance_name
  }

  depends_on = [
    aws_ssm_parameter.discord_token,
    aws_ssm_parameter.ollama_api_key,
    aws_ssm_parameter.aws_region,
    aws_ssm_parameter.ecr_repo,
    aws_ssm_parameter.image_tag,
    aws_ssm_parameter.memory_limit,
    aws_ssm_parameter.chat_ollama_model,
    aws_ssm_parameter.music_max_queue,
  ]
}
