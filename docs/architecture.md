# Arquitectura del sistema

> **La idea en una frase:** lo que mueve el robot es **determinista y corre junto a la línea**; la IA generativa
> **asesora a las personas desde la nube** y nunca toca la planta sin pasar por una política en código y, si la
> acción es riesgosa, por la aprobación de un humano distinto de quien la pidió.

## 1. El problema y el objetivo

Hoy, en cada estación, un operador por turno (3 turnos × 8 h, 5 días) identifica cada objeto, lo gira a una
orientación de referencia y lo deja al brazo robótico. El objetivo es que una cámara + IA identifique el producto
y su ángulo y se lo envíe al robot, que compensa la rotación; que supervisores, calidad y mantenimiento tengan un
asistente conversacional; y que **el costo total sea menor que el de hoy, con un mecanismo seguro para lo que el
modelo no resuelva**.

## 2. Determinista vs agéntico, y dónde corre cada parte

| Parte | Tipo | Dónde corre en producción | Latencia | Costo | Riesgo si falla |
|---|---|---|---|---|---|
| Captura → calidad → clase → ángulo → decisión → comando | **Determinista** (ML clásico + umbrales) | **Borde**: PC industrial en la celda | ms (visión medida p50 18 / p95 25 ms; presupuesto total < 100 ms) | Fijo, CPU, sin costo por objeto | Alto (mueve un robot) → falla segura `HOLD` |
| Cola de revisión (alta del caso) | Determinista | Borde con buffer → nube | s | Bajo | Medio → se guarda localmente |
| Revisión humana | Humano | UI en nube (respaldo local) | min | Es el costo residual principal | Bajo |
| Asistente: preguntas, diagnóstico, SOP con citas, traspaso de turno | **Agéntico** (LLM + 16 tools: 8 de lectura + 8 de escritura) | **Nube** | s | Por token (~USD 20/mes) | Bajo para la línea: si cae, la producción sigue |
| Política RBAC, aprobaciones, ejecutor, auditoría | **Determinista** | Nube | ms | Bajo | Alto → por eso no es un prompt |
| Alta de SKU, reentrenamiento, promoción | Determinista + aprobación humana | Nube → despliegue por anillos al borde | min–h | Esporádico | Alto → staging, canario, rollback |

**Por qué así:**
- **Latencia:** un viaje de ida y vuelta a la nube (60–150 ms + jitter) no aporta nada y añade un punto de falla. El
  pipeline clásico completo (`vision-1.0.2`) tarda p50 13,6 ms / p95 22,6 ms por imagen en CPU de portátil (medido, `docs/report/results.json`).
- **Costo:** inferencia local = costo fijo de un PC; en la nube serían millones de llamadas al mes.
- **Riesgo:** un LLM puede equivocarse o ser manipulado por texto (inyección de prompt). Por eso el LLM **propone**
  y el código **dispone**: el robot nunca recibe un ángulo dudoso (si la decisión no es `ACT`, el comando es `HOLD`)
  y ninguna acción de riesgo se ejecuta sin aprobación.

## 3. Componentes (demo local)

| Componente | Ruta | Puerto | Rol |
|---|---|---|---|
| Librería de visión | `src/vision/` | — | Datos, características, modelos, ángulo, rechazo, calidad |
| API de visión | `services/vision_api/` | 8001 | Inferencia, umbrales, staging/promoción de SKU |
| Agente | `services/agent/` | 8002 | LangGraph + API + UI de chat |
| Backends simulados de planta | `services/agent/plant/` | — | SQLite: métricas, cámara, robot, tickets, cola, aprobaciones, auditoría |
| SOP | `docs/sop/` | — | Procedimientos que el agente cita |
| Evaluación | `eval/agent/` | — | ≥ 30 casos reproducibles |

Interfaces exactas en [`CONTRACTS.md`](CONTRACTS.md).

## 4. Diagramas

| Diagrama | Qué muestra |
|---|---|
| [Arquitectura local](diagrams/01_arquitectura_local.md) | Componentes de la demo y cómo se conectan |
| [Secuencia imagen → robot](diagrams/02_secuencia_imagen_robot.md) | Ciclo en tiempo real con ACT / REVIEW / REJECT, `PICK rotate_deg` o `HOLD`, y presupuesto de latencia |
| [Flujo agéntico con aprobación humana](diagrams/03_secuencia_aprobacion_humana.md) | Diagnóstico → SOP → propuesta → política → `interrupt` → aprobación (aprobador ≠ solicitante) → ejecutor → auditoría |
| [Alta de SKU nuevo](diagrams/04_secuencia_onboarding_sku.md) | Staging → métricas → aprobación → promoción → canario → rollback |
| [Azure](cloud/azure_architecture.md#2-diagrama) · [AWS](cloud/aws_architecture.md#2-diagrama) | Arquitecturas de producción |

## 5. De la demo a producción

| Demo | Producción (Azure) | Producción (AWS) |
|---|---|---|
| API de visión en el portátil | Módulo IoT Edge en PC industrial + adaptador OPC UA al PLC | Componente Greengrass |
| Gemini | Azure OpenAI (gpt-4.1-mini por defecto, gpt-4.1 para diagnósticos) | Bedrock (Claude Haiku 4.5 / Sonnet 4.5) |
| SQLite | PostgreSQL Flexible (checkpoints LangGraph, aprobaciones, auditoría) | Aurora PostgreSQL Serverless v2 |
| SOP en disco | Azure AI Search | Bedrock Knowledge Bases (pgvector) |
| Imágenes en `data/runtime` | Blob con ciclo de vida, anonimizadas en el borde | S3 con lifecycle |
| `ADMIN_TOKEN` en `.env` | Entra ID + identidades administradas + Key Vault | IAM Identity Center + roles IAM + Secrets Manager |
| `models/vision-x.y.z` | Azure ML Registry + imagen en ACR + despliegue por anillos | SageMaker Model Registry + ECR + despliegue Greengrass |

Detalle: [Azure](cloud/azure_architecture.md) · [AWS](cloud/aws_architecture.md) ·
[Comparación y recomendación](cloud/azure_vs_aws.md) · [FinOps](cloud/finops.md) ·
[Modelo de costos (xlsx)](cloud/finops_model.xlsx).

## 6. Caso de negocio: un rango, no un número (supuestos editables en el xlsx)

El caso de negocio se presenta como **rango** entre dos coberturas **medidas** de `vision-1.0.1` (la `vision-1.0.2` desplegada da 90,3% y 73,6%, prácticamente iguales); ninguna de las dos es
todavía el número real de planta.

| Escenario | % automatizado | Colombia (Azure, 1 estación) | España (Azure, 1 estación) |
|---|---|---|---|
| Pesimista (desconocidos mezclados, medido) | 73,7% | −1.290 USD/mes: no se paga | −3.778 USD/mes: no se paga |
| Equilibrio | 85,6% CO / 80,8% ES | 0 | 0 |
| Base (referencias densas, medido) | 90,3% | +505 USD/mes (20%); payback ≈ 100 meses, ≈ 27 con 5 estaciones | +5.088 USD/mes (41%); payback ≈ 9,9 meses, ≈ 4,7 con 5 estaciones |

- **Con objetos desconocidos mezclados, el proyecto no se paga** en ningún país: la cola de revisión se come el ahorro.
  El equilibrio está en **≈ 80-86%** de automatización segura.
- Por eso la palanca crítica no es la nube sino **el alta de SKU** (mantener el catálogo al día) y **el rechazo correcto
  de desconocidos** (que no lleguen a la línea o se resuelvan rápido).
- **El número real debe medirse en un piloto en modo sombra** (el sistema decide en paralelo al operador, sin mover el
  robot) antes de comprometer la inversión.
- La nube cuesta **≈ USD 640/mes por planta en East US 2 y ≈ 740 en Spain Central** (Azure, 1 estación) ≈ **USD 0,002 por
  objeto**: no es lo que decide el caso.

## 7. Cómo ejecutarlo

```bash
cp .env.example .env      # completar GOOGLE_API_KEY
make up                   # uv sync + dataset verificado + modelo + visión :8001 + agente :8002
# o, con Docker instalado:
make train && make up-docker
```

`make help` lista el resto (`test`, `eval`, `notebook`, `report`, `finops`).
Los archivos Docker (`Dockerfile`, `docker-compose.yml`) **no se probaron en la máquina de desarrollo** (sin Docker);
la ruta sin Docker (`scripts/run_local.sh`) sí.
