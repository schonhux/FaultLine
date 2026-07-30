.PHONY: up down build logs smoke demo eval test fmt lint help

up:            ## Start ShopGrid + telemetry stack
	docker compose up -d --build

down:          ## Stop everything and remove volumes (full reset)
	docker compose down -v

build:         ## Build all service images
	docker compose build

logs:          ## Follow local Compose logs
	docker compose logs -f --tail=120

smoke:         ## Hit the gateway once through the public API
	curl -fsS -H 'x-shopgrid-api-key: dev-shopgrid-key' http://localhost:8080/products/1

run-scenario:  ## Run a scenario end to end: make run-scenario SCENARIO=db-pool-exhaustion SEED=42
	docker compose run --rm controlplane run $(SCENARIO) --seed $(SEED)

run-agent:     ## Run the Layer 3 agent against an alert: make run-agent ALERT_NAME=... ALERT_CONDITION="..."
	docker compose run --rm agent --alert-name "$(ALERT_NAME)" --alert-condition "$(ALERT_CONDITION)"

demo:          ## Run db-pool-exhaustion, then print the alert it fired so you can feed it to run-agent
	docker compose run --rm controlplane run db-pool-exhaustion --seed 42
	@echo "--- alert fired by the run above (name | condition) ---"
	@docker compose exec -T postgres psql -U shopgrid -d shopgrid -t -A -F' | ' \
		-c "SELECT name, condition FROM alerts ORDER BY fired_at DESC LIMIT 1;"
	@echo "--- now run: make run-agent ALERT_NAME=\"<name above>\" ALERT_CONDITION=\"<condition above>\" ---"

eval:          ## Run + score scenarios end to end: make eval SCENARIOS=redis-latency SEEDS=42,7
	python3 -m pip install -q -r evaluation/requirements.txt
	python3 evaluation/harness.py --seeds "$(or $(SEEDS),42)" $(if $(SCENARIOS),--scenarios "$(SCENARIOS)")

test:          ## Run Rust and Python tests
	cargo test --workspace
	cd mcp/telemetry-server && python3 -m pip install -q -r requirements-dev.txt && python3 -m pytest tests/ -v
	cd agent && python3 -m pip install -q -r requirements-dev.txt && python3 -m pytest tests/ -v
	python3 -m pip install -q -r evaluation/requirements-dev.txt && python3 -m pytest evaluation/tests/ -v

fmt:           ## Format Rust workspace
	cargo fmt --all

lint:          ## Check Rust formatting and compile the workspace
	cargo fmt --all -- --check
	cargo check --workspace

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'
