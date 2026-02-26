# =============================================================================
# Terraform Import Blocks — existing ortobahn infrastructure
# These import blocks bring manually-created resources under Terraform management.
# After successful import + apply, these blocks can optionally be removed.
# =============================================================================

# --- Networking: VPC ---
import {
  to = module.networking.aws_vpc.main
  id = "vpc-0ec0aabd936179b84"
}

# --- Networking: Subnets ---
import {
  to = module.networking.aws_subnet.public[0]
  id = "subnet-01cad75810686d918"
}

import {
  to = module.networking.aws_subnet.public[1]
  id = "subnet-0354b7a9503489a84"
}

import {
  to = module.networking.aws_subnet.private_ecs[0]
  id = "subnet-02e43b3f96dd5a654"
}

import {
  to = module.networking.aws_subnet.private_ecs[1]
  id = "subnet-0abb5d31aa473c65c"
}

import {
  to = module.networking.aws_subnet.private_rds[0]
  id = "subnet-0dfea4c8da9bfd676"
}

import {
  to = module.networking.aws_subnet.private_rds[1]
  id = "subnet-0c9dc6ca4be1e4fe1"
}

# --- Networking: Internet Gateway ---
import {
  to = module.networking.aws_internet_gateway.main
  id = "igw-06dc6d1b5f11421d5"
}

# --- Networking: NAT Gateway + EIP ---
import {
  to = module.networking.aws_eip.nat
  id = "eipalloc-0fa1579cc32eb88f9"
}

import {
  to = module.networking.aws_nat_gateway.main
  id = "nat-0e690c45bc70f880e"
}

# --- Networking: Route Tables ---
import {
  to = module.networking.aws_route_table.public
  id = "rtb-0c504a7ae15cb9127"
}

import {
  to = module.networking.aws_route_table.private
  id = "rtb-065803472d9bb128c"
}

# --- Networking: Route Table Associations (format: subnet_id/route_table_id) ---
import {
  to = module.networking.aws_route_table_association.public[0]
  id = "subnet-01cad75810686d918/rtb-0c504a7ae15cb9127"
}

import {
  to = module.networking.aws_route_table_association.public[1]
  id = "subnet-0354b7a9503489a84/rtb-0c504a7ae15cb9127"
}

import {
  to = module.networking.aws_route_table_association.private_ecs[0]
  id = "subnet-02e43b3f96dd5a654/rtb-065803472d9bb128c"
}

import {
  to = module.networking.aws_route_table_association.private_ecs[1]
  id = "subnet-0abb5d31aa473c65c/rtb-065803472d9bb128c"
}

import {
  to = module.networking.aws_route_table_association.private_rds[0]
  id = "subnet-0dfea4c8da9bfd676/rtb-065803472d9bb128c"
}

import {
  to = module.networking.aws_route_table_association.private_rds[1]
  id = "subnet-0c9dc6ca4be1e4fe1/rtb-065803472d9bb128c"
}

# --- Networking: Security Groups ---
import {
  to = module.networking.aws_security_group.alb
  id = "sg-090ade0bb03864ab3"
}

import {
  to = module.networking.aws_security_group.ecs
  id = "sg-01fb9e64417406da9"
}

import {
  to = module.networking.aws_security_group.rds
  id = "sg-06e358a3553c3b145"
}

import {
  to = module.networking.aws_security_group.vpce
  id = "sg-05f3d9a98ea0fb95a"
}

# --- Networking: Security Group Rules ---
import {
  to = module.networking.aws_vpc_security_group_ingress_rule.alb_http
  id = "sgr-0a68caf53ab27673c"
}

import {
  to = module.networking.aws_vpc_security_group_ingress_rule.alb_https
  id = "sgr-09a8791630ae73226"
}

import {
  to = module.networking.aws_vpc_security_group_ingress_rule.alb_staging
  id = "sgr-088863f68a5c977ac"
}

import {
  to = module.networking.aws_vpc_security_group_egress_rule.alb_all
  id = "sgr-0a62d24af8c667c0d"
}

import {
  to = module.networking.aws_vpc_security_group_ingress_rule.ecs_from_alb
  id = "sgr-050c933a121704eb0"
}

import {
  to = module.networking.aws_vpc_security_group_egress_rule.ecs_all
  id = "sgr-06fba254f45e73036"
}

import {
  to = module.networking.aws_vpc_security_group_ingress_rule.rds_from_ecs
  id = "sgr-05431e52944240643"
}

import {
  to = module.networking.aws_vpc_security_group_egress_rule.rds_all
  id = "sgr-0684912398b56498d"
}

import {
  to = module.networking.aws_vpc_security_group_ingress_rule.vpce_https
  id = "sgr-026de4f2ff898bf61"
}

import {
  to = module.networking.aws_vpc_security_group_egress_rule.vpce_all
  id = "sgr-0efb76ae81d35f741"
}

# --- Networking: VPC Endpoints ---
import {
  to = module.networking.aws_vpc_endpoint.s3
  id = "vpce-04e83c65e0880fcd2"
}

import {
  to = module.networking.aws_vpc_endpoint.ecr_api
  id = "vpce-05542cb8e9fd39eed"
}

import {
  to = module.networking.aws_vpc_endpoint.ecr_dkr
  id = "vpce-040a49a6185bc4ab7"
}

import {
  to = module.networking.aws_vpc_endpoint.logs
  id = "vpce-00de12b3faeae595e"
}

import {
  to = module.networking.aws_vpc_endpoint.secretsmanager
  id = "vpce-066be2fdc3d03b1a3"
}

import {
  to = module.networking.aws_vpc_endpoint.bedrock
  id = "vpce-0c801a8ae3c78ff9e"
}

# --- ECR ---
import {
  to = module.ecr.aws_ecr_repository.main
  id = "ortobahn"
}

# --- RDS ---
import {
  to = module.rds.aws_db_subnet_group.main
  id = "ortobahn-db-v2"
}

import {
  to = module.rds.aws_db_instance.main
  id = "ortobahn-pg-v2"
}

# --- Secrets ---
import {
  to = module.secrets.aws_secretsmanager_secret.prod
  id = "arn:aws:secretsmanager:us-west-2:418295677815:secret:ortobahn/prod-TaDxEG"
}

import {
  to = module.secrets.aws_secretsmanager_secret.staging
  id = "arn:aws:secretsmanager:us-west-2:418295677815:secret:ortobahn/staging-2vpqOw"
}

# --- ALB ---
import {
  to = module.alb.aws_lb.main
  id = "arn:aws:elasticloadbalancing:us-west-2:418295677815:loadbalancer/app/ortobahn-alb-v2/8deb99e9e3870572"
}

import {
  to = module.alb.aws_lb_target_group.prod
  id = "arn:aws:elasticloadbalancing:us-west-2:418295677815:targetgroup/ortobahn-tg-v2/a6e9c98ad338ae73"
}

import {
  to = module.alb.aws_lb_target_group.staging
  id = "arn:aws:elasticloadbalancing:us-west-2:418295677815:targetgroup/ortobahn-tg-staging/54cf5ef7b4183a93"
}

import {
  to = module.alb.aws_lb_listener.https
  id = "arn:aws:elasticloadbalancing:us-west-2:418295677815:listener/app/ortobahn-alb-v2/8deb99e9e3870572/57594eea109307f3"
}

import {
  to = module.alb.aws_lb_listener.http_redirect
  id = "arn:aws:elasticloadbalancing:us-west-2:418295677815:listener/app/ortobahn-alb-v2/8deb99e9e3870572/8c0a70ea6f66df8e"
}

import {
  to = module.alb.aws_lb_listener.staging
  id = "arn:aws:elasticloadbalancing:us-west-2:418295677815:listener/app/ortobahn-alb-v2/8deb99e9e3870572/ec923b98e023206d"
}

# --- ECS: Cluster ---
import {
  to = module.ecs.aws_ecs_cluster.main
  id = "ortobahn"
}

# --- ECS: IAM Roles ---
import {
  to = module.ecs.aws_iam_role.execution
  id = "ortobahn-ecs-execution"
}

import {
  to = module.ecs.aws_iam_role.task
  id = "ortobahn-ecs-task"
}

import {
  to = module.ecs.aws_iam_role_policy_attachment.execution_base
  id = "ortobahn-ecs-execution/arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

import {
  to = module.ecs.aws_iam_role_policy.execution_secrets
  id = "ortobahn-ecs-execution:SecretsManagerRead"
}

import {
  to = module.ecs.aws_iam_role_policy.task_bedrock
  id = "ortobahn-ecs-task:bedrock-invoke"
}

# --- ECS: Services ---
import {
  to = module.ecs.aws_ecs_service.web
  id = "ortobahn/ortobahn-web-v2"
}

import {
  to = module.ecs.aws_ecs_service.scheduler
  id = "ortobahn/ortobahn-scheduler-v2"
}

import {
  to = module.ecs.aws_ecs_service.web_staging
  id = "ortobahn/ortobahn-web-staging"
}

import {
  to = module.ecs.aws_ecs_service.scheduler_staging
  id = "ortobahn/ortobahn-scheduler-staging"
}

# --- ECS: Autoscaling ---
import {
  to = module.ecs.aws_appautoscaling_target.web
  id = "ecs/service/ortobahn/ortobahn-web-v2/ecs:service:DesiredCount"
}

import {
  to = module.ecs.aws_appautoscaling_policy.web_cpu
  id = "ecs/service/ortobahn/ortobahn-web-v2/ecs:service:DesiredCount/ortobahn-web-cpu-scaling"
}

import {
  to = module.ecs.aws_appautoscaling_policy.web_memory
  id = "ecs/service/ortobahn/ortobahn-web-v2/ecs:service:DesiredCount/ortobahn-web-memory-scaling"
}

# --- Monitoring: Log Group ---
import {
  to = module.monitoring.aws_cloudwatch_log_group.ecs
  id = "/ecs/ortobahn"
}

# --- Monitoring: SNS ---
import {
  to = module.monitoring.aws_sns_topic.alerts
  id = "arn:aws:sns:us-west-2:418295677815:ortobahn-alerts"
}

import {
  to = module.monitoring.aws_sns_topic_subscription.email[0]
  id = "arn:aws:sns:us-west-2:418295677815:ortobahn-alerts:a7f1d62f-f37d-480c-ab51-a23b40a403d3"
}

# --- Monitoring: Active CloudWatch Alarms ---
import {
  to = module.monitoring.aws_cloudwatch_metric_alarm.alb_5xx
  id = "ortobahn-alb-5xx"
}

import {
  to = module.monitoring.aws_cloudwatch_metric_alarm.target_5xx
  id = "ortobahn-target-5xx"
}

import {
  to = module.monitoring.aws_cloudwatch_metric_alarm.ecs_web_cpu
  id = "ortobahn-ecs-web-cpu"
}

import {
  to = module.monitoring.aws_cloudwatch_metric_alarm.ecs_web_memory
  id = "ortobahn-ecs-web-memory"
}

import {
  to = module.monitoring.aws_cloudwatch_metric_alarm.ecs_scheduler_cpu
  id = "ortobahn-ecs-scheduler-cpu"
}

import {
  to = module.monitoring.aws_cloudwatch_metric_alarm.rds_cpu
  id = "ortobahn-rds-cpu"
}

import {
  to = module.monitoring.aws_cloudwatch_metric_alarm.rds_connections
  id = "ortobahn-rds-connections"
}

import {
  to = module.monitoring.aws_cloudwatch_metric_alarm.rds_storage
  id = "ortobahn-rds-storage"
}

# --- Cognito ---
import {
  to = module.cognito.aws_cognito_user_pool.main
  id = "us-west-2_TdKMSy7uS"
}

import {
  to = module.cognito.aws_cognito_user_pool_client.web
  id = "us-west-2_TdKMSy7uS/tdj14docqsnb6s2n5aoq6gspr"
}

# --- DNS: Route 53 ---
import {
  to = module.dns.aws_route53_zone.main
  id = "Z070114934ZDNUAR37BXH"
}

import {
  to = module.dns.aws_route53_record.root
  id = "Z070114934ZDNUAR37BXH_ortobahn.com_A"
}

import {
  to = module.dns.aws_route53_record.www
  id = "Z070114934ZDNUAR37BXH_www.ortobahn.com_A"
}

import {
  to = module.dns.aws_route53_record.app
  id = "Z070114934ZDNUAR37BXH_app.ortobahn.com_A"
}

# --- CDN: S3 Landing ---
import {
  to = module.cdn.aws_s3_bucket.landing
  id = "ortobahn-landing"
}

import {
  to = module.cdn.aws_s3_bucket_policy.landing
  id = "ortobahn-landing"
}

# --- CDN: CloudFront (distribution is data source, not managed) ---
import {
  to = module.cdn.aws_cloudfront_origin_access_control.landing
  id = "E2IBKUMQJY80V9"
}

import {
  to = module.cdn.aws_cloudfront_function.redirect
  id = "ortobahn-redirect"
}

# =============================================================================
# NOTE: The following stale CloudWatch alarms reference old v1 resources
# and should be manually deleted (not imported):
#   - ortobahn-alb-5xx-errors (references old ALB)
#   - ortobahn-alb-unhealthy-hosts (references old ALB/TG)
#   - ortobahn-rds-cpu-high (references old RDS instance ortobahn-pg)
#   - ortobahn-rds-low-storage (references old RDS instance ortobahn-pg)
#   - ortobahn-scheduler-unhealthy-tasks (references old service name)
#   - ortobahn-web-unhealthy-tasks (references old service name)
#
# Delete with: aws cloudwatch delete-alarms --alarm-names \
#   ortobahn-alb-5xx-errors ortobahn-alb-unhealthy-hosts \
#   ortobahn-rds-cpu-high ortobahn-rds-low-storage \
#   ortobahn-scheduler-unhealthy-tasks ortobahn-web-unhealthy-tasks \
#   --region us-west-2
# =============================================================================
