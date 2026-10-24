# Visión + asistente agéntico para una línea de pick & pack

Prueba técnica AI Engineer (Indra / Minsait). Este sistema sustituye la tarea manual de **identificar un producto y estimar su ángulo** antes de que un brazo robótico lo empaque. Además ofrece un **copiloto agéntico** para supervisores, calidad, mantenimiento e ingeniería de ML.

**Principio de diseño:** el lazo imagen → robot es **determinístico**: ML clásico más reglas, en el edge, sin LLM. El asistente es **agéntico**: LLM con tools en la nube, y toda acción de riesgo pasa por política en código y aprobación humana. El LLM propone; el código decide; el humano aprueba.

## Dónde está cada entregable

| # | Entregable | Reto | Ubicación |
|---|---|---|---|
| 2 | Notebook de experimentación reproducible | 2 | [`notebooks/01_experimentacion.ipynb`](notebooks/01_experimentacion.ipynb) (ejecutado, con salidas) |
| 3 | Reporte de resultados por protocolo, análisis de errores y model card | 2 | [`docs/report/vision_report.pdf`](docs/report/vision_report.pdf) · [`docs/model_card.md`](docs/model_card.md) · métricas crudas en [`docs/report/results.json`](docs/report/results.json) |
| 5 | Repositorio de código con historial de commits | 2, 3, 4 | Este repositorio (`git log`) |
| 7 | Catálogo de tools y contratos | 3 | [`docs/tool_catalog.md`](docs/tool_catalog.md) · JSON Schema en [`docs/tool_schemas/`](docs/tool_schemas/) · API en [`docs/CONTRACTS.md`](docs/CONTRACTS.md) |
| 8 | Threat model y controles | 3 | [`docs/threat_model.md`](docs/threat_model.md) |
| 10 | Servicio desplegado (local, un comando) | 4 | `make up` (sección siguiente) · [`docker-compose.yml`](docker-compose.yml) |
| 12 | Arquitecturas Azure (y AWS) + modelo de costos | 6 | [`docs/cloud/azure_architecture.md`](docs/cloud/azure_architecture.md) · [`docs/cloud/aws_architecture.md`](docs/cloud/aws_architecture.md) · [`docs/cloud/azure_vs_aws.md`](docs/cloud/azure_vs_aws.md) · [`docs/cloud/finops.md`](docs/cloud/finops.md) · hoja [`docs/cloud/finops_model.xlsx`](docs/cloud/finops_model.xlsx) |

**Plan de proyecto y equipo:** [`docs/plan_proyecto.md`](docs/plan_proyecto.md) cubre las fases con criterios go/no-go (piloto en sombra → canario → producción), el equipo y los roles, los riesgos y los KPIs.

**Documentos de soporte:**
- **Arquitectura y diagramas:** [`docs/architecture.md`](docs/architecture.md) y los diagramas en [`docs/diagrams/`](docs/diagrams/): la arquitectura, la secuencia imagen → robot, el flujo agéntico con aprobación humana y el alta de SKU.
- **Diseño del agente:** [`docs/agent_design.md`](docs/agent_design.md) explica el patrón, las alternativas descartadas, el estado, la memoria, el contexto y el aislamiento.
- **Evaluación del agente:** [`docs/report/agent_eval.md`](docs/report/agent_eval.md), con 30 o más casos, tokens y costo, fallos y mejoras. El runner está en [`eval/agent/`](eval/agent/).
- **Experimentos trazables:** MLflow en `mlflow.db`. Para verlos: `uv run mlflow ui --backend-store-uri sqlite:///mlflow.db`.
- **Validación independiente:** [`docs/validacion/`](docs/validacion/) guarda auditorías de cada reto hechas por un segundo modelo (Codex, de otro proveedor) contra el texto de la prueba, y sus correcciones están en el historial de commits.

## Ejecución local en un comando

Requisitos: macOS o Linux, [uv](https://docs.astral.sh/uv/) y una API key de LLM (la demo usa la capa gratuita de Google AI Studio).

```bash
cp .env.example .env      # completa GOOGLE_API_KEY
make up
```

`make up` hace todo esto:
1. Instala las dependencias.
2. Descarga COIL-100 y verifica su SHA-256.
3. Entrena el modelo si falta el artefacto.
4. Levanta el servicio de visión (http://localhost:8001/docs) y el agente con su UI de chat (http://localhost:8002/).
5. Espera a que los dos respondan `/health` con estado `ok`.

| Comando | Qué hace |
|---|---|
| `make test` | Pruebas unitarias e integración (visión + agente, sin red) |
| `make notebook` | Re-ejecuta el notebook de punta a punta |
| `make train` | Re-entrena y exporta `models/vision-1.0.1/` |
| `make eval` | Evaluación reproducible del agente con el LLM real |
| `make report` | Regenera el reporte PDF de visión |
| `make finops` | Regenera la hoja de costos |
| `make up-docker` | Alternativa con Docker Compose |

> **Docker:** el `Dockerfile` y el `docker-compose.yml` están incluidos, pero no se pudieron probar en la máquina de desarrollo porque no tenía Docker. La ruta probada es `make up`.

## Componentes

```
src/vision/            librería de visión (datos, protocolos, características, modelos, ángulo, rechazo, calidad, inferencia)
services/vision_api/   API de inferencia FastAPI, independiente del agente (:8001)
services/agent/        agente LangGraph + API + UI (:8002); plant/ = backends de planta simulados (SQLite)
configs/               umbrales, roles, políticas, precios de LLM (sin secretos)
models/vision-1.0.1/   artefacto versionado del modelo + metadata (hash de datos y de artefacto)
eval/agent/            casos de evaluación y runner
docs/                  reportes, arquitectura, cloud, seguridad, validación
```

## Qué es simulado

- **Las imágenes de planta:** se usa COIL-100, como indica la prueba.
- **Robot, PLC, cámara, métricas de turno, tickets y cola de revisión:** son backends en SQLite con datos sembrados de forma determinística. Las tools del agente los usan como si fueran sistemas reales.
- **Identidad de usuarios:** roles en `configs/roles.yaml`; en producción vendrían de Entra ID.
- **LLM:** se configura por entorno (`LLM_PROVIDER` y `LLM_MODEL`). La demo usa Gemini; el diseño cloud usa Azure OpenAI (o Bedrock en AWS), sin cambiar el código.

## Guía de defensa

[`docs/DEFENSA.md`](docs/DEFENSA.md) resume las decisiones, las cifras clave, las preguntas difíciles y el vocabulario. El guion de la demo en vivo está en [`docs/DEMO.md`](docs/DEMO.md).
