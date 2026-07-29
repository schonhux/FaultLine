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

demo:          ## Run the db-pool-exhaustion scenario end to end
	python -m agent.run --scenario scenarios/db-pool-exhaustion/scenario.yaml

eval:          ## Score all recorded runs
	python -m evaluation.scorers.run_all

test:          ## Run Rust and Python tests
	cargo test --workspace
	python -m pytest tests/

fmt:           ## Format Rust workspace
	cargo fmt --all

lint:          ## Check Rust formatting and compile the workspace
	cargo fmt --all -- --check
	cargo check --workspace

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'
