.PHONY: install install-web test lint lint-fix typecheck run dry-run generate seed healthcheck validate dashboard web docker-build docker-up docker-down docker-logs deploy-landing deploy-ec2 deploy-ecs deploy-staging promote rollback deploy-status ecr-tags waf-setup clean

ECR_REPO = 418295677815.dkr.ecr.us-west-2.amazonaws.com/ortobahn
CLUSTER = ortobahn

install:
	python3 -m pip install -e ".[dev]"
	@echo "\nDone. Copy .env.example to .env and configure your API keys."

install-web:
	python3 -m pip install -e ".[dev,web]"
	@echo "\nDone. Web dashboard dependencies installed."

test:
	python3 -m pytest

lint:
	python3 -m ruff check ortobahn/ tests/
	python3 -m ruff format --check ortobahn/ tests/

lint-fix:
	python3 -m ruff check --fix ortobahn/ tests/
	python3 -m ruff format ortobahn/ tests/

typecheck:
	python3 -m mypy ortobahn/

run:
	python3 -m ortobahn run

dry-run:
	python3 -m ortobahn run --dry-run

generate:
	python3 -m ortobahn generate --client vaultscaler --platforms twitter,linkedin,google_ads

seed:
	python3 -m ortobahn seed

healthcheck:
	python3 -m ortobahn healthcheck

validate: test healthcheck

dashboard:
	python3 -m ortobahn dashboard

web:
	python3 -m ortobahn web

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

deploy-landing:
	aws s3 sync ortobahn/landing/ s3://ortobahn-landing/ --delete
	aws cloudfront create-invalidation --distribution-id E1R6PE83G6T984 --paths "/*" > /dev/null
	@echo "\nLanding page deployed to ortobahn.com."

deploy-ec2:
	@echo "Deploying to EC2 via SSM..."
	GITHUB_TOKEN= aws ssm send-command --region us-west-2 \
		--instance-ids i-02525f63177387819 \
		--document-name AWS-RunShellScript \
		--parameters 'commands=["cd /app/ortobahn && git pull && docker compose build && docker compose up -d"]' \
		--query 'Command.CommandId' --output text
	@echo "\nDeploy command sent. Check SSM console for status."

deploy-ecs:
	$(eval SHA := $(shell git rev-parse --short=7 HEAD))
	@echo "Building and pushing $(SHA) to ECR..."
	aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 418295677815.dkr.ecr.us-west-2.amazonaws.com
	docker build -t ortobahn .
	docker tag ortobahn:latest $(ECR_REPO):$(SHA)
	docker tag ortobahn:latest $(ECR_REPO):latest
	docker push $(ECR_REPO):$(SHA)
	docker push $(ECR_REPO):latest
	$(MAKE) promote SHA=$(SHA)

# Deploy a specific SHA to staging
# Usage: make deploy-staging SHA=abc123f
deploy-staging:
ifndef SHA
	$(error SHA is required. Usage: make deploy-staging SHA=abc123f)
endif
	@echo "Deploying $(ECR_REPO):$(SHA) to staging..."
	jq --arg img "$(ECR_REPO):$(SHA)" '.containerDefinitions[0].image = $$img' \
		ecs/staging-web-task-def.json > /tmp/staging-web-td.json
	WEB_ARN=$$(aws ecs register-task-definition \
		--cli-input-json file:///tmp/staging-web-td.json --region us-west-2 \
		--query 'taskDefinition.taskDefinitionArn' --output text) && \
	aws ecs update-service --cluster $(CLUSTER) --service ortobahn-web-staging \
		--task-definition "$$WEB_ARN" --force-new-deployment --region us-west-2
	jq --arg img "$(ECR_REPO):$(SHA)" '.containerDefinitions[0].image = $$img' \
		ecs/staging-scheduler-task-def.json > /tmp/staging-sched-td.json
	SCHED_ARN=$$(aws ecs register-task-definition \
		--cli-input-json file:///tmp/staging-sched-td.json --region us-west-2 \
		--query 'taskDefinition.taskDefinitionArn' --output text) && \
	aws ecs update-service --cluster $(CLUSTER) --service ortobahn-scheduler-staging \
		--task-definition "$$SCHED_ARN" --force-new-deployment --region us-west-2
	@echo "\nStaging services updating with $(SHA)."

# Promote a specific SHA to production
# Usage: make promote SHA=abc123f
promote:
ifndef SHA
	$(error SHA is required. Usage: make promote SHA=abc123f)
endif
	@echo "Promoting $(ECR_REPO):$(SHA) to production..."
	jq --arg img "$(ECR_REPO):$(SHA)" '.containerDefinitions[0].image = $$img' \
		ecs/web-task-def.json > /tmp/prod-web-td.json
	WEB_ARN=$$(aws ecs register-task-definition \
		--cli-input-json file:///tmp/prod-web-td.json --region us-west-2 \
		--query 'taskDefinition.taskDefinitionArn' --output text) && \
	aws ecs update-service --cluster $(CLUSTER) --service ortobahn-web-v2 \
		--task-definition "$$WEB_ARN" --force-new-deployment --region us-west-2
	jq --arg img "$(ECR_REPO):$(SHA)" '.containerDefinitions[0].image = $$img' \
		ecs/scheduler-task-def.json > /tmp/prod-sched-td.json
	SCHED_ARN=$$(aws ecs register-task-definition \
		--cli-input-json file:///tmp/prod-sched-td.json --region us-west-2 \
		--query 'taskDefinition.taskDefinitionArn' --output text) && \
	aws ecs update-service --cluster $(CLUSTER) --service ortobahn-scheduler-v2 \
		--task-definition "$$SCHED_ARN" --force-new-deployment --region us-west-2
	@echo "\nProduction services updating with $(SHA)."

# Rollback production to a previous SHA (image must exist in ECR)
# Usage: make rollback SHA=abc123f
rollback:
ifndef SHA
	$(error SHA is required. Usage: make rollback SHA=abc123f)
endif
	@echo "Rolling back production to $(SHA)..."
	$(MAKE) promote SHA=$(SHA)

# Show current deployment status for staging and production
deploy-status:
	@echo "=== Production ==="
	@aws ecs describe-services --cluster $(CLUSTER) \
		--services ortobahn-web-v2 ortobahn-scheduler-v2 --region us-west-2 \
		--query 'services[].{service:serviceName,status:status,desired:desiredCount,running:runningCount}' \
		--output table 2>/dev/null || echo "  (prod services not found)"
	@echo "\n=== Staging ==="
	@aws ecs describe-services --cluster $(CLUSTER) \
		--services ortobahn-web-staging ortobahn-scheduler-staging --region us-west-2 \
		--query 'services[].{service:serviceName,status:status,desired:desiredCount,running:runningCount}' \
		--output table 2>/dev/null || echo "  (staging services not yet created — see docs/STAGING_SETUP.md)"

# List recent ECR image tags (useful for finding a SHA to rollback to)
ecr-tags:
	@aws ecr describe-images --repository-name ortobahn --region us-west-2 \
		--query 'sort_by(imageDetails,&imagePushedAt)[-10:].{pushed:imagePushedAt,tags:imageTags}' \
		--output table

waf-setup:
	@echo "Creating WAF Web ACL for Ortobahn ALB..."
	aws wafv2 create-web-acl --region us-west-2 --cli-input-json file://ecs/waf-rules.json
	@echo "\nWAF created. Associate it with the ALB:"
	@echo "  aws wafv2 associate-web-acl --region us-west-2 --web-acl-arn <WAF_ARN> --resource-arn <ALB_ARN>"

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
