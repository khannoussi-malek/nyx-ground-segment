FROM node:24-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /bin/
# the base image ships the stdlib without .pyc; USER gs can't write them, so every start compiled
# ~135 stdlib modules again (UI-07 restart budget)
RUN python -m compileall -q -j 0 "$(python -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')"
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
# not editable: gs lands in site-packages and is byte-compiled here; USER gs can't write __pycache__ (UI-07)
RUN uv sync --frozen --no-dev --no-editable
COPY --from=ui /ui/dist /app/ui/dist
RUN useradd --system --uid 10001 gs
USER gs
