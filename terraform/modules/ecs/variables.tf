variable "aws_region" {
  type = string
}

variable "ecr_repository_url" {
  type = string
}

variable "ecs_subnet_ids" {
  type = list(string)
}

variable "ecs_security_group_id" {
  type = string
}

variable "prod_target_group_arn" {
  type = string
}

variable "staging_target_group_arn" {
  type = string
}

variable "prod_secret_arn" {
  type = string
}

variable "staging_secret_arn" {
  type = string
}

variable "web_cpu" {
  type    = number
  default = 256
}

variable "web_memory" {
  type    = number
  default = 512
}

variable "scheduler_cpu" {
  type    = number
  default = 256
}

variable "scheduler_memory" {
  type    = number
  default = 512
}

variable "web_min_count" {
  type    = number
  default = 1
}

variable "web_max_count" {
  type    = number
  default = 4
}
