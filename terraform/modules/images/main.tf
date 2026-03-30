resource "aws_s3_bucket" "images" {
  bucket = var.bucket_name

  tags = {
    Name        = "Ortobahn Images"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_public_access_block" "images" {
  bucket = aws_s3_bucket.images.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_identity" "images_oai" {
  comment = "OAI for ${var.bucket_name}"
}

data "aws_iam_policy_document" "s3_policy" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.images.arn}/*"]

    principals {
      type        = "AWS"
      identifiers = [aws_cloudfront_origin_access_identity.images_oai.iam_arn]
    }
  }
}

resource "aws_s3_bucket_policy" "images" {
  bucket = aws_s3_bucket.images.id
  policy = data.aws_iam_policy_document.s3_policy.json
}

resource "aws_cloudfront_distribution" "images_distribution" {
  origin {
    domain_name = aws_s3_bucket.images.bucket_regional_domain_name
    origin_id   = "S3-${var.bucket_name}"

    s3_origin_config {
      origin_access_identity = aws_cloudfront_origin_access_identity.images_oai.cloudfront_access_identity_path
    }
  }

  enabled             = true
  is_ipv6_enabled     = true
  comment             = "CloudFront distribution for ${var.bucket_name}"
  default_root_object = ""

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "S3-${var.bucket_name}"

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 3600
    max_ttl                = 86400

    trusted_signers = ["self"]
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Environment = var.environment
  }
}

output "bucket_name" {
  value = aws_s3_bucket.images.id
}

output "bucket_arn" {
  value = aws_s3_bucket.images.arn
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.images_distribution.id
}

output "cloudfront_domain_name" {
  value = aws_cloudfront_distribution.images_distribution.domain_name
}

output "cloudfront_key_pair_id" {
  value = aws_cloudfront_distribution.images_distribution.trusted_signers[0]
}
