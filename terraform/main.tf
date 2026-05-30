provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "silencer-discord-bot"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}

data "aws_caller_identity" "current" {}
