DOCS_URL := http://localhost:8000/docs
HEALTH_URL := http://localhost:8000/health

.PHONY: up down logs dev test

## Start via Docker (build, run detached, wait for health, open docs)
up:
	docker compose up --build -d
	@printf '⏳  Waiting for service'
	@until curl -sf $(HEALTH_URL) >/dev/null 2>&1; do printf '.'; sleep 1; done
	@echo ' ✓'
	open $(DOCS_URL)
	docker compose logs -f

## Stop containers
down:
	docker compose down

## Tail logs without restarting
logs:
	docker compose logs -f

## Run locally without Docker (requires .venv)
dev:
	@.venv/bin/uvicorn app:app --reload --port 8000 &
	@printf '⏳  Waiting for service'
	@until curl -sf $(HEALTH_URL) >/dev/null 2>&1; do printf '.'; sleep 1; done
	@echo ' ✓'
	open $(DOCS_URL)
	@wait

## Run tests
test:
	.venv/bin/pytest tests/ -v
