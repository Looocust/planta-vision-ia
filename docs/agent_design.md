# Diseño del asistente agéntico de operaciones (Reto 3)

Código: `services/agent/` · API: `uv run uvicorn services.agent.app:app --port 8002` · Evaluación: `eval/agent/`.

## 1. Qué es (y qué no es)

Es el copiloto operativo de la línea para supervisores, calidad, mantenimiento, ingenieros de ML y gerentes de planta. No es un formulario ni un chatbot de preguntas y respuestas: un LLM decide qué herramientas usar, en qué orden y con qué argumentos sobre backends reales o simulados. Toda decisión con impacto físico o de seguridad queda fuera del LLM, en código determinista.

- El modelo de visión se usa **como herramienta** (HTTP, `services/vision_api`, puerto 8001) a través de `inspect_images`, `stage_new_sku` y del ejecutor de aprobaciones (umbrales, promoción). El agente no ve imágenes ni recalcula ACT/REVIEW/REJECT.
- Los backends de planta están simulados en SQLite (`services/agent/plant/db.py`), con una siembra determinista que cuenta una historia realista (ver §6).

## 2. Patrón agéntico elegido

**Un solo agente ReAct con tool-calling, dentro de un `StateGraph` de LangGraph, con nodos deterministas de guarda, ejecución e interrupción human-in-the-loop.**

```
START → agent ──tool_calls──► guard ──ok──► tools ──► agent …
          │                     └─stop──► finalize → END
          ├─respuesta final + aprobación pendiente ─► await_approval [interrupt()] → END
          └─respuesta final ─► END
POST /v1/approvals/{id} ─► approvals.decide()  (determinista: RBAC, aprobador ≠ solicitante,
                            re-validación de límites/estado, ejecución idempotente, auditoría)
                         └► Command(resume=…) reanuda el hilo y deja constancia en la conversación
```

| Nodo | Tipo | Responsabilidad |
|---|---|---|
| `agent` | LLM | Razona y elige herramientas. Solo recibe las tools que permite su rol. Revisa los límites de pasos, tiempo y tokens antes de cada llamada. |
| `guard` | determinista | Revisa que la tool exista, valida los argumentos contra el esquema pydantic (`extra=forbid`), aplica RBAC, cuenta llamadas, detecta bucles (tool+args repetidos) y corta el turno tras demasiados bloqueos. |
| `tools` | determinista | Ejecuta las tools con timeout por tool. Inyecta el contexto de sesión (planta, línea, usuario y rol nunca vienen del LLM). Trunca y estructura la salida y audita cada llamada. |
| `finalize` | determinista | Explica en español por qué se detuvo el turno (bucle, límites, LLM caído). |
| `await_approval` | `interrupt()` | Pausa el hilo cuando hay una operación riesgosa pendiente. Lo reanuda la decisión humana. |

### Alternativas descartadas

| Alternativa | Costo | Latencia | Riesgo | Mantenibilidad | Por qué se descarta |
|---|---|---|---|---|---|
| **Multi-agente supervisor** (un router más agentes por dominio: visión, mantenimiento, ML) | 1,5–3× más llamadas LLM: el router y cada subagente razonan por separado | +1 o 2 saltos LLM por turno; con el free tier (pocas RPM) se nota mucho | Más superficie para inyección entre agentes y el contexto se pierde en los traspasos | N prompts y N contratos que versionar y evaluar | Las 16 tools caben en un solo contexto (~2k tokens de esquemas). Los casos de uso cruzan dominios (p. ej. rechazos = métricas + cámara + modelo), y separarlos obliga a coordinar. No hay evidencia de que la separación aporte precisión; sí aporta costo y latencia. |
| **Workflow fijo / router por intención** (clasificador → pipeline fijo por caso de uso) | Mínimo (1 llamada de clasificación) | Mínima | Bajo | Alta para 7 flujos; frágil fuera de ellos | No resuelve preguntas abiertas ("¿por qué subieron los rechazos?") que exigen elegir qué datos correlacionar, ni el diagnóstico iterativo con preguntas al usuario. Sería un formulario, que es justo lo que el reto excluye. |
| **Plan-and-execute** (planificador LLM → ejecutor → re-planificador) | ~2× llamadas (plan + re-plan) | Mayor: el plan completo se genera antes de actuar | Medio: un plan erróneo se ejecuta entero si no se re-planifica | Media | Los turnos típicos necesitan 1–4 tools. El ciclo ReAct ya re-planifica con cada observación. La planificación explícita solo se justificaría en tareas largas (>10 pasos), que aquí se cortan por límites de seguridad. |

**Decisión:** un ReAct con guardas deterministas da la flexibilidad de un agente donde aporta valor (elegir y correlacionar datos, redactar diagnósticos y explicaciones) y confina en código lo que no puede fallar. El grafo explícito (en vez de `create_react_agent`) permite insertar el `guard`, el `interrupt` y la terminación controlada como nodos auditables.

## 3. Decisiones que NUNCA están en manos del LLM

| Decisión | Dónde | Evidencia |
|---|---|---|
| Autenticación (usuario ↔ rol ↔ planta), simulando un IdP | `rbac.authenticate`, `configs/roles.yaml` | `test_authentication_role_and_plant` |
| Autorización por tool (RBAC). Además, solo se exponen al LLM las tools del rol | `guard`, `rbac.tool_allowed` | `test_rbac_operator_cannot_request_risky_or_tickets`, UC2-03, UC6-04 |
| Quién puede solicitar o aprobar cada operación riesgosa. Aprobador ≠ solicitante, misma planta, rol registrado | `approvals._check_approver` | `test_risky_op_requires_distinct_authorized_approver`, UC6-03 |
| Límites de umbrales (rango absoluto y cambio relativo máximo de 30 %) | `tools.validate_risky`, `configs/policy.yaml` | `test_threshold_bounds_and_relative_change`, UC6-05 |
| Ángulo manual: rango [0, 359.9], clase conocida y línea **pausada** | `validate_risky` | `test_manual_angle_requires_paused_line_and_range`, UC6-06/07 |
| Gates de promoción (accuracy ≥ 0.90, caída ≤ 1 punto vs. producción, estado staging) | `_promotion_gates` | `test_promotion_gates_and_flow`, UC5-04 |
| Re-validación de todas las precondiciones **al ejecutar** (no solo al solicitar) | `approvals.decide` | la línea se reanuda antes de aprobar → `failed` |
| Decisión ACT/REVIEW/REJECT | servicio de visión; el agente solo la transmite | UC1-xx |
| Idempotencia (tickets, cola, etiquetas, staging, aprobaciones, reportes, comandos de robot) | claves de idempotencia / UNIQUE | `test_ticket_idempotency_and_escalation`, `test_duplicate_request_is_idempotent`, UC4-05 |
| Aislamiento de tenant: planta, línea y usuario se inyectan desde la sesión. `line_id` opcional validado contra la planta. Argumentos extra (`plant_id`) se rechazan | `ToolContext`, `_line`, `extra=forbid` | `test_cross_plant_line_argument_is_scope_violation`, `test_extra_args_like_plant_id_are_rejected`, ADV-10 |
| Suficiencia de vistas para alta de SKU (≥ 12 vistas, cobertura ≥ 270°) antes de llamar a staging | `stage_new_sku` | `test_sku_onboarding_validation_and_staging`, UC5-02 |

## 4. Criterios de terminación y límites (`configs/agent.yaml › limits`)

| Límite | Valor | Comportamiento |
|---|---|---|
| `max_steps_per_turn` | 8 invocaciones LLM | `finalize` con `stop_reason=max_steps` |
| `max_tool_calls_per_turn` | 12 | corta y cierra las llamadas pendientes |
| `max_identical_calls` | 1 repetición | La 1.ª repetición de tool+args se bloquea ("usa el resultado anterior"). La 2.ª corta el turno con `loop_detected` |
| `max_blocked_calls_per_turn` | 4 | `too_many_blocked` (p. ej. un LLM insistiendo en una tool prohibida) |
| `tool_timeout_s` | 20 s por tool (visión: timeout HTTP de 5 s, 2 reintentos) | resultado `tool_timeout`, sin propagar excepciones |
| `turn_timeout_s` | 120 s | `turn_timeout` |
| `token_budget_per_turn` | 120 000 | `token_budget` |
| LLM caído / 429 | reintentos con backoff exponencial que respetan `retry in Xs` | Si se agotan: `llm_error` con un mensaje honesto y ninguna acción adicional |

Al cortar un turno, el guard siempre responde a todos los `tool_calls` pendientes con un `ToolMessage` (`not_executed`), para que el historial siga siendo válido para el proveedor.

## 5. Estado, memoria y contexto

| Capa | Qué | Dónde | Retención | Identificadores |
|---|---|---|---|---|
| Estado de ejecución | `AgentState` (mensajes, contadores del turno, firmas de llamadas, aprobación pendiente, tokens) | LangGraph `SqliteSaver` → `data/runtime/agent_checkpoints.db` | Vida de la sesión. Purga operativa propuesta: 7 días | `thread_id = plant:line:user:session_id` |
| Historial conversacional | mensajes humanos, IA y tool del hilo | mismo checkpoint | ídem | `session_id`, `turn_id` (request_id del turno) |
| Resúmenes de turnos | 1 línea determinista por turno (pregunta, tools, inicio de la respuesta), máx. 10 | `turn_summaries` en el estado | ídem | — |
| Memoria persistente de usuario | preferencias con lista cerrada de claves (formato, nivel de detalle, línea habitual…) | tabla `user_memory` | TTL 30 días (`policy.yaml`), máx. 20 por usuario | `user_id` |
| Conocimiento recuperado | secciones de SOP (TF-IDF bigramas, normalización de acentos) con `trust` official/untrusted | `docs/sop/*.md`, índice en memoria | versión del repo | `doc_id`, `section_id` |
| Datos operativos | métricas, cámara, robot, tickets, cola, registro de modelos, aprobaciones, auditoría | `data/runtime/plant.db` | auditoría append-only | `approval_id`, `ticket_id`, `case_id`, `request_id` (visión), `image_id` |
| Imágenes subidas | archivo + sha256 + dueño | `data/runtime/uploads/<plant>/<line>/img_*.png` + tabla `uploads` | 30 días (`expires_at`). Nunca se envían al LLM | `image_id` |

**Control de crecimiento del contexto** (`graph.build_context`): el turno actual siempre va completo. Los turnos anteriores entran enteros mientras quepan en `max_history_tokens` (6 000), con las salidas de tools antiguas comprimidas a 300 caracteres. Nunca se separa un par `tool_call`/`ToolMessage`. Lo que no cabe queda representado por los resúmenes deterministas del system prompt. Las salidas de tools son JSON estructurado, truncado a 3 500 caracteres.

**Aislamiento:** el `thread_id` incluye planta, línea y usuario, así que dos usuarios con el mismo `session_id` no comparten historial (`test_session_isolation_between_users`). Las imágenes solo se resuelven dentro de la planta y línea de la sesión.

**Información sensible:** los nombres de operarios se enmascaran (`C. P***`), los emails y teléfonos se redactan en auditoría y en texto libre, y las imágenes crudas y los secretos nunca entran al contexto. El token de admin de visión solo vive en el cliente HTTP y se lee de `.env`.

## 6. Backends simulados (siembra determinista, reloj de planta fijo 2026-10-24 07:30)

- Dos plantas (P1 Bogotá: P1-L1 y P1-L2; P2 Medellín: P2-L1) para las pruebas de aislamiento.
- Métricas horarias de los últimos 3 turnos más el turno en curso, por línea.
- **Historia en P1-L1:**
  - Turno 2 (18–21 h): caída de vacío en la ventosa G2 (-45 kPa) que provoca fallos de agarre.
  - 01:50: limpieza de la cámara sin recalibrar, con lo que la exposición pasa de 8 a 12,5 ms y el brillo de 0,46 a 0,69.
  - 02:40: canary de `vision-1.1.0`.
  - 03:00: los rechazos suben del 2 % al 16 %.
  - El agente debe correlacionar las tres fuentes. El SOP [ML-04.2] advierte que se descarte la captura antes de culpar al modelo.
- Cola de revisión sembrada con IDs reales de COIL-100. Las predicciones son simuladas.
- Tickets, entre ellos uno con inyección de prompt (TCK-1002) y uno confidencial de P2 (TCK-2001).
- Registro de modelos con un candidato que pasa los gates (rc1) y otro que no (rc0).
- SOPs en `docs/sop/` con IDs de sección para las citas, más una nota no oficial con inyección (NOTA-07).

## 7. Robustez frente al servicio de visión

`vision_client.py`:
- Timeout de 5 s.
- Reintentos con backoff exponencial solo ante 5xx, timeout o error de conexión.
- Circuit breaker que se abre tras 3 fallos consecutivos y pasa a half-open a los 30 s.
- Errores 4xx devueltos como datos, nunca como excepciones.

Ante fallas, `inspect_images` devuelve `degraded: true`, `robot_command: HOLD` y un mensaje para el usuario. El agente nunca tiene un canal para enviar comandos al robot: el único es el ejecutor de `manual_angle` tras una aprobación humana. La inyección de fallas usa la cabecera `X-Fault` (contrato del servicio), que activa `VisionClient.fault`.

## 8. Limitaciones conocidas

- La idempotencia de `create_ticket` agrupa por planta+línea+categoría+día. Dos problemas distintos de la misma categoría el mismo día se fusionan en un solo ticket. Es una decisión conservadora frente a duplicados del LLM.
- El timeout por tool no puede matar el hilo de una tool de escritura lenta. Mitigación: idempotencia y timeouts HTTP más cortos que el de la tool.
- Los resúmenes de turnos antiguos son deterministas (truncados) y no resúmenes LLM: más baratos, pero menos informativos.
- Los umbrales del servicio de visión son globales en el contrato. El agente los registra por línea en la DB, pero el servicio los aplica a todo.

## 9. Cómo ejecutar

```bash
# variables (.env, nunca en código): GOOGLE_API_KEY, LLM_PROVIDER=google_genai, LLM_MODEL=gemini-3.1-flash-lite (por defecto),
# opcionales: LLM_FALLBACK_MODELS=gemini-3-flash-preview, LLM_MIN_INTERVAL_S, LLM_THINKING_LEVEL=low,
# VISION_API_URL=http://localhost:8001, VISION_ADMIN_TOKEN (o ADMIN_TOKEN), PLANT_DB_PATH, PLANT_SIM_NOW
uv run python -m services.agent.plant.db                     # (re)siembra la DB de planta
uv run uvicorn services.vision_api.app:app --port 8001       # servicio de visión
uv run uvicorn services.agent.app:app --port 8002            # agente + UI en http://localhost:8002/
uv run pytest tests/test_agent_core.py -q                    # pruebas offline (modelo guionado + stub)
uv run python eval/agent/run_eval.py                         # evaluación (LLM real, stub de visión)
uv run python eval/agent/run_eval.py --vision real           # evaluación contra el servicio de visión real
uv run python eval/agent/integration_vision.py               # integración del cliente con el servicio real
uv run python eval/agent/export_tool_schemas.py              # JSON Schemas → docs/tool_schemas/
```

Flujo de aprobación en la UI:
1. El solicitante (p. ej. `u_sup_p1`) pide "pausa la línea" y recibe un `APR-…` pendiente.
2. Se cambia el selector de usuario a un aprobador (p. ej. `u_sup2_p1`).
3. En el panel "Aprobaciones pendientes" se pulsa Aprobar o Rechazar.

Si el aprobador es el mismo solicitante o tiene un rol sin permiso, el intento se rechaza con 403 y queda auditado.
