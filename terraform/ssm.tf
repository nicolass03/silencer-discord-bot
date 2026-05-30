resource "aws_ssm_parameter" "discord_token" {
  name  = "${var.ssm_parameter_prefix}/discord_token"
  type  = "SecureString"
  value = var.discord_token
}

resource "aws_ssm_parameter" "ollama_api_key" {
  name  = "${var.ssm_parameter_prefix}/ollama_api_key"
  type  = "SecureString"
  value = var.ollama_api_key
}

resource "aws_ssm_parameter" "aws_region" {
  name  = "${var.ssm_parameter_prefix}/aws_region"
  type  = "String"
  value = var.aws_region
}

resource "aws_ssm_parameter" "ecr_repo" {
  name  = "${var.ssm_parameter_prefix}/ecr_repo"
  type  = "String"
  value = var.ecr_repo_name
}

resource "aws_ssm_parameter" "image_tag" {
  name  = "${var.ssm_parameter_prefix}/image_tag"
  type  = "String"
  value = var.image_tag
}

resource "aws_ssm_parameter" "memory_limit" {
  name  = "${var.ssm_parameter_prefix}/memory_limit"
  type  = "String"
  value = var.memory_limit
}

resource "aws_ssm_parameter" "chat_ollama_model" {
  name  = "${var.ssm_parameter_prefix}/chat_ollama_model"
  type  = "String"
  value = var.chat_ollama_model
}

resource "aws_ssm_parameter" "music_max_queue" {
  name  = "${var.ssm_parameter_prefix}/music_max_queue"
  type  = "String"
  value = var.music_max_queue
}
