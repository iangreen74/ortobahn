# S3 bucket for AI-generated images
resource "aws_s3_bucket" "images" {
  bucket = "ortobahn-images"
}

resource "aws_s3_bucket_public_access_block" "images" {
  bucket = aws_s3_bucket.images.id

  # Allow public read via bucket policy (for serving images)
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "images_public_read" {
  bucket = aws_s3_bucket.images.id

  depends_on = [aws_s3_bucket_public_access_block.images]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadImages"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.images.arn}/images/*"
      }
    ]
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "images" {
  bucket = aws_s3_bucket.images.id

  rule {
    id     = "expire-old-images"
    status = "Enabled"

    filter {
      prefix = "images/"
    }

    expiration {
      days = 90
    }
  }
}

# IAM policy for Bedrock image generation + S3 storage
resource "aws_iam_policy" "image_gen" {
  name        = "ortobahn-image-gen"
  description = "Allows ECS tasks to generate images via Bedrock and store them in S3"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BedrockInvokeModel"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-image-generator-v2:0"
      },
      {
        Sid    = "S3WriteImages"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
        ]
        Resource = "${aws_s3_bucket.images.arn}/images/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "image_gen" {
  role       = var.task_role_name
  policy_arn = aws_iam_policy.image_gen.arn
}
