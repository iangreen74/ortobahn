output "distribution_id" {
  value = data.aws_cloudfront_distribution.landing.id
}

output "distribution_domain_name" {
  value = data.aws_cloudfront_distribution.landing.domain_name
}

output "distribution_hosted_zone_id" {
  value = data.aws_cloudfront_distribution.landing.hosted_zone_id
}

output "landing_bucket_name" {
  value = aws_s3_bucket.landing.id
}
