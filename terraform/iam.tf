# IAM policy for image generation (Bedrock + S3)
# Attached to the existing ortobahn-ecs-task role

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
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_image_model}"
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

resource "aws_iam_role_policy_attachment" "ecs_task_image_gen" {
  role       = data.aws_iam_role.ecs_task.name
  policy_arn = aws_iam_policy.image_gen.arn
}
