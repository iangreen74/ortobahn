variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-west-2"
}

variable "image_bucket_name" {
  description = "S3 bucket name for generated images"
  type        = string
  default     = "ortobahn-images"
}

variable "bedrock_image_model" {
  description = "Bedrock model ID for image generation"
  type        = string
  default     = "amazon.titan-image-generator-v2:0"
}

variable "image_expiry_days" {
  description = "Days before generated images are automatically deleted"
  type        = number
  default     = 90
}
