.PHONY: help install dev test lint build web serve chat doctor logs docker clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install Python package (with full extras)
	pip install -e ".[full]"

dev: ## Install dev dependencies
	pip install -e ".[full,dev]"

test: ## Run the test suite
	pytest -q

lint: ## Lint Python code
	ruff check backend/

web: ## Build the web UI
	cd web && npm ci && npm run build

serve: ## Start the API server + UI
	colons serve

chat: ## Interactive CLI chat
	colons

doctor: ## Diagnose environment, config, provider and storage
	colons doctor

logs: ## Tail recent server logs
	colons logs --limit 50

docker: ## Build and run with Docker Compose
	docker compose up -d --build

clean: ## Remove build artifacts and local data
	rm -rf web/dist web/node_modules .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -f colons.db colons.db-wal colons.db-shm
