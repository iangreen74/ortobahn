.PHONY: install install-web test lint lint-fix typecheck run dry-run generate seed healthcheck validate dashboard web docker-build docker-up docker-down docker-logs deploy-landing clean

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

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
