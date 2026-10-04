FROM ghcr.io/astral-sh/uv:0.12.4@sha256:d0a6eca6c669dc7e9c51218707b8438a3d30402733d739dcc00adb3e213e8f5c AS uv
FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY webapp ./webapp
COPY auth ./auth
RUN groupadd --gid 10001 hypervaults && useradd --uid 10001 --gid 10001 --no-create-home hypervaults && mkdir .local && chown 10001:10001 .local
USER 10001:10001
EXPOSE 8088
CMD ["/app/.venv/bin/uvicorn", "webapp.main:app", "--host", "0.0.0.0", "--port", "8088", "--no-access-log", "--no-proxy-headers"]
