output "aws_region" {
  description = "AWS region in use."
  value       = var.aws_region
}

output "aws_account_id" {
  description = "AWS account ID."
  value       = data.aws_caller_identity.current.account_id
}

output "ecr_repository_url" {
  description = "ECR repository URL for docker push (without tag)."
  value       = aws_ecr_repository.bot.repository_url
}

output "ecr_repository_name" {
  description = "ECR repository name."
  value       = aws_ecr_repository.bot.name
}

output "instance_id" {
  description = "EC2 instance ID."
  value       = aws_instance.bot.id
}

output "instance_public_ip" {
  description = "Public IP of the bot EC2 instance."
  value       = aws_instance.bot.public_ip
}

output "ssh_command" {
  description = "SSH command for debugging (Amazon Linux ec2-user)."
  value       = "ssh ec2-user@${aws_instance.bot.public_ip}"
}

output "log_command" {
  description = "Command to view container logs over SSH."
  value       = "ssh ec2-user@${aws_instance.bot.public_ip} 'sudo docker logs -f silencer-bot'"
}

output "push_image_command" {
  description = "Helper script to build and push the slim image."
  value       = "./terraform/scripts/push-image.sh"
}

output "redeploy_hint" {
  description = "Bump deploy_revision after pushing a new image to restart the bot."
  value       = "terraform apply -var=\"deploy_revision=<N>\""
}
