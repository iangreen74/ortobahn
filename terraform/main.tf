terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "ortobahn-terraform-state"
    key            = "ortobahn/terraform.tfstate"
    region         = "us-west-2"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "ortobahn"
      ManagedBy = "terraform"
    }
  }
}

# Alias provider for CloudFront ACM cert (must be in us-east-1)
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "ortobahn"
      ManagedBy = "terraform"
    }
  }
}

# --- Networking ---
module "networking" {
  source = "./modules/networking"

  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
}

# --- ECR ---
module "ecr" {
  source = "./modules/ecr"
}

# --- RDS ---
module "rds" {
  source = "./modules/rds"

  subnet_ids         = module.networking.rds_subnet_ids
  security_group_id  = module.networking.rds_security_group_id
  instance_class     = var.db_instance_class
  allocated_storage  = var.db_allocated_storage
  db_master_password = var.db_master_password
}

# --- Secrets ---
module "secrets" {
  source = "./modules/secrets"
}

# --- ALB ---
module "alb" {
  source = "./modules/alb"

  vpc_id            = module.networking.vpc_id
  public_subnet_ids = module.networking.public_subnet_ids
  security_group_id = module.networking.alb_security_group_id
  certificate_arn   = var.certificate_arn
}

# --- ECS ---
module "ecs" {
  source = "./modules/ecs"

  aws_region               = var.aws_region
  ecr_repository_url       = module.ecr.repository_url
  ecs_subnet_ids           = module.networking.ecs_subnet_ids
  ecs_security_group_id    = module.networking.ecs_security_group_id
  prod_target_group_arn    = module.alb.prod_target_group_arn
  staging_target_group_arn = module.alb.staging_target_group_arn
  prod_secret_arn          = module.secrets.prod_secret_arn
  staging_secret_arn       = module.secrets.staging_secret_arn
  web_cpu                  = var.web_cpu
  web_memory               = var.web_memory
  scheduler_cpu            = var.scheduler_cpu
  scheduler_memory         = var.scheduler_memory
  web_min_count            = var.web_min_count
  web_max_count            = var.web_max_count
}

# --- Monitoring ---
module "monitoring" {
  source = "./modules/monitoring"

  aws_region   = var.aws_region
  alert_emails = var.alert_emails
}

# --- Cognito ---
module "cognito" {
  source = "./modules/cognito"
}

# --- DNS ---
module "dns" {
  source = "./modules/dns"

  alb_dns_name   = module.alb.dns_name
  alb_zone_id    = module.alb.zone_id
  cf_domain_name = module.cdn.distribution_domain_name
  cf_hosted_zone = module.cdn.distribution_hosted_zone_id
}

# --- CDN ---
module "cdn" {
  source = "./modules/cdn"

  certificate_arn_us_east_1 = var.certificate_arn_us_east_1
}

# --- Images (new resources) ---
module "images" {
  source = "./modules/images"

  aws_region     = var.aws_region
  task_role_name = module.ecs.task_role_name
}
