# INFRASTRUCTURE.md - Ortobahn Infrastructure Registry

> Single source of truth for all infrastructure values. Referenced by CLAUDE.md and MEMORY.md.
> **Any infrastructure change must update this file.**

## URLs

| Environment | URL | Purpose |
|-------------|-----|---------|
| Production app | `https://app.ortobahn.com` | ECS web service (FastAPI) |
| Staging app | `http://ortobahn-alb-v2-1644127875.us-west-2.elb.amazonaws.com:8080` | Staging ECS (HTTP, port 8080) |
| Landing page | `https://ortobahn.com` | Static S3 + CloudFront |
| Glass dashboard | `https://app.ortobahn.com/glass` | Public operational transparency |
| Health check | `https://app.ortobahn.com/health` | ALB/ECS health probe (JSON) |
| GitHub repo | `https://github.com/angreen74/ortobahn` | Source code |

## AWS Account

| Field | Value |
|-------|-------|
| Account ID | `418295677815` |
| Region | `us-west-2` |

## ECS

| Resource | Name |
|----------|------|
| Cluster | `ortobahn` |
| Prod web service | `ortobahn-web-v2` |
| Prod scheduler service | `ortobahn-scheduler-v2` |
| Staging web service | `ortobahn-web-staging` |
| Staging scheduler service | `ortobahn-scheduler-staging` |
| Execution role | `ortobahn-ecs-execution` |
| Task role | `ortobahn-ecs-task` |
| Log group | `/ecs/ortobahn` |
| CPU / Memory | 256 (0.25 vCPU) / 512 MB |
| Container port | 8000 |

## ECR

| Field | Value |
|-------|-------|
| Repository | `418295677815.dkr.ecr.us-west-2.amazonaws.com/ortobahn` |
| Image tags | Full commit SHA + `latest` |
| Lifecycle | Keep last 50 images |

## Secrets Manager

| Secret | Path | Keys stored |
|--------|------|-------------|
| Production | `ortobahn/prod` (ARN suffix: `-TaDxEG`) | ANTHROPIC_API_KEY, DATABASE_URL, ORTOBAHN_SECRET_KEY, BLUESKY_HANDLE, BLUESKY_APP_PASSWORD, STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_ID, COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, GH_TOKEN |
| Staging | `ortobahn/staging` (ARN suffix: `-2vpqOw`) | Same keys as prod |

## CloudFront / S3

| Resource | Identifier |
|----------|------------|
| CloudFront distribution | `E1R6PE83G6T984` |
| S3 bucket (landing) | `ortobahn-landing` |

## WAF

| Resource | Name |
|----------|------|
| Web ACL | `ortobahn-waf` |
| Rules | CommonRuleSet, KnownBadInputs, SQLiRuleSet |

## ALB / Networking

| Resource | Identifier |
|----------|------------|
| ALB | `ortobahn-alb-v2` (`app/ortobahn-alb-v2/8deb99e9e3870572`) |
| ALB DNS | `ortobahn-alb-v2-1644127875.us-west-2.elb.amazonaws.com` |
| ALB security group | `sg-090ade0bb03864ab3` |
| ECS security group | `sg-01fb9e64417406da9` |
| Prod target group | `ortobahn-tg-v2` |
| Staging target group | `ortobahn-tg-staging` |
| Prod listener | HTTPS :443 → `ortobahn-tg-v2` |
| HTTP redirect listener | HTTP :80 → HTTPS |
| Staging listener | HTTP :8080 → `ortobahn-tg-staging` |
| VPC | `vpc-0ec0aabd936179b84` |
| Subnets | `subnet-0abb5d31aa473c65c`, `subnet-02e43b3f96dd5a654` |

## IAM Roles

| Role | Key Permissions |
|------|----------------|
| `ortobahn-deploy` | ECR push, ECS RegisterTaskDefinition/UpdateService/DescribeServices, S3/CloudFront (landing), PassRole |
| `ortobahn-ecs-execution` | AmazonECSTaskExecutionRolePolicy + SecretsManagerRead (prod + staging ARNs) |
| `ortobahn-ecs-task` | Bedrock, S3, other runtime permissions |

## EC2 Fallback

| Resource | Identifier |
|----------|------------|
| Instance ID | `i-02525f63177387819` |

## GitHub Secrets (names only)

| Secret | Purpose |
|--------|---------|
| `AWS_DEPLOY_ROLE_ARN` | IAM role for OIDC deploy |
| `GH_APP_ID` | GitHub App for CIFix PR creation |
| `GH_APP_PRIVATE_KEY` | GitHub App private key |
| `STAGING_URL` | Staging ALB base URL |
| `PROD_URL` | `https://app.ortobahn.com` |
| `ORTOBAHN_SECRET_KEY` | Same value as in Secrets Manager prod |

## GitHub Environments

| Environment | Approval |
|-------------|----------|
| `staging` | Auto (no approval) |
| `production` | Manual approval required |

## Task Definition Files

| File | Purpose |
|------|---------|
| `ecs/web-task-def.json` | Prod web |
| `ecs/scheduler-task-def.json` | Prod scheduler |
| `ecs/staging-web-task-def.json` | Staging web |
| `ecs/staging-scheduler-task-def.json` | Staging scheduler |
| `ecs/waf-rules.json` | WAF configuration |

## Quick Reference Commands

```bash
make deploy-status                # Show running ECS services (prod + staging)
make ecr-tags                     # List recent ECR images (find rollback SHAs)
make deploy-ecs                   # Full build + push + promote to prod
make deploy-staging SHA=abc123f   # Deploy specific SHA to staging
make promote SHA=abc123f          # Promote SHA to production
make rollback SHA=abc123f         # Rollback prod to known-good SHA
make deploy-landing               # Push landing page to S3 + CloudFront invalidation
```
