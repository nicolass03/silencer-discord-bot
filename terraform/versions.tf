terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment and configure for remote state:
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "silencer-discord-bot/terraform.tfstate"
  #   region = "us-east-1"
  # }
}
