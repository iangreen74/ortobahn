terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket         = "ortobahn-terraform-state"
    key            = "production/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "ortobahn-terraform-locks"
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  default = "us-east-1"
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
}

variable "deployment_color" {
  description = "Blue or green deployment"
  type        = string
  default     = "blue"
}

resource "aws_ecs_cluster" "ortobahn" {
  name = "ortobahn-cluster"
}

resource "aws_lb" "ortobahn" {
  name               = "ortobahn-alb"
  internal           = false
  load_balancer_type = "application"
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]
}

resource "aws_lb_target_group" "blue" {
  name        = "ortobahn-blue"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }
}

resource "aws_lb_target_group" "green" {
  name        = "ortobahn-green"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }
}

resource "aws_lb_listener" "ortobahn" {
  load_balancer_arn = aws_lb.ortobahn.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS-1-2-2017-01"
  certificate_arn   = aws_acm_certificate.ortobahn.arn

  default_action {
    type = "forward"
    forward {
      target_group {
        arn    = aws_lb_target_group.blue.arn
        weight = var.deployment_color == "blue" ? 100 : 0
      }
      target_group {
        arn    = aws_lb_target_group.green.arn
        weight = var.deployment_color == "green" ? 100 : 0
      }
    }
  }
}

resource "aws_ecs_task_definition" "ortobahn" {
  family                   = "ortobahn"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name  = "ortobahn"
    image = "${data.aws_ecr_repository.ortobahn.repository_url}:${var.image_tag}"
    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]
    environment = [
      { name = "DEPLOYMENT_COLOR", value = var.deployment_color }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ortobahn.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "ortobahn" {
  name            = "ortobahn-service"
  cluster         = aws_ecs_cluster.ortobahn.id
  task_definition = aws_ecs_task_definition.ortobahn.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.deployment_color == "blue" ? aws_lb_target_group.blue.arn : aws_lb_target_group.green.arn
    container_name   = "ortobahn"
    container_port   = 8000
  }
}
