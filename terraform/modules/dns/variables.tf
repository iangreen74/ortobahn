variable "alb_dns_name" {
  description = "ALB DNS name for app.ortobahn.com"
  type        = string
}

variable "alb_zone_id" {
  description = "ALB hosted zone ID"
  type        = string
}

variable "cf_domain_name" {
  description = "CloudFront distribution domain name"
  type        = string
}

variable "cf_hosted_zone" {
  description = "CloudFront hosted zone ID (always Z2FDTNDATAQYW2)"
  type        = string
}
