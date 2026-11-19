# Host needs only Docker + make (ENG-01); secrets are generated in a throwaway container.
ENV_INIT = docker run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR)":/w -w /w python:3.14-slim python scripts/init_env.py

.PHONY: env up down reset migrate gen e2e perf perf-p soak

env:
	@$(ENV_INIT)

up: env
	docker compose up -d --build --wait

down:
	docker compose down

reset:
	uv run python scripts/reset_guard.py
	docker compose down -v && rm -f .env users.yaml redis/users.acl

migrate:
	docker compose run --rm migrate

gen:
	uv run python -m gs.common.interfaces.export
	cd ui && npx openapi-typescript ../interfaces/openapi.json -o src/server/schema.d.ts

e2e: e2e/node_modules
	cd e2e && npx playwright test

perf: e2e/node_modules
	uv run python -m perf.run_n

perf-p:
	uv run python -m perf.run_p

soak:
	uv run python -m perf.soak_count --hours 2

# ponytail: a file target, installed once per clone; delete the folder to reinstall
e2e/node_modules: e2e/package-lock.json
	cd e2e && npm ci && npx playwright install chromium
