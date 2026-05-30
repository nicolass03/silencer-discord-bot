#!/bin/bash
# EC2 user-data snippet for Amazon Linux 2023 (us-east-1).
# Paste into "Advanced details -> User data" when launching a t3.micro,
# or pass with: --user-data file://deploy/ec2/bootstrap-user-data.sh
#
# After the instance boots, SSH in and run install-on-instance.sh from a
# clone of this repo (or scp deploy/ec2/* to the instance).

set -euxo pipefail

dnf update -y
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user
dnf install -y aws-cli
