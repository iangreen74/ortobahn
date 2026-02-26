output "image_bucket_name" {
  description = "S3 bucket name for generated images"
  value       = aws_s3_bucket.images.id
}

output "image_bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.images.arn
}

output "image_bucket_url" {
  description = "Base URL for serving images"
  value       = "https://${aws_s3_bucket.images.id}.s3.${var.aws_region}.amazonaws.com"
}

output "image_gen_policy_arn" {
  description = "IAM policy ARN for image generation permissions"
  value       = aws_iam_policy.image_gen.arn
}
