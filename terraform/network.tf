data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default_public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "bot" {
  name        = "silencer-bot-sg"
  description = "Silencer bot egress only"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound (Discord, Ollama Cloud, YouTube, ECR, SSM)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "silencer-bot-sg"
  }
}
