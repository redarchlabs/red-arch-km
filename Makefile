include .env
export

.PHONY: help dev dev-infra dev-go down lint type-check test test-unit test-integration format install-hooks clean \
        go-build go-test go-lint go-run-api go-clean go-mod-tidy seed-e2e

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Development ---

dev-infra: ## Start infrastructure only (PostgreSQL, Qdrant, Neo4j, Redis)
	docker compose --env-file .env -f docker/docker-compose.infra.yml up -d

dev: ## Start full development stack (source bind-mounts + uvicorn --reload)
	docker compose --env-file .env -f docker/docker-compose.dev.yml up -d --build

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

# Host port 5433 matches docker/docker-compose.infra.yml (postgres publishes
# 5433:5432 to avoid clashing with other local postgres stacks on 5432).
DATABASE_URL_LOCAL = postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:5433/$(POSTGRES_DB)

migrate: ## Run Alembic migrations
	cd services/api && DATABASE_URL=$(DATABASE_URL_LOCAL) uv run alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create MSG="add users table")
	cd services/api && DATABASE_URL=$(DATABASE_URL_LOCAL) uv run alembic revision --autogenerate -m "$(MSG)"

seed-e2e: ## Seed the local DB with E2E fixtures (org, roles, e2e_admin) — idempotent
	cd services/api && DATABASE_URL=$(DATABASE_URL_LOCAL) uv run python -m scripts.seed_e2e

# --- Setup ---

install-hooks: ## Install pre-commit hooks
	uv run pre-commit install

clean: ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# =============================================================================
# Go Services
# =============================================================================

GO_BIN_DIR := bin

go-build: ## Build all Go services
	@mkdir -p $(GO_BIN_DIR)
	go build -o $(GO_BIN_DIR)/api ./services/api-go/cmd/server
	go build -o $(GO_BIN_DIR)/brain-api ./services/brain-api-go/cmd/server
	go build -o $(GO_BIN_DIR)/worker ./services/worker-go/cmd/worker
	@echo "Built: $(GO_BIN_DIR)/api, $(GO_BIN_DIR)/brain-api, $(GO_BIN_DIR)/worker"

go-test: ## Run Go tests with coverage
	go test -v -race -cover ./packages/accessmask/... ./packages/shared/... ./services/api-go/... ./services/brain-api-go/... ./services/worker-go/...

go-test-cover: ## Run Go tests with detailed coverage report
	go test -v -race -coverprofile=coverage.out ./packages/accessmask/... ./packages/shared/... ./services/api-go/... ./services/brain-api-go/... ./services/worker-go/...
	go tool cover -html=coverage.out -o coverage.html
	@echo "Coverage report: coverage.html"

go-lint: ## Run Go linters (requires golangci-lint)
	@which golangci-lint > /dev/null || (echo "Install golangci-lint: https://golangci-lint.run/welcome/install/" && exit 1)
	golangci-lint run ./packages/accessmask/... ./packages/shared/... ./services/api-go/... ./services/brain-api-go/... ./services/worker-go/...

go-run-api: ## Run API server locally (requires DATABASE_URL)
	go run ./services/api-go/cmd/server

go-run-brain-api: ## Run Brain API server locally
	go run ./services/brain-api-go/cmd/server

go-run-worker: ## Run Worker locally
	go run ./services/worker-go/cmd/worker

go-mod-tidy: ## Tidy all Go module dependencies
	cd packages/accessmask && go mod tidy
	cd packages/shared && go mod tidy
	cd services/api-go && go mod tidy
	cd services/brain-api-go && go mod tidy
	cd services/worker-go && go mod tidy

go-clean: ## Remove Go build artifacts
	rm -rf $(GO_BIN_DIR)
	rm -f coverage.out coverage.html

dev-go: ## Start Go development stack (Go services + infrastructure)
	docker compose --env-file .env -f docker/docker-compose.go.yml up -d --build

# --- Database (Go) ---

DATABASE_URL_GO = postgres://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:5432/$(POSTGRES_DB)?sslmode=disable

go-migrate: ## Run Go migrations with golang-migrate
	@which migrate > /dev/null || (echo "Install golang-migrate: go install -tags 'postgres' github.com/golang-migrate/migrate/v4/cmd/migrate@latest" && exit 1)
	migrate -path services/api-go/migrations -database "$(DATABASE_URL_GO)" up

go-migrate-down: ## Rollback Go migrations
	migrate -path services/api-go/migrations -database "$(DATABASE_URL_GO)" down 1

go-migrate-create: ## Create a new Go migration (usage: make go-migrate-create NAME=add_users)
	migrate create -ext sql -dir services/api-go/migrations -seq $(NAME)

go-sqlc: ## Generate sqlc code
	@which sqlc > /dev/null || (echo "Install sqlc: go install github.com/sqlc-dev/sqlc/cmd/sqlc@latest" && exit 1)
	cd services/api-go && sqlc generate
