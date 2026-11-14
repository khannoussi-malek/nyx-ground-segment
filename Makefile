# Host needs only Docker + make (ENG-01); secrets are generated in a throwaway container.
ENV_INIT = docker run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR)":/w -w /w python:3.14-slim python scripts/init_env.py

.PHONY: env up down reset migrate

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
