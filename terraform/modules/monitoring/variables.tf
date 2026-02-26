variable "aws_region" {
  type = string
}

variable "alert_emails" {
  type    = list(string)
  default = []
}
