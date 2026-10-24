## 1) Veredicto: **CAMBIOS REQUERIDOS**

La entrega es sustancial, pero persisten defectos en el alta de SKU, contaminación del desarrollo experimental por observación del test y verificadores del agente que aceptan respuestas incorrectas.

Reverifiqué correcciones de los informes 01–06: `DATA_DIR`, readiness, baseline D, NaN, `top_k`, hashes/snapshots, autenticación, confirmación humana, aislamiento y denominadores de evaluación. El plan de proyecto ya existe. **No todos los hallazgos pueden cerrarse.**

**Tests:** ejecuté el comando solicitado; quedó detenido descubriendo plugins y lo interrumpí. Con `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, aborta por `FileNotFoundError: No usable temporary directory found`, incluyendo `data/runtime/pytest_tmp`, inexistente y no creable en este entorno de solo lectura. **No certifico los 86 tests anteriores.** No modifiqué archivos.

## 2) Matriz de cumplimiento

Las referencias son relativas a la raíz del repositorio. Los entregables de §9 se vinculan con sus requisitos técnicos.

| Requisito | Estado | Evidencia |
|---|:---:|---|
| Ficha: plan de proyecto y dirección de equipo | ✅ | `docs/plan_proyecto.md:12`, `:19`, `:43`: roles, fases, decisiones y criterios go/no-go. |
| §3: clase, ángulo, confianza y decisión para robot | ⚠️ | `src/vision/inference.py:99`; restricciones degradadas al incorporar SKU, descritas abajo. |
| §3: propósito económico, throughput y excepciones | ✅ | `docs/plan_proyecto.md:45`, `:48`; resultados de planta correctamente pendientes de piloto. |
| §3: separación determinística/agéntica por latencia, costo y riesgo | ✅ | `docs/architecture.md:15`. |
| §4: caracterización de COIL-100 y diferencias con planta | ✅ | `scripts/build_vision_notebook.py:79`; `docs/model_card.md:65`. |
| §4: A — interpolación | ✅ | `src/vision/data.py:133`: 3.600 train / 3.600 test. |
| §4: B — sector ciego de 60° | ✅ | `src/vision/data.py:133`: 6.000 / 1.200. |
| §4: C — una vista cada 30° | ✅ | `src/vision/data.py:133`: 1.200 / 6.000. |
| §4: D — 90 objetos completos / 10 excluidos | ✅ | `src/vision/data.py:150`: `D_strict`, 6.480 / 720; variante mixta identificada aparte. |
| §4: validación exclusivamente dentro del entrenamiento | ✅ | `src/vision/data.py:162`; intersección validación/test vacía en cada protocolo. |
| §4: ningún ajuste utilizando test | ❌ | Segunda compuerta publicada por mejora observada en test: `scripts/build_vision_report.py:411`; heredada por evaluación anidada: `src/vision/recipe.py:346`. |
| §5: ML clásico productivo; clase/confianza, ángulo/incertidumbre y top-k | ✅ | `src/vision/models.py:42`, `:89`; `src/vision/inference.py:104`, `:109`. |
| §5.1: ≥3 clasificadores con validación | ✅ | Cuatro familias: `src/vision/recipe.py:326`. |
| §5.1: orientación y métrica circular justificadas | ✅ | `src/vision/angle.py:7`; score heurístico explicado en `docs/model_card.md:12`. |
| §5.1: casos dudosos, rechazo y confianza para automatización | ⚠️ | HOLD implementado; D mixto anidado alcanza **2,56% error entre ACT** y **9,86% desconocidos→ACT**: `docs/report/results.json:6092`, `:6114`. |
| §5.1: baseline y referencia moderna con costo/latencia | ✅ | `src/vision/recipe.py:237`, `:317`; `docs/report/results.json:6291`. |
| §5.1: traducir ±10° a aceptación | ✅ | Éxito conjunto y política de riesgo: `src/vision/reject.py:115`; `docs/model_card.md:62`. |
| §5.2 / §9-2: notebook completo y ejecutado | ✅ | 18/18 celdas ejecutadas, sin errores guardados; adquisición/exportación: `scripts/build_vision_notebook.py:67`, `:489`. |
| §5.2: trazabilidad de parámetros, datos, métricas y artefactos | ✅ | `scripts/train_vision.py:138`, `:180`, `:194`; hashes verificados y snapshot idéntico al código actual. |
| §5.2 / §9-3: PDF por protocolo, errores y model card | ⚠️ | Contenidos presentes; contradicciones y marcador literal en PDF: `scripts/build_vision_report.py:323`, `:378`, `:475`. |
| §6: utiliza visión como herramienta independiente | ✅ | `services/agent/tools.py:126`; `services/agent/vision_client.py:113`. |
| §6.1: inspección múltiple, evidencia y acción ante baja confianza | ⚠️ | Inspección/UI completas; sigue fallando el encolado delegado: `services/agent/static/index.html:82`; `docs/report/agent_eval.md:183`. |
| §6.1: revisión humana → etiqueta/ángulo → dataset | ⚠️ | Confirmación transaccional implementada, pero excluye SKU nuevos: `services/agent/review.py:31`; `services/agent/app.py:92`. |
| §6.1: consulta operativa correlacionada | ⚠️ | Persiste omisión de cámara: `docs/report/agent_eval.md:184`. |
| §6.1: diagnóstico, SOP con citas y tickets | ⚠️ | Persiste cita de sección incorrecta: `docs/report/agent_eval.md:186`. |
| §6.1: alta controlada de SKU, desempeño y promoción | ⚠️ | Sin regresión de clases existentes y pérdida de flags: `services/agent/tools.py:613`; `src/vision/models.py:66`, `:122`. |
| §6.1: operaciones riesgosas con permisos y aprobación explícita | ✅ | Token y aprobador distinto: `services/agent/app.py:165`; `services/agent/approvals.py:84`. Identidad demo declarada. |
| §6.1: entrega de turno | ✅ | `services/agent/tools.py:750`: métricas, incidentes, tickets y pendientes. |
| §6.2: patrón, alternativas y justificación | ✅ | Un agente; tres alternativas comparadas: `docs/agent_design.md:12`, `:34`. |
| §6.2: decisiones determinísticas fuera del LLM | ✅ | `services/agent/graph.py:234`; `services/agent/approvals.py:29`. |
| §6.2: terminación y límites | ⚠️ | Deadline comprobado antes del LLM, sin cubrir invocación/reintentos: `services/agent/graph.py:188`; `services/agent/llm.py:56`. |
| §6.3 / §9-7: ≥10 tools distintas y contratos completos | ⚠️ | **16 tools, 8 lectura/8 escritura**; catálogo completo, pero garantías de auditoría/atomicidad parciales: `services/agent/tools.py:818`; `docs/tool_catalog.md:22`; `docs/threat_model.md:58`. |
| §6.4: estado, historial, memoria y conocimiento | ✅ | `docs/agent_design.md:80`. |
| §6.4: persistencia, TTL, identificadores e imágenes | ✅ | `docs/agent_design.md:82`, `:88`; purga de checkpoints identificada como propuesta. |
| §6.4: crecimiento de contexto, aislamiento y sensibles | ✅ | `services/agent/graph.py:155`; `services/agent/service.py:97`; limitaciones declaradas en `docs/threat_model.md:60`. |
| §6.5: ≥8 adversos, incluidos los cinco obligatorios | ✅ | 12 escenarios; guionados diferenciados e integración de visión real: `docs/report/agent_eval.md:58`, `:191`, `:210`. |
| §6.6: ≥30 casos reproducibles con criterio objetivo | ⚠️ | 43 + 11 holdout; algunos verificadores aceptan cifras fabricadas: `eval/agent/cases.yaml:194`; `eval/agent/run_eval.py:261`. |
| §6.6: selección, argumentos, tokens y costo por ejecución | ✅ | Cobertura y consumo separados: `docs/report/agent_eval.md:13`, `:103`; coinciden con JSON. |
| §6.6: resultados, fallos y ≥3 mejoras priorizadas | ✅ | `docs/report/agent_eval.md:153`, `:177`. |
| §7 / §9-10: despliegue local de un comando | ⚠️ | Implementado en `Makefile:35` y `scripts/run_local.sh:139`; ejecución integral no certificada aquí. Docker expresamente sin probar: `README.md:54`. |
| §7: servicios independientes, organización y configuración | ✅ | `docs/CONTRACTS.md:10`; `docker-compose.yml:22`, `:40`; `configs/`. |
| §7: README reproducible y `.env.example` sin secretos | ✅ | `README.md:28`; `.env.example:4`. |
| §7: artefacto versionado y empaquetado reproducible | ✅ | Modelo y `uv.lock` incluidos en Git; `Dockerfile:24`; `scripts/train_vision.py:138`. |
| §7: arquitectura y dos secuencias exigidas | ✅ | `docs/diagrams/02_secuencia_imagen_robot.md:7`; `03_secuencia_aprobacion_humana.md:8`. |
| §8 / §9-12: Azure concreto, trade-offs y modelo de costos | ✅ | `docs/cloud/azure_architecture.md:112`, `:167`; `scripts/build_finops.py:644`. |
| §9-5: historial Git razonable | ✅ | `git rev-list --count HEAD`: **36 commits**; HEAD `d355cc6`. |
| §9-8: threat model y controles | ✅ | `docs/threat_model.md:54`; límites operativos explícitos. |
| §9: repositorio único y mapa raíz de entregables | ✅ | `README.md:7`, `:19`. |

## 3) Alucinaciones o afirmaciones sin respaldo

Las tablas principales de visión y agente coinciden con sus JSON. Recalculadas **431 fórmulas FinOps**, los totales publicados también coinciden por redondeo; esto comprueba consistencia interna, no reverifica las tarifas externas.

:codex-file-citation{path="/Users/nico/Documents/IA/docs/cloud/finops_model.xlsx" artifact_kind="workbook" purpose="source"}

Persisten estas afirmaciones incorrectas o excesivas:

- **Cifras desactualizadas:** `DEFENSA.md:75` dice **4,1%** de pseudo-desconocidos aceptados; metadata registra **42/1.440 = 2,92%** (`models/vision-1.0.2/metadata.json:104`). `DEFENSA.md:162` dice argumentos **10/11**; realmente son **10/10 aplicables y 1 N/A**.
- **Incertidumbre mal explicada:** `DEFENSA.md:60` la atribuye al acuerdo entre vecinos; el score utilizado procede de distancia mínima y perfil por clase (`src/vision/models.py:91`).
- **ResNet contradictorio:** PDF pág. 4/notebook dicen “sin aumentación”, aunque sí se aplica (`scripts/build_vision_report.py:378`; `src/vision/experiments.py:58`). PDF pág. 9 afirma cero ACT en todos los protocolos, pero D estricto tiene **15/720 ACT** (`build_vision_report.py:475`).
- **Latencia y volúmenes antiguos:** `architecture.md:19` dice **18/25 ms**, frente a **13,55/22,62 ms** medidos (`results.json:6294`). Azure dice **7%/~3 GB y ~100 estaciones/S1**, frente a **11,506%/5,135 GiB y ~86** con los supuestos actuales (`azure_architecture.md:228`, `:241`; XLSX `Supuestos!C38`, `C41`, `C47`).
- **Conclusión económica falsa para Colombia:** “cada punto de automatización vale más que elegir Azure o AWS” (`DEFENSA.md:221`). Un punto aporta **USD 108,12/mes**; la diferencia entre proveedores es **USD 227,27/mes**.
- **Garantías excesivas:** “nunca recibe un ángulo dudoso” (`DEFENSA.md:12`) excede la garantía de cumplir reglas con riesgo residual. El catálogo promete auditar todos los fallos de autenticación (`tool_catalog.md:22`), pero los 401 no se registran (`services/agent/app.py:53`).
- **Estimaciones sin benchmark/fuente:** multiplicadores **1,5–3×/~2×** (`agent_design.md:38`), cuota **~20 solicitudes/día** (`configs/agent.yaml:6`). Las tarifas Gemini están correctamente marcadas como **no verificadas**.

## 4) Errores técnicos restantes

1. **Staging elimina restricciones calibradas.** `add_class()` recalcula `symmetric_classes` exclusivamente por LOO y pierde **obj35, obj47, obj94 y obj95**; la metadata copiada conserva la lista antigua. Evidencia: [models.py:66](/Users/nico/Documents/IA/src/vision/models.py:66), [:122](/Users/nico/Documents/IA/src/vision/models.py:122), [metadata.json:252](/Users/nico/Documents/IA/models/vision-1.0.2/metadata.json:252).

2. **SKU nuevos incompatibles con revisión y ángulo manual.** Se admiten nombres nuevos, pero ambos flujos solo aceptan `obj1..obj100`. Evidencia: [tools.py:526](/Users/nico/Documents/IA/services/agent/tools.py:526), [:700](/Users/nico/Documents/IA/services/agent/tools.py:700), [app.py:92](/Users/nico/Documents/IA/services/agent/app.py:92).

3. **Promoción con garantías distintas según entrada.** El agente exige métricas angulares; la API administrativa únicamente comprueba `sufficient`, que no incorpora esos límites. Tampoco se mide regresión sobre clases existentes. Evidencia: `src/vision/inference.py:164`, `:167`; `services/vision_api/app.py:264`; `services/agent/tools.py:613`.

4. **Evaluación factual insuficiente.** Un probe en memoria con respuesta «ambas líneas tienen 99% de rechazos» pasa UC3-03 si aparecen las tools y nombres esperados. Demuestra un falso positivo del evaluador, **no que la corrida real inventara esas cifras**. Evidencia: `eval/agent/cases.yaml:194`; `run_eval.py:261`.

5. **Deadline incompleto y documentación mal generada.** La llamada LLM puede superar el límite sin nueva comprobación antes de terminar (`graph.py:188`, `:232`). Hay marcadores literales en `architecture.md:78`, `finops.md:37`, `azure_architecture.md:384` y PDF pág. 1 (`build_vision_report.py:323`).

## 5) Cambios priorizados

1. Preservar restricciones de simetría y exigir regresión de clases existentes antes de promover.
2. Unificar gates de promoción en el servicio de visión.
3. Admitir los SKU realmente registrados en revisión humana y ángulo manual.
4. Evaluar el diseño completo congelado con datos intactos; retirar mientras tanto las afirmaciones de independencia.
5. Verificar cifras, citas y conclusiones del agente contra las respuestas estructuradas; corregir los tres fallos funcionales persistentes.
6. Aplicar un deadline que abarque llamadas LLM y reintentos.
7. Corregir las afirmaciones enumeradas y regenerar PDF/documentos sin marcadores.
8. Completar la suite y el smoke test desde un clon limpio con temporales escribibles.