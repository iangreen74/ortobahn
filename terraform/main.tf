# Ortobahn Infrastructure — Image Generation Resources
#
# Bootstrap: create the state bucket before first `terraform init`:
#   aws s3api create-bucket --bucket ortobahn-terraform-state \
#     --region us-west-2 \
#     --create-bucket-configuration LocationConstraint=us-west-2
#   aws s3api put-bucket-versioning --bucket ortobahn-terraform-state \
#     --versioning-configuration Status=Enabled

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket = "ortobahn-terraform-state"
    key    = "image-gen/terraform.tfstate"
    region = "us-west-2"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "ortobahn"
      ManagedBy = "terraform"
    }
  }
}

# Reference the existing ECS task role (created manually)
data "aws_iam_role" "ecs_task" {
  name = "ortobahn-ecs-task"
}

data "aws_caller_identity" "current" {}
