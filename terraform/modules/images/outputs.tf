output "bucket_name" {
  value = aws_s3_bucket.images.id
}

output "bucket_arn" {
  value = aws_s3_bucket.images.arn
}

output "bucket_url" {
  value = "https://${aws_s3_bucket.images.id}.s3.${var.aws_region}.amazonaws.com"
}

output "policy_arn" {
  value = aws_iam_policy.image_gen.arn
}
