terraform {
  required_version = ">= 1.0"
  
  backend "s3" {
    bucket = "lighthouse-terraform-state"
    key    = "lighthouse/terraform.tfstate"
    region = "us-east-1"
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "environment" {
  description = "Environment (staging or production)"
  type        = string
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

locals {
  app_name = "lighthouse"
  common_tags = {
    Project     = local.app_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_ecs_cluster" "main" {
  name = "${local.app_name}-${var.environment}"
  tags = local.common_tags
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${local.app_name}-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.environment == "production" ? 1024 : 512
  memory                   = var.environment == "production" ? 2048 : 1024
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = local.app_name
    image     = "${data.aws_ssm_parameter.docker_registry.value}/${data.aws_ssm_parameter.docker_image.value}:${var.image_tag}"
    essential = true

    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "ENVIRONMENT", value = var.environment },
      { name = "LOG_LEVEL", value = var.environment == "production" ? "INFO" : "DEBUG" }
    ]

    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
      { name = "JWT_SECRET", valueFrom = aws_ssm_parameter.jwt_secret.arn }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
  }])

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.app_name}-${var.environment}"
  retention_in_days = var.environment == "production" ? 30 : 7
  tags              = local.common_tags
}

data "aws_ssm_parameter" "docker_registry" {
  name = "/lighthouse/docker/registry"
}

data "aws_ssm_parameter" "docker_image" {
  name = "/lighthouse/docker/image"
}

resource "aws_ssm_parameter" "database_url" {
  name  = "/lighthouse/${var.environment}/database_url"
  type  = "SecureString"
  value = "placeholder"
  lifecycle {
    ignore_changes = [value]
  }
  tags = local.common_tags
}

resource "aws_ssm_parameter" "jwt_secret" {
  name  = "/lighthouse/${var.environment}/jwt_secret"
  type  = "SecureString"
  value = "placeholder"
  lifecycle {
    ignore_changes = [value]
  }
  tags = local.common_tags
}

resource "aws_iam_role" "ecs_execution" {
  name = "${local.app_name}-${var.environment}-ecs-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role" "ecs_task" {
  name = "${local.app_name}-${var.environment}-ecs-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
  tags = local.common_tags
}

output "app_url" {
  value = "https://${var.environment == "production" ? "app" : "staging"}.lighthouse.example.com"
}
