FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV HOME=/tmp \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY OAuth2.py add_user.py main.py spotify.py sql.py ./

RUN mkdir -p /app/data \
    && ln -s /app/data/users.db /app/users.db

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "add_user:app"]
