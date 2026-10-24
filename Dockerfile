# Imagen única para ambos servicios (visión y agente). El comando se elige en
# docker-compose.yml. Una sola imagen = un solo uv.lock, un solo escaneo de
# vulnerabilidades y la garantía de que ambos servicios usan las mismas versiones.
#
# NOTA: no probado en esta máquina (Docker no instalado). Ver docs/architecture.md.
# Tamaño: pyproject incluye torch/torchvision; en Linux la rueda por defecto de PyPI trae CUDA
# (varios GB). Para producción en el borde (CPU) conviene un grupo de dependencias mínimo para
# los servicios o el índice CPU de PyTorch (https://download.pytorch.org/whl/cpu).

# ---------- etapa 1: dependencias ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Primero solo los manifiestos: la capa de dependencias se cachea mientras
# pyproject.toml / uv.lock no cambien.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Código de la aplicación
COPY src ./src
COPY services ./services
COPY configs ./configs
COPY scripts ./scripts
COPY docs/sop ./docs/sop
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- etapa 2: runtime ----------
FROM python:3.12-slim AS runtime

# libgl/libglib: requeridas por opencv-headless en algunas ruedas; curl para healthcheck
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_DIR=models/vision-1.0.2 \
    DATA_DIR=data/raw/coil-100 \
    AGENT_DB_PATH=data/runtime/agent.db

# Directorios montables (modelos y datos de ejecución llegan por volumen)
RUN mkdir -p /app/models /app/data/raw/coil-100 /app/data/runtime && chown -R app:app /app/models /app/data

USER app

EXPOSE 8001 8002

# Por defecto arranca la API de visión; compose sobrescribe el comando para el agente.
CMD ["uvicorn", "services.vision_api.app:app", "--host", "0.0.0.0", "--port", "8001"]
