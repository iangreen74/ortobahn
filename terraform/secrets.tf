# AWS Secrets Manager resources for Ortobahn

# Secrets for each environment
resource "aws_secretsmanager_secret" "database_url" {
  for_each = toset(["development", "staging", "production"])

  name        = "ortobahn/${each.key}/database-url"
  description = "Database connection URL for ${each.key}"

  rotation_rules {
    automatically_after_days = 90
  }

  tags = {
    Environment = each.key
    Application = "ortobahn"
    ManagedBy   = "terraform"
  }
}

resource "aws_secretsmanager_secret" "api_key" {
  for_each = toset(["development", "staging", "production"])

  name        = "ortobahn/${each.key}/api-key"
  description = "API key for ${each.key}"

  rotation_rules {
    automatically_after_days = 30
  }

  tags = {
    Environment = each.key
    Application = "ortobahn"
    ManagedBy   = "terraform"
  }
}

resource "aws_secretsmanager_secret" "secret_key" {
  for_each = toset(["development", "staging", "production"])

  name        = "ortobahn/${each.key}/secret-key"
  description = "Secret key for ${each.key}"

  rotation_rules {
    automatically_after_days = 90
  }

  tags = {
    Environment = each.key
    Application = "ortobahn"
    ManagedBy   = "terraform"
  }
}

# IAM role for Lambda rotation function
resource "aws_iam_role" "secret_rotation" {
  name = "ortobahn-secret-rotation"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Application = "ortobahn"
    ManagedBy   = "terraform"
  }
}

# IAM policy for secret rotation
resource "aws_iam_role_policy" "secret_rotation" {
  name = "secret-rotation-policy"
  role = aws_iam_role.secret_rotation.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecretVersionStage"
        ]
        Resource = "arn:aws:secretsmanager:*:*:secret:ortobahn/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# IAM policy for application to read secrets
resource "aws_iam_policy" "read_secrets" {
  name        = "ortobahn-read-secrets"
  description = "Allow Ortobahn application to read secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = "arn:aws:secretsmanager:*:*:secret:ortobahn/*"
      }
    ]
  })

  tags = {
    Application = "ortobahn"
    ManagedBy   = "terraform"
  }
}

# Outputs
output "secret_rotation_role_arn" {
  description = "ARN of the secret rotation Lambda role"
  value       = aws_iam_role.secret_rotation.arn
}

output "read_secrets_policy_arn" {
  description = "ARN of the policy to read secrets"
  value       = aws_iam_policy.read_secrets.arn
}
