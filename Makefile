# Host needs only Docker + make (ENG-01); secrets are generated in a throwaway container.
ENV_INIT = docker run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR)":/w -w /w python:3.14-slim python scripts/init_env.py

.PHONY: env up down reset migrate gen e2e

env:
	@$(ENV_INIT)

up: env
	docker compose up -d --build --wait

down:
	docker compose down

reset:
	docker compose down -v && rm -f .env users.yaml redis/users.acl

migrate:
	docker compose run --rm migrate

gen:
	uv run python -m gs.common.interfaces.export
	cd ui && npx openapi-typescript ../interfaces/openapi.json -o src/api/schema.d.ts

e2e:
	cd e2e && npx playwright test
