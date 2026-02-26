# Manages the secret resources only — values are managed out-of-band
resource "aws_secretsmanager_secret" "prod" {
  name = "ortobahn/prod"

  tags = { Name = "ortobahn-prod-secrets" }

  lifecycle {
    # Secret values managed via AWS Console/CLI, not Terraform
    ignore_changes = [description]
  }
}

resource "aws_secretsmanager_secret" "staging" {
  name = "ortobahn/staging"

  tags = { Name = "ortobahn-staging-secrets" }

  lifecycle {
    ignore_changes = [description]
  }
}
