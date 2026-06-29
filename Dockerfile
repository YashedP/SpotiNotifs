FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV HOME=/tmp \
    PATH=/app/.venv/bin:$PATH \
    PORT=80 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY OAuth2.py add_user.py main.py logging_config.py spotify.py sql.py ./

RUN mkdir -p /app/data \
    && ln -s /app/data/users.db /app/users.db \
    && chown -R 1000:1000 /app/data

CMD ["gunicorn", "--bind", "0.0.0.0:80", "add_user:app"]
