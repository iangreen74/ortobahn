# --- Production Web ---
resource "aws_ecs_task_definition" "web" {
  family                   = "ortobahn-web"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "ortobahn-web"
      image     = "${var.ecr_repository_url}:latest"
      essential = true
      command   = ["python", "-m", "ortobahn", "web", "--host", "0.0.0.0", "--port", "8000"]

      portMappings = [{ containerPort = 8000, protocol = "tcp" }]

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      environment = [
        { name = "WEB_HOST", value = "0.0.0.0" },
        { name = "WEB_PORT", value = "8000" },
        { name = "AUTONOMOUS_MODE", value = "true" },
        { name = "COGNITO_REGION", value = var.aws_region },
        { name = "PIPELINE_INTERVAL_HOURS", value = "8" },
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "USE_BEDROCK", value = "true" },
        { name = "BEDROCK_REGION", value = var.aws_region },
        { name = "DEPLOY_SHA", value = "unknown" },
        { name = "IMAGE_GENERATION_ENABLED", value = "true" },
        { name = "IMAGE_S3_BUCKET", value = "ortobahn-images" },
        { name = "BEDROCK_IMAGE_MODEL", value = "amazon.titan-image-generator-v2:0" },
      ]

      secrets = [
        { name = "ANTHROPIC_API_KEY", valueFrom = "${var.prod_secret_arn}:ANTHROPIC_API_KEY::" },
        { name = "DATABASE_URL", valueFrom = "${var.prod_secret_arn}:DATABASE_URL::" },
        { name = "ORTOBAHN_SECRET_KEY", valueFrom = "${var.prod_secret_arn}:ORTOBAHN_SECRET_KEY::" },
        { name = "BLUESKY_HANDLE", valueFrom = "${var.prod_secret_arn}:BLUESKY_HANDLE::" },
        { name = "BLUESKY_APP_PASSWORD", valueFrom = "${var.prod_secret_arn}:BLUESKY_APP_PASSWORD::" },
        { name = "STRIPE_SECRET_KEY", valueFrom = "${var.prod_secret_arn}:STRIPE_SECRET_KEY::" },
        { name = "STRIPE_PUBLISHABLE_KEY", valueFrom = "${var.prod_secret_arn}:STRIPE_PUBLISHABLE_KEY::" },
        { name = "STRIPE_WEBHOOK_SECRET", valueFrom = "${var.prod_secret_arn}:STRIPE_WEBHOOK_SECRET::" },
        { name = "STRIPE_PRICE_ID", valueFrom = "${var.prod_secret_arn}:STRIPE_PRICE_ID::" },
        { name = "COGNITO_USER_POOL_ID", valueFrom = "${var.prod_secret_arn}:COGNITO_USER_POOL_ID::" },
        { name = "COGNITO_CLIENT_ID", valueFrom = "${var.prod_secret_arn}:COGNITO_CLIENT_ID::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/ortobahn"
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "web"
        }
      }
    }
  ])

  lifecycle {
    ignore_changes = [container_definitions]
  }
}

# --- Production Scheduler ---
resource "aws_ecs_task_definition" "scheduler" {
  family                   = "ortobahn-scheduler"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.scheduler_cpu
  memory                   = var.scheduler_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "ortobahn-scheduler"
      image     = "${var.ecr_repository_url}:latest"
      essential = true
      command   = ["python", "-m", "ortobahn", "schedule", "--platforms", "bluesky"]

      environment = [
        { name = "AUTONOMOUS_MODE", value = "true" },
        { name = "BACKUP_ENABLED", value = "true" },
        { name = "COGNITO_REGION", value = var.aws_region },
        { name = "PIPELINE_INTERVAL_HOURS", value = "8" },
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "USE_BEDROCK", value = "true" },
        { name = "BEDROCK_REGION", value = var.aws_region },
        { name = "DEPLOY_SHA", value = "unknown" },
        { name = "IMAGE_GENERATION_ENABLED", value = "true" },
        { name = "IMAGE_S3_BUCKET", value = "ortobahn-images" },
        { name = "BEDROCK_IMAGE_MODEL", value = "amazon.titan-image-generator-v2:0" },
      ]

      secrets = [
        { name = "ANTHROPIC_API_KEY", valueFrom = "${var.prod_secret_arn}:ANTHROPIC_API_KEY::" },
        { name = "DATABASE_URL", valueFrom = "${var.prod_secret_arn}:DATABASE_URL::" },
        { name = "ORTOBAHN_SECRET_KEY", valueFrom = "${var.prod_secret_arn}:ORTOBAHN_SECRET_KEY::" },
        { name = "BLUESKY_HANDLE", valueFrom = "${var.prod_secret_arn}:BLUESKY_HANDLE::" },
        { name = "BLUESKY_APP_PASSWORD", valueFrom = "${var.prod_secret_arn}:BLUESKY_APP_PASSWORD::" },
        { name = "STRIPE_SECRET_KEY", valueFrom = "${var.prod_secret_arn}:STRIPE_SECRET_KEY::" },
        { name = "STRIPE_PUBLISHABLE_KEY", valueFrom = "${var.prod_secret_arn}:STRIPE_PUBLISHABLE_KEY::" },
        { name = "STRIPE_WEBHOOK_SECRET", valueFrom = "${var.prod_secret_arn}:STRIPE_WEBHOOK_SECRET::" },
        { name = "STRIPE_PRICE_ID", valueFrom = "${var.prod_secret_arn}:STRIPE_PRICE_ID::" },
        { name = "COGNITO_USER_POOL_ID", valueFrom = "${var.prod_secret_arn}:COGNITO_USER_POOL_ID::" },
        { name = "COGNITO_CLIENT_ID", valueFrom = "${var.prod_secret_arn}:COGNITO_CLIENT_ID::" },
        { name = "GH_TOKEN", valueFrom = "${var.prod_secret_arn}:GH_TOKEN::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/ortobahn"
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "scheduler"
        }
      }

      stopTimeout = 30
    }
  ])

  lifecycle {
    ignore_changes = [container_definitions]
  }
}

# --- Staging Web ---
resource "aws_ecs_task_definition" "web_staging" {
  family                   = "ortobahn-web-staging"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "ortobahn-web"
      image     = "${var.ecr_repository_url}:latest"
      essential = true
      command   = ["python", "-m", "ortobahn", "web", "--host", "0.0.0.0", "--port", "8000"]

      portMappings = [{ containerPort = 8000, protocol = "tcp" }]

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      environment = [
        { name = "WEB_HOST", value = "0.0.0.0" },
        { name = "WEB_PORT", value = "8000" },
        { name = "AUTONOMOUS_MODE", value = "true" },
        { name = "COGNITO_REGION", value = var.aws_region },
        { name = "PIPELINE_INTERVAL_HOURS", value = "8" },
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "USE_BEDROCK", value = "true" },
        { name = "BEDROCK_REGION", value = var.aws_region },
        { name = "ENVIRONMENT", value = "staging" },
        { name = "DEPLOY_SHA", value = "unknown" },
        { name = "IMAGE_GENERATION_ENABLED", value = "false" },
        { name = "IMAGE_S3_BUCKET", value = "ortobahn-images" },
        { name = "BEDROCK_IMAGE_MODEL", value = "amazon.titan-image-generator-v2:0" },
      ]

      secrets = [
        { name = "ANTHROPIC_API_KEY", valueFrom = "${var.staging_secret_arn}:ANTHROPIC_API_KEY::" },
        { name = "DATABASE_URL", valueFrom = "${var.staging_secret_arn}:DATABASE_URL::" },
        { name = "ORTOBAHN_SECRET_KEY", valueFrom = "${var.staging_secret_arn}:ORTOBAHN_SECRET_KEY::" },
        { name = "BLUESKY_HANDLE", valueFrom = "${var.staging_secret_arn}:BLUESKY_HANDLE::" },
        { name = "BLUESKY_APP_PASSWORD", valueFrom = "${var.staging_secret_arn}:BLUESKY_APP_PASSWORD::" },
        { name = "STRIPE_SECRET_KEY", valueFrom = "${var.staging_secret_arn}:STRIPE_SECRET_KEY::" },
        { name = "STRIPE_PUBLISHABLE_KEY", valueFrom = "${var.staging_secret_arn}:STRIPE_PUBLISHABLE_KEY::" },
        { name = "STRIPE_WEBHOOK_SECRET", valueFrom = "${var.staging_secret_arn}:STRIPE_WEBHOOK_SECRET::" },
        { name = "STRIPE_PRICE_ID", valueFrom = "${var.staging_secret_arn}:STRIPE_PRICE_ID::" },
        { name = "COGNITO_USER_POOL_ID", valueFrom = "${var.staging_secret_arn}:COGNITO_USER_POOL_ID::" },
        { name = "COGNITO_CLIENT_ID", valueFrom = "${var.staging_secret_arn}:COGNITO_CLIENT_ID::" },
        { name = "GH_TOKEN", valueFrom = "${var.staging_secret_arn}:GH_TOKEN::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/ortobahn"
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "staging-web"
        }
      }
    }
  ])

  lifecycle {
    ignore_changes = [container_definitions]
  }
}

# --- Staging Scheduler ---
resource "aws_ecs_task_definition" "scheduler_staging" {
  family                   = "ortobahn-scheduler-staging"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.scheduler_cpu
  memory                   = var.scheduler_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "ortobahn-scheduler"
      image     = "${var.ecr_repository_url}:latest"
      essential = true
      command   = ["python", "-m", "ortobahn", "schedule", "--platforms", "bluesky"]

      environment = [
        { name = "AUTONOMOUS_MODE", value = "true" },
        { name = "BACKUP_ENABLED", value = "false" },
        { name = "COGNITO_REGION", value = var.aws_region },
        { name = "PIPELINE_INTERVAL_HOURS", value = "8" },
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "USE_BEDROCK", value = "true" },
        { name = "BEDROCK_REGION", value = var.aws_region },
        { name = "ENVIRONMENT", value = "staging" },
        { name = "DEPLOY_SHA", value = "unknown" },
        { name = "IMAGE_GENERATION_ENABLED", value = "false" },
        { name = "IMAGE_S3_BUCKET", value = "ortobahn-images" },
        { name = "BEDROCK_IMAGE_MODEL", value = "amazon.titan-image-generator-v2:0" },
      ]

      secrets = [
        { name = "ANTHROPIC_API_KEY", valueFrom = "${var.staging_secret_arn}:ANTHROPIC_API_KEY::" },
        { name = "DATABASE_URL", valueFrom = "${var.staging_secret_arn}:DATABASE_URL::" },
        { name = "ORTOBAHN_SECRET_KEY", valueFrom = "${var.staging_secret_arn}:ORTOBAHN_SECRET_KEY::" },
        { name = "BLUESKY_HANDLE", valueFrom = "${var.staging_secret_arn}:BLUESKY_HANDLE::" },
        { name = "BLUESKY_APP_PASSWORD", valueFrom = "${var.staging_secret_arn}:BLUESKY_APP_PASSWORD::" },
        { name = "STRIPE_SECRET_KEY", valueFrom = "${var.staging_secret_arn}:STRIPE_SECRET_KEY::" },
        { name = "STRIPE_PUBLISHABLE_KEY", valueFrom = "${var.staging_secret_arn}:STRIPE_PUBLISHABLE_KEY::" },
        { name = "STRIPE_WEBHOOK_SECRET", valueFrom = "${var.staging_secret_arn}:STRIPE_WEBHOOK_SECRET::" },
        { name = "STRIPE_PRICE_ID", valueFrom = "${var.staging_secret_arn}:STRIPE_PRICE_ID::" },
        { name = "COGNITO_USER_POOL_ID", valueFrom = "${var.staging_secret_arn}:COGNITO_USER_POOL_ID::" },
        { name = "COGNITO_CLIENT_ID", valueFrom = "${var.staging_secret_arn}:COGNITO_CLIENT_ID::" },
        { name = "GH_TOKEN", valueFrom = "${var.staging_secret_arn}:GH_TOKEN::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/ortobahn"
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "staging-scheduler"
        }
      }

      stopTimeout = 30
    }
  ])

  lifecycle {
    ignore_changes = [container_definitions]
  }
}
