include .env
export

.PHONY: help dev dev-infra down lint type-check test test-unit test-integration format install-hooks clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Development ---

dev-infra: ## Start infrastructure only (PostgreSQL, Qdrant, Neo4j, Redis)
	docker compose --env-file .env -f docker/docker-compose.infra.yml up -d

dev: ## Start full development stack
	docker compose --env-file .env -f docker/docker-compose.yml up -d --build

down: ## Stop all containers
	docker compose --env-file .env -f docker/docker-compose.yml down
	docker compose --env-file .env -f docker/docker-compose.infra.yml down

logs: ## Tail logs for all services
	docker compose --env-file .env -f docker/docker-compose.yml logs -f

# --- Code Quality ---

lint: ## Run ruff linter
	uv run ruff check .

format: ## Auto-format code
	uv run ruff format .
	uv run ruff check --fix .

type-check: ## Run mypy type checker
	uv run mypy packages/ services/

# --- Testing ---

test: ## Run all tests
	uv run pytest -x --tb=short

test-unit: ## Run unit tests only
	uv run pytest -x --tb=short -m unit

test-integration: ## Run integration tests only
	uv run pytest -x --tb=short -m integration

test-cov: ## Run tests with coverage report
	uv run pytest --cov=packages --cov=services --cov-report=term-missing --cov-fail-under=80

# --- Database ---

DATABASE_URL_LOCAL = postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:5432/$(POSTGRES_DB)

migrate: ## Run Alembic migrations
	cd services/api && DATABASE_URL=$(DATABASE_URL_LOCAL) uv run alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create MSG="add users table")
	cd services/api && DATABASE_URL=$(DATABASE_URL_LOCAL) uv run alembic revision --autogenerate -m "$(MSG)"

# --- Setup ---

install-hooks: ## Install pre-commit hooks
	uv run pre-commit install

clean: ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
