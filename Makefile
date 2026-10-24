# Atajos del proyecto. `make help` lista los objetivos.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV        ?= uv
NOTEBOOK  ?= notebooks/01_experimentacion.ipynb
EVAL_RUNNER  := $(firstword $(wildcard eval/agent/run_eval.py eval/agent/runner.py eval/agent/run.py))
REPORT_BUILD := scripts/build_vision_report.py

.PHONY: help setup data train notebook test eval up up-docker down report finops

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Instala dependencias (uv sync) y crea .env desde .env.example si no existe
	$(UV) sync --frozen
	@test -f .env || (cp .env.example .env && echo "Creado .env: completa GOOGLE_API_KEY")

data: ## Descarga COIL-100 y verifica su SHA-256 (idempotente)
	scripts/run_local.sh --data-only

train: data ## Entrena y exporta el modelo de visión en models/
	$(UV) run python scripts/train_vision.py

notebook: ## Ejecuta el notebook de punta a punta (in-place)
	$(UV) run jupyter nbconvert --to notebook --execute --inplace $(NOTEBOOK)

test: ## Corre la suite de pruebas
	$(UV) run pytest -q

eval: ## Corre la evaluación reproducible del agente
	@test -n "$(EVAL_RUNNER)" || (echo "No se encontró runner en eval/agent/" && exit 1)
	$(UV) run python $(EVAL_RUNNER)

up: ## Levanta todo en local sin Docker (datos + modelo + visión :8001 + agente :8002)
	scripts/run_local.sh

up-docker: ## Levanta ambos servicios con docker compose (requiere modelo ya entrenado)
	@test -f .env || cp .env.example .env
	@test -s models/vision-1.0.1/metadata.json -a -s models/vision-1.0.1/model.joblib || (echo "Artefacto incompleto: make train" && exit 1)
	docker compose up --build -d
	@echo "Visión: http://localhost:8001/docs  ·  Agente: http://localhost:8002/"

down: ## Detiene los servicios de docker compose
	docker compose down

report: ## Genera el reporte final
	@test -n "$(REPORT_BUILD)" || (echo "No se encontró script de reporte" && exit 1)
	$(UV) run python $(REPORT_BUILD)

finops: ## Regenera docs/cloud/finops_model.xlsx (fórmulas vivas)
	$(UV) run python scripts/build_finops.py
