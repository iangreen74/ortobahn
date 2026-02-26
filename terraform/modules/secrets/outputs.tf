output "prod_secret_arn" {
  value = aws_secretsmanager_secret.prod.arn
}

output "staging_secret_arn" {
  value = aws_secretsmanager_secret.staging.arn
}
