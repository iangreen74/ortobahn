# AWS Secrets Manager configuration for database credentials

locals {
  secret_name_prefix = "ortobahn/${var.environment}"
}

# Database credentials secret
resource "aws_secretsmanager_secret" "db_credentials" {
  name_prefix             = "${local.secret_name_prefix}/db-credentials-"
  description             = "Database credentials for Ortobahn ${var.environment}"
  recovery_window_in_days = var.environment == "production" ? 30 : 0

  tags = merge(var.common_tags, {
    Name        = "${local.secret_name_prefix}/db-credentials"
    Environment = var.environment
    ManagedBy   = "terraform"
  })
}

# Initial secret version with generated password
resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id = aws_secretsmanager_secret.db_credentials.id
  secret_string = jsonencode({
    username          = var.db_username
    password          = random_password.db_password.result
    engine            = "postgres"
    host              = var.db_host
    port              = var.db_port
    dbname            = var.db_name
    POSTGRES_PASSWORD = random_password.db_password.result
  })
}

# Generate secure random password
resource "random_password" "db_password" {
  length  = 32
  special = true
  # Exclude characters that might cause issues in connection strings
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# Rotation configuration (Lambda required for full rotation)
resource "aws_secretsmanager_secret_rotation" "db_credentials" {
  count               = var.enable_secret_rotation ? 1 : 0
  secret_id           = aws_secretsmanager_secret.db_credentials.id
  rotation_lambda_arn = aws_lambda_function.rotate_secret[0].arn

  rotation_rules {
    automatically_after_days = var.rotation_days
  }
}

# IAM policy for application to read secrets
resource "aws_iam_policy" "read_db_secret" {
  name_prefix = "${var.environment}-ortobahn-read-db-secret-"
  description = "Allow reading database credentials from Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = aws_secretsmanager_secret.db_credentials.arn
      }
    ]
  })
}

# Outputs
output "db_secret_arn" {
  description = "ARN of the database credentials secret"
  value       = aws_secretsmanager_secret.db_credentials.arn
  sensitive   = true
}

output "db_secret_name" {
  description = "Name of the database credentials secret"
  value       = aws_secretsmanager_secret.db_credentials.name
}

output "db_secret_policy_arn" {
  description = "ARN of IAM policy for reading database secret"
  value       = aws_iam_policy.read_db_secret.arn
}
