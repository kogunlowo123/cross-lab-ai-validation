.PHONY: install format lint typecheck security contracts test integration check helm terraform audit-deps image run

CHART := deployments/helm/cross-lab-ai-validation

install:
	uv sync

format:
	uv run black src tests
	uv run ruff check src tests --fix

lint:
	uv run ruff check src tests
	uv run black --check src tests

typecheck:
	uv run mypy

security:
	uv run python -m bandit -q -r src -c pyproject.toml

contracts:
	uv run python -m cross_lab_ai_validation.contracts --check

test:
	uv run pytest

integration:
	uv run pytest tests/integration -m integration --no-cov

check: lint typecheck security contracts test

helm:
	helm lint $(CHART) -f $(CHART)/ci/test-values.yaml
	@if helm template clv $(CHART) > /dev/null 2>&1; then echo "chart rendered without required values"; exit 1; fi

terraform:
	terraform fmt -recursive -check deployments/terraform
	for c in aws azure gcp; do terraform -chdir=deployments/terraform/$$c init -backend=false -input=false && terraform -chdir=deployments/terraform/$$c validate; done

audit-deps:
	uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.audit.txt
	uvx pip-audit -r requirements.audit.txt --disable-pip --no-deps

image:
	docker build -t cross-lab-ai-validation:dev .

run:
	docker compose up --build
