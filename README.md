# Visión + copiloto agéntico para una línea de pick & pack

Una cámara mira cada producto que llega a la línea. El sistema dice **qué producto es** y **cuántos grados está girado**, y se lo pasa al brazo robótico para que lo agarre bien. Así se reemplaza la tarea manual de identificar y alinear cada objeto.

Encima hay un **copiloto conversacional** para supervisores, calidad, mantenimiento e ingeniería de ML. Inspecciona fotos, explica por qué subieron los rechazos, busca en los manuales, crea tickets y prepara operaciones de riesgo, que solo se ejecutan si **otra persona** las aprueba.

**La idea central:** el lazo foto → robot no usa IA generativa. Es un modelo clásico más reglas, rápido (~14 ms), sin internet y auditable. El LLM solo conversa con humanos. El LLM propone; el código decide; el humano aprueba.

---

## Cómo correrlo (Windows y macOS)

Necesitas unos 10 minutos la primera vez. Después arranca en segundos.

> **Importante:** no pongas el proyecto en una carpeta sincronizada con **iCloud** (Documentos/Escritorio en Mac) ni con **OneDrive** (Documentos/Escritorio en Windows). La sincronización vuelve muy lento el arranque. Usa por ejemplo `C:\proyectos` o tu carpeta de usuario.

### 1. Instala `uv` (gestor de Python)

`uv` instala Python y todas las librerías por ti.

**macOS** (Terminal):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows** (PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Cierra y vuelve a abrir la terminal después de instalarlo.

### 2. Descarga el proyecto

```bash
git clone https://github.com/Looocust/planta-vision-ia.git
cd planta-vision-ia
```

Si no tienes `git`, en GitHub usa **Code → Download ZIP**, descomprímelo y entra a la carpeta.

### 3. Pon tu clave de Gemini (gratis)

1. Crea una clave en https://aistudio.google.com/apikey (solo pide tu cuenta de Google, sin tarjeta).
2. Copia el archivo de ejemplo:
   - **macOS:** `cp .env.example .env`
   - **Windows:** `copy .env.example .env`
3. Abre `.env` con cualquier editor y pega tu clave en la línea `GOOGLE_API_KEY=`.

Sin clave, la parte de visión funciona igual; solo el chat del copiloto la necesita.

### 4. Arranca todo

Mismo comando en Windows y macOS:

```bash
uv run python scripts/start.py
```

La primera vez descarga el dataset (~125 MB) y verifica que no esté corrupto. Cuando veas **"Sistema arriba"**, abre:

| Qué | Dónde |
|---|---|
| Copiloto (chat) | http://localhost:8002 |
| API de visión, para probar fotos con clics | http://localhost:8001/docs |

Para detener todo: **Ctrl+C** en la terminal.

### 5. Pruébalo en 2 minutos

1. En http://localhost:8001/docs abre `POST /v1/predict`, pulsa **Try it out** y sube fotos de la carpeta `demo/imagenes/`. Cada una muestra una decisión distinta: el robot actúa, va a revisión humana o se rechaza.
2. En http://localhost:8002 el usuario ya entra solo. Pregunta: *"¿Por qué subieron los rechazos a las 3 a. m.?"*
3. Escribe *"Pausa la línea P1-L1 para recalibrar la cámara"*. La operación queda **pendiente**. Cambia el usuario a `u_sup2_p1` y apruébala.

### Otros comandos útiles

| Para | Comando |
|---|---|
| Correr las pruebas automáticas | `uv run pytest -q` |
| Re-ejecutar el notebook de experimentos | `uv run jupyter nbconvert --to notebook --execute --inplace notebooks/01_experimentacion.ipynb` |
| Correr la evaluación del copiloto (usa la clave de Gemini) | `uv run python eval/agent/run_eval.py` |
| Ver los experimentos registrados | `uv run mlflow ui --backend-store-uri sqlite:///mlflow.db` |
| Con Docker (alternativa) | `docker compose up --build` |

En macOS también funcionan los atajos `make up`, `make test` y `make eval`.

---

## Dónde está cada entregable

| Entregable | Archivo |
|---|---|
| Notebook de experimentación (de punta a punta, ya ejecutado) | [`notebooks/01_experimentacion.ipynb`](notebooks/01_experimentacion.ipynb) |
| Reporte de resultados por protocolo, errores y model card (PDF) | [`docs/report/vision_report.pdf`](docs/report/vision_report.pdf) · [`docs/model_card.md`](docs/model_card.md) |
| Código con historial de commits | Este repositorio |
| Catálogo de herramientas del copiloto y sus contratos | [`docs/tool_catalog.md`](docs/tool_catalog.md) · esquemas en [`docs/tool_schemas/`](docs/tool_schemas/) · APIs en [`docs/CONTRACTS.md`](docs/CONTRACTS.md) |
| Threat model y controles de seguridad | [`docs/threat_model.md`](docs/threat_model.md) |
| Servicio desplegado (local, un comando) | Sección "Cómo correrlo" |
| Arquitectura en Azure (y AWS) + modelo de costos | [`docs/cloud/azure_architecture.md`](docs/cloud/azure_architecture.md) · [`docs/cloud/aws_architecture.md`](docs/cloud/aws_architecture.md) · [`docs/cloud/finops_model.xlsx`](docs/cloud/finops_model.xlsx) |
| Plan de proyecto y equipo | [`docs/plan_proyecto.md`](docs/plan_proyecto.md) |

**Para entender el diseño:**
- [`docs/architecture.md`](docs/architecture.md): arquitectura y diagramas (foto → robot, flujo con aprobación humana).
- [`docs/agent_design.md`](docs/agent_design.md): cómo funciona el copiloto y por qué así.
- [`docs/report/agent_eval.md`](docs/report/agent_eval.md): evaluación del copiloto (43 casos + 11 de control, costo y fallos).

## Resultados principales

| | Resultado |
|---|---|
| Objetos que el robot maneja solo (sin humano) | 90,3% |
| Error cuando el robot actúa | 0,09% |
| Tiempo por foto | ~14 ms en CPU |
| Copiloto: éxito en casos de prueba | 88,4% (43 casos) · 11/11 en casos nuevos |
| Copiloto: escenarios de ataque y falla superados | 12/12 |
| Costo de nube por estación (Azure) | ~USD 640/mes |

Cuando el sistema no está seguro (objeto desconocido, foto mala, objeto simétrico), **no le da una orden al robot**: manda el caso a revisión humana.

## Cómo está organizado el código

```
src/vision/            modelo de visión (características, clasificador, ángulo, rechazo)
services/vision_api/   API de visión, independiente del copiloto (puerto 8001)
services/agent/        copiloto con LangGraph + API + página de chat (puerto 8002)
configs/               umbrales, roles y permisos (sin secretos)
models/vision-1.0.2/   modelo entrenado y versionado
eval/agent/            casos de prueba del copiloto
scripts/start.py       arranque en un comando
docs/                  reportes, arquitectura, nube y seguridad
```

**Qué es simulado:** las fotos de planta (se usa el dataset COIL-100, como pide el enunciado), y el robot, la cámara, las métricas y los tickets, que viven en una base SQLite con datos de ejemplo. Los usuarios son de demo; en producción vendrían de Entra ID.

## Limitaciones conocidas

Lo que todavía no está resuelto, declarado en vez de ocultarse:

- **Desconocidos casi idénticos:** la segunda compuerta de `vision-1.0.2` se diseñó después de ver un fallo en el test, así que su resultado (0,97% de desconocidos aceptados) es optimista. La estimación independiente es 6,4%. Se confirma en el piloto en planta.
- **Configuración desplegada:** se eligió con validaciones que se cruzan con los tests de otros protocolos. Las cifras independientes son las de la selección anidada (ver el reporte PDF).
- **Productos nuevos:** el alta de un SKU no mide si empeora el reconocimiento de los productos existentes, y los SKU nuevos aún no se aceptan en la revisión humana ni en el ángulo manual.
- **Copiloto:** persisten 3 fallos de calidad de respuesta (ninguno de seguridad). El límite de tiempo por turno no corta una llamada al LLM ya en curso.
- **Probado en:** macOS. Windows usa el mismo comando y solo librería estándar de Python, pero no se probó en un equipo Windows. Docker no se probó.
