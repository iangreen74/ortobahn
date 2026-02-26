variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "task_role_name" {
  description = "ECS task role name to attach image gen policy to"
  type        = string
}
