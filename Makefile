# Front door for the common tasks. Everything real lives in scripts/ and compose.yaml.
.PHONY: help up down restart logs ps token verify update chrome twin test lint frontend

help: ## List the tasks
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort

up: ## First start and every later start: create .env, build, start, print the URL
	@scripts/setup.sh

down: ## Stop AXIOM (your data is kept in Docker volumes)
	docker compose down

restart: ## Restart the app container
	docker compose restart app

logs: ## Follow the app logs
	docker compose logs -f --tail=100 app

ps: ## Show container status and health
	docker compose ps

token: ## Print the dashboard access token (a secret: keep it private)
	@docker compose exec app jobpilot token

verify: ## Run two synthetic browser applications against the built-in demo form
	docker compose exec app jobpilot verify

update: ## Pull the latest code and rebuild. Pause applications first: the app restarts
	git pull --ff-only
	@scripts/setup.sh

chrome: ## Start the worker Chrome on this computer (port 9224)
	@scripts/chrome-worker.sh

twin: ## Start the twin Chrome that mints hCaptcha tokens (port 9225)
	@scripts/chrome-twin.sh

test: ## Fast tests (no real browsers)
	uv run pytest -q -m "not browser"

lint: ## Lint the Python code
	uv run ruff check jobpilot tests

frontend: ## Type-check and build the dashboard into jobpilot/static for local development
	cd frontend && npm ci && npm run build
