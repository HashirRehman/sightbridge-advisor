locals {
  name = "${var.project}-${var.environment}"
  ssm  = "/${var.project}/${var.environment}"
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# The default VPC hosts RDS. Lambda deliberately stays outside any VPC: inside one it would
# need a NAT gateway (~$32/mo) or interface endpoints (~$7/mo each) to reach Bedrock/Cognito.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}
