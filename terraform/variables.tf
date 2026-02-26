variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-west-2"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "AZs for subnets"
  type        = list(string)
  default     = ["us-west-2a", "us-west-2b"]
}

# ECS sizing
variable "web_cpu" {
  description = "CPU units for web task"
  type        = number
  default     = 256
}

variable "web_memory" {
  description = "Memory (MiB) for web task"
  type        = number
  default     = 512
}

variable "scheduler_cpu" {
  description = "CPU units for scheduler task"
  type        = number
  default     = 256
}

variable "scheduler_memory" {
  description = "Memory (MiB) for scheduler task"
  type        = number
  default     = 512
}

variable "web_min_count" {
  description = "Minimum web tasks (autoscaling)"
  type        = number
  default     = 1
}

variable "web_max_count" {
  description = "Maximum web tasks (autoscaling)"
  type        = number
  default     = 4
}

# RDS
variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage in GB"
  type        = number
  default     = 20
}

variable "db_master_password" {
  description = "RDS master password (ignored after import)"
  type        = string
  sensitive   = true
  default     = "placeholder-ignored-after-import"
}

# Certificates
variable "certificate_arn" {
  description = "ACM certificate ARN (us-west-2) for ALB"
  type        = string
  default     = "arn:aws:acm:us-west-2:418295677815:certificate/3bda6cf5-ca99-4342-b3d5-753caa6b05e7"
}

variable "certificate_arn_us_east_1" {
  description = "ACM certificate ARN (us-east-1) for CloudFront"
  type        = string
  default     = "arn:aws:acm:us-east-1:418295677815:certificate/7181aa53-e36a-4118-a845-c744a92a9d2f"
}

# Monitoring
variable "alert_emails" {
  description = "Email addresses for SNS alert notifications"
  type        = list(string)
  default     = ["ian@vaultscaler.com"]
}
