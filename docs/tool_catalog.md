# Catálogo de tools del agente

Fuente de verdad: `services/agent/tools.py` (modelos pydantic con `extra="forbid"`). Los JSON Schema exportados están en `docs/tool_schemas/<tool>.json` y se regeneran con `uv run python eval/agent/export_tool_schemas.py`. Los permisos por rol están en `configs/roles.yaml` y los límites en `configs/policy.yaml`.

## Reglas comunes a todas las tools

- **Contexto inyectado, no elegido por el LLM:** `session_id`, `plant_id`, `line_id`, `user_id`, `role` y el reloj de planta vienen de la sesión (`ToolContext`). El argumento opcional `line_id` solo acepta líneas de la misma planta; si no, devuelve `scope_violation`. Cualquier argumento no declarado (p. ej. `plant_id`) se rechaza con `invalid_args`.
- **Validación previa (nodo guard):** se comprueba que la tool exista, el esquema y el RBAC, y se detectan duplicados o bucles. Las llamadas rechazadas vuelven al LLM como `{"error": code, "message": ...}` para que pueda corregir, y quedan auditadas como `tool_blocked`.
- **Fallas:** los errores de negocio (`ToolError`) se devuelven estructurados. Las excepciones inesperadas se devuelven como `internal_error`, sin stacktrace. Si una tool supera el tiempo se devuelve `tool_timeout` (20 s).
- **Qué ve el LLM:** solo JSON estructurado y truncado (3 500 caracteres). Nunca ve bytes de imagen, rutas de archivos ni tokens. Los nombres de operarios van enmascarados. El texto libre de terceros (tickets, notas no oficiales) va envuelto en `{"untrusted_text", "possible_prompt_injection"}`.
- **Auditoría:** cada ejecución o bloqueo se registra en `audit_log` (ts, sesión, turno, usuario, rol, planta, línea, tool, args redactados, resultado).

## Tools de lectura (9)

| Tool | Entrada (resumen) | Salida | Roles | Fallas / notas |
|---|---|---|---|---|
| `inspect_images` | `image_ids[1..6]` (`img_[a-f0-9]{12}`, sin repetir), `top_k` 1–5 | Por imagen: decisión ACT/REVIEW/REJECT, objeto+confianza, ángulo+incertidumbre, calidad, razones explicadas, acción recomendada y vistas similares con URL de evidencia (`/v1/images/{id}` del servicio de visión) | operator, supervisor, quality, maintenance, ml_engineer, plant_manager | Imagen de otra planta o línea: `not_found_in_scope`. Visión 5xx, timeout o circuito abierto: `degraded=true`, `HOLD`, sin comando al robot. Registra la inspección (`inspections`) |
| `list_review_queue` | `status` pending\|labeled, `line_id?`, `limit` ≤ 25 | Casos de la planta con predicción, razones y URL de evidencia | supervisor, quality, ml_engineer, plant_manager | Filtra por la planta de la sesión |
| `get_line_metrics` | `shift?` turno_1..3, `date?`, `start?`/`end?` ISO, `line_id?` | Resumen (tasas de rechazo, revisión y fallo de agarre, confianza, versiones), línea base de las 24 h previas, serie horaria, eventos de línea, estado y umbrales | todos | `end ≤ start`: `invalid_args`. Sin datos: mensaje explícito |
| `get_camera_health` | `line_id?`, `hours_back` 1–48 o `start`/`end` | Lecturas, rango nominal, anomalías (primera anomalía) y eventos de limpieza | todos | — |
| `get_model_versions` | — | Registro (estado, métricas, notas), despliegues por línea de la planta, umbrales vigentes | supervisor, quality, ml_engineer, plant_manager | — |
| `get_robot_events` | `line_id?`, `hours_back`, `gripper_id?` | Fallos por ventosa y por hora, vacío promedio y máximo, muestra de eventos (operario enmascarado), bitácora de mantenimiento | supervisor, quality, maintenance, ml_engineer, plant_manager | — |
| `search_procedures` | `query` 3–300 caracteres, `k` ≤ 6 | Secciones con `citation` `[DOC §SEC]`, `trust`, score y texto. Las secciones no oficiales o con inyección van envueltas como no confiables | todos | Sin resultados sobre el umbral: lista vacía |
| `list_tickets` | `status` open\|closed\|all, `line_id?` | Tickets de la planta (descripción como `untrusted_text` con bandera de inyección) | supervisor, quality, maintenance, ml_engineer, plant_manager | No devuelve tickets de otras plantas |

## Tools de escritura (7)

| Tool | Entrada | Efecto | Idempotencia ante repetición | Roles |
|---|---|---|---|---|
| `enqueue_for_review` | `image_id`, `note?` | Crea un caso en `review_queue` a partir de la última inspección | Si la imagen ya está en cola: `already_queued`, devuelve el mismo `case_id`. Si la inspección fue ACT: `not_needed`. Sin inspección previa: `precondition_failed` | operator, supervisor, quality, ml_engineer |
| `submit_review_label` | `case_id` `RC-…`, `label` obj1..obj100, `angle_deg` [0,360), `comment?` | Marca el caso como etiquetado y lo inserta en `retraining_dataset` | La misma etiqueta otra vez devuelve `already_labeled`. Una etiqueta distinta devuelve `conflict`. `case_id` es UNIQUE en el dataset | supervisor, quality, ml_engineer |
| `create_ticket` | `title`, `description`, `severity`, `category`, `line_id?`, `citations[]` | Inserta un ticket abierto con citas | Clave = planta+línea+categoría+día; si existe, devuelve `already_exists` con el ticket existente | supervisor, quality, maintenance, ml_engineer, plant_manager |
| `escalate_ticket` | `ticket_id`, `to_team` (enum), `reason` | Asigna equipo y sube la severidad a high | Si ya está escalado al mismo equipo: `already_escalated`. Si está cerrado: `precondition_failed` | supervisor, maintenance, plant_manager |
| `stage_new_sku` | `sku`, `image_ids[]`, `angles[]` (misma longitud) | Valida suficiencia (≥ 12 vistas, cobertura ≥ 270°) **antes** de llamar a `/v1/skus/stage`. Registra el candidato `staging` y evalúa los gates de promoción. Nunca toca producción | Clave = planta+sku+imágenes: `already_staged` | quality, ml_engineer |
| `request_risky_operation` | `operation` ∈ {set_threshold, pause_line, resume_line, manual_angle, promote_model}, `justification`, `line_id?` y parámetros planos según la operación | Valida rol solicitante, estado de línea, límites y gates. Crea una **aprobación pendiente** (TTL 60 min) y **no ejecuta** nada. Pausa el grafo con `interrupt` | Una solicitud pendiente idéntica del mismo usuario devuelve el mismo `approval_id`. La clave se libera al decidirse | supervisor, quality, maintenance, ml_engineer, plant_manager (según `can_request`) |
| `generate_shift_report` | `shift`, `date?`, `line_id?` | Compila métricas, peores horas, incidentes, fallos por ventosa, tickets abiertos, casos y aprobaciones pendientes, y lo persiste en `shift_reports` | Upsert por planta+línea+turno (`updated`) | supervisor, quality, plant_manager |
| `save_user_preference` | `key` (enum cerrado), `value` ≤ 120 caracteres | Upsert en `user_memory` con TTL de 30 días | Upsert. Rechaza valores con patrón de inyección (envenenamiento de memoria) | todos |

## Ejecución de operaciones riesgosas (fuera del catálogo del LLM)

`services/agent/approvals.py::decide` se invoca solo vía `POST /v1/approvals/{approval_id}`, por un humano:

1. Autentica al aprobador: debe existir, su rol debe coincidir con el registrado y debe pertenecer a la planta de la solicitud.
2. Comprueba que el aprobador no sea el solicitante y que su rol esté en `can_approve` para la operación.
3. Comprueba que la solicitud esté `pending` y no haya expirado. Si ya se decidió, devuelve el estado actual sin re-ejecutar (idempotente).
4. Si es `reject`, pasa a `rejected` y no ejecuta nada.
5. Si es `approve`, **re-valida** estado de línea, límites y gates, y ejecuta:
   - `pause_line` / `resume_line`: cambian el estado de la línea.
   - `set_threshold`: `PUT /v1/admin/thresholds` y actualización de la DB.
   - `manual_angle`: inserta en `robot_commands` (UNIQUE por `approval_id`, a lo sumo un comando).
   - `promote_model`: `POST /v1/admin/promote` y actualización del registro.
   El resultado queda `executed` o `failed` (con la causa).
6. Audita todo, incluidos los intentos denegados. Reanuda el hilo del solicitante con `Command(resume=…)` para dejar constancia en la conversación.
