#!/usr/bin/env bash
# One time: start Mellow's server on EC2, then run deploy/deploy.sh. Needs the AWS CLI logged in (`aws configure`).
# A t3.xlarge (4 CPUs, 16 GB: a Lean check holds ~5 GB of Mathlib, up to 4 at once) costs about $4 a day while running.
# Writes deploy/.instance (git-ignored): the instance, its fixed IP, its HTTPS host name and the testers' access code.
set -euo pipefail
cd "$(dirname "$0")"
[ -e .instance ] && { echo "deploy/.instance exists: already launched"; exit 1; }
export AWS_DEFAULT_REGION=${AWS_REGION:-$(aws configure get region || echo us-east-1)}
KEY=~/.ssh/mellow-ec2.pem

AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
if [ ! -e $KEY ]; then
  mkdir -p ~/.ssh
  aws ec2 create-key-pair --key-name mellow --query KeyMaterial --output text > $KEY
  chmod 600 $KEY
fi
SG=$(aws ec2 create-security-group --group-name mellow --description "Mellow: web and ssh" --query GroupId --output text)
for port in 80 443; do
  aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port $port --cidr 0.0.0.0/0 >/dev/null
done
# ssh only from this machine; deploy.sh re-adds it when this machine's IP changes.
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 \
  --cidr "$(curl -s https://checkip.amazonaws.com)/32" >/dev/null

ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "${MELLOW_INSTANCE_TYPE:-t3.xlarge}" --key-name mellow \
  --security-group-ids "$SG" --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=40,VolumeType=gp3}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=mellow}]' --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids "$ID"
# A fixed IP, so the host name (and the link testers saved) survives a stop and start.
ALLOC=$(aws ec2 allocate-address --query AllocationId --output text)
aws ec2 associate-address --instance-id "$ID" --allocation-id "$ALLOC" >/dev/null
IP=$(aws ec2 describe-addresses --allocation-ids "$ALLOC" --query 'Addresses[0].PublicIp' --output text)

cat > .instance <<EOF
REGION=$AWS_DEFAULT_REGION
INSTANCE=$ID
SG=$SG
ALLOC=$ALLOC
IP=$IP
HOST=${IP//./-}.sslip.io
ACCESS_CODE=$(openssl rand -hex 8)
EOF
echo "Launched $ID at $IP. Next: deploy/deploy.sh"
