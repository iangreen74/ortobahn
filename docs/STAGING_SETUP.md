# Staging Environment & CI/CD Setup

This guide covers one-time AWS setup for staging, GitHub environment configuration, branch protection, and ECR lifecycle policy.

## Prerequisites

- AWS CLI configured for account `418295677815` in `us-west-2`
- GitHub admin access to `iangreen74/ortobahn`
- Access to the existing RDS instance

## 1. Create Staging Database

Create a second database on the existing RDS instance (zero extra cost):

```bash
# Connect to the existing RDS and create the staging database
psql "$DATABASE_URL" -c "CREATE DATABASE ortobahn_staging;"
```

## 2. Create Staging Secret in Secrets Manager

```bash
aws secretsmanager create-secret \
  --name ortobahn/staging \
  --region us-west-2 \
  --secret-string '{
    "ANTHROPIC_API_KEY": "<same-as-prod-or-test-key>",
    "DATABASE_URL": "postgresql://ortobahn_app:<password>@<rds-endpoint>:5432/ortobahn_staging",
    "ORTOBAHN_SECRET_KEY": "<generate-new-32-char-key>",
    "BLUESKY_HANDLE": "<test-handle-or-same>",
    "BLUESKY_APP_PASSWORD": "<test-password-or-same>",
    "STRIPE_SECRET_KEY": "<stripe-TEST-mode-key>",
    "STRIPE_PUBLISHABLE_KEY": "<stripe-TEST-mode-key>",
    "STRIPE_WEBHOOK_SECRET": "<stripe-test-webhook-secret>",
    "STRIPE_PRICE_ID": "<stripe-test-price-id>",
    "COGNITO_USER_POOL_ID": "<same-or-staging-pool>",
    "COGNITO_CLIENT_ID": "<same-or-staging-client>",
    "GH_TOKEN": "<same>"
  }'
```

After creation, note the full ARN suffix (e.g., `ortobahn/staging-AbCdEf`). Then update the `CHANGE_ME` placeholder in both staging task definitions:

```bash
# Replace the placeholder in staging task defs with the actual secret suffix
sed -i '' 's/ortobahn\/staging-CHANGE_ME/ortobahn\/staging-AbCdEf/g' \
  ecs/staging-web-task-def.json ecs/staging-scheduler-task-def.json
```

## 3. Grant Execution Role Access to Staging Secret

The `ortobahn-ecs-execution` role needs permission to read the staging secret.

```bash
# Find the existing policy
aws iam list-attached-role-policies --role-name ortobahn-ecs-execution

# Update the policy to include the staging secret ARN alongside the prod one:
#   "arn:aws:secretsmanager:us-west-2:418295677815:secret:ortobahn/staging-*"
```

## 4. Create Staging ECS Services

First, find the networking config from existing prod services:

```bash
aws ecs describe-services --cluster ortobahn \
  --services ortobahn-web-v2 --region us-west-2 \
  --query 'services[0].networkConfiguration'
```

Then create the staging services:

```bash
# Register staging task definitions
aws ecs register-task-definition \
  --cli-input-json file://ecs/staging-web-task-def.json --region us-west-2

aws ecs register-task-definition \
  --cli-input-json file://ecs/staging-scheduler-task-def.json --region us-west-2

# Create staging web service
aws ecs create-service \
  --cluster ortobahn \
  --service-name ortobahn-web-staging \
  --task-definition ortobahn-web-staging \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<SUBNET_IDS>],securityGroups=[<SG_IDS>],assignPublicIp=ENABLED}" \
  --region us-west-2

# Create staging scheduler service
aws ecs create-service \
  --cluster ortobahn \
  --service-name ortobahn-scheduler-staging \
  --task-definition ortobahn-scheduler-staging \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<SUBNET_IDS>],securityGroups=[<SG_IDS>],assignPublicIp=ENABLED}" \
  --region us-west-2
```

## 5. Create GitHub Environments

Go to: `https://github.com/iangreen74/ortobahn/settings/environments`

1. **Create `staging` environment** — no protection rules (auto-deploys on every merge to main)
2. **Create `production` environment** — add protection rule: **Required reviewers** (add yourself). This creates a manual approval gate before prod deploys.

## 6. Configure Branch Protection

Go to: `https://github.com/iangreen74/ortobahn/settings/branches`

Configure the `main` branch rule:

| Setting | Value |
|---------|-------|
| Require a pull request before merging | **Enabled** |
| Require status checks to pass | **Enabled** |
| Required checks | `lint`, `typecheck`, `test (3.10)`, `test (3.11)`, `test (3.12)` |
| Require branches to be up to date | Enabled |
| **Do not allow bypassing the above settings** | **Enabled** (critical!) |

The "do not allow bypassing" setting is what prevents even repo admins from pushing directly to main. Without this, branch protection is advisory only.

## 7. Set ECR Lifecycle Policy

Prevent unlimited image accumulation — keep the last 50 images (~1 month of rollback history):

```bash
aws ecr put-lifecycle-policy --repository-name ortobahn --region us-west-2 \
  --lifecycle-policy-text '{
    "rules": [{
      "rulePriority": 1,
      "description": "Keep last 50 images",
      "selection": {
        "tagStatus": "any",
        "countType": "imageCountMoreThan",
        "countNumber": 50
      },
      "action": {"type": "expire"}
    }]
  }'
```

## Deployment Flow After Setup

```
Push to main → CI (lint, typecheck, test)
                ↓ (on success)
         Build Docker image → Push to ECR (SHA + latest tags)
                ↓ (automatic)
         Deploy to staging → Wait for healthy
                ↓ (manual approval in GitHub Actions UI)
         Deploy to production → Wait for healthy
```

### Manual Operations

```bash
# Check what's deployed
make deploy-status

# List recent ECR images (for finding rollback SHAs)
make ecr-tags

# Deploy specific SHA to staging
make deploy-staging SHA=abc123f

# Promote staging SHA to production
make promote SHA=abc123f

# Rollback production to a known-good SHA
make rollback SHA=abc123f
```

Rollback is instant — it reuses the existing Docker image in ECR (no rebuild). Typical rollback time: 2-3 minutes.
