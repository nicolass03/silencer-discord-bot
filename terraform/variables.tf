variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment label applied as a default tag."
  type        = string
  default     = "production"
}

variable "instance_type" {
  description = "EC2 instance type for the bot host."
  type        = string
  default     = "t3.micro"
}

variable "instance_name" {
  description = "Name tag for the EC2 instance."
  type        = string
  default     = "silencer-bot"
}

variable "ecr_repo_name" {
  description = "ECR repository name for the bot image."
  type        = string
  default     = "silencer-discord-bot"
}

variable "ecr_image_count" {
  description = "Number of images to retain in ECR (older images are expired)."
  type        = number
  default     = 10
}

variable "ecr_force_delete" {
  description = "Delete ECR repository even when it contains images (used by terraform destroy)."
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "Docker image tag to deploy."
  type        = string
  default     = "slim"
}

variable "memory_limit" {
  description = "Docker memory limit passed to the container (e.g. 768m)."
  type        = string
  default     = "768m"
}

variable "chat_ollama_model" {
  description = "Ollama Cloud model name for @mention chat."
  type        = string
  default     = "gpt-oss:120b"
}

variable "music_max_queue" {
  description = "Maximum number of tracks in the music queue."
  type        = string
  default     = "10"
}

variable "discord_token" {
  description = "Discord bot token (stored in SSM SecureString)."
  type        = string
  sensitive   = true
}

variable "ollama_api_key" {
  description = "Ollama Cloud API key (stored in SSM SecureString)."
  type        = string
  sensitive   = true
}

variable "deploy_revision" {
  description = "Increment to force a container restart without changing other settings."
  type        = number
  default     = 0
}

variable "ssm_parameter_prefix" {
  description = "Prefix for SSM Parameter Store paths."
  type        = string
  default     = "/silencer"
}
