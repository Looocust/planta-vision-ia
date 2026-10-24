# Catálogo de tools del agente

**16 tools: 8 de lectura y 8 de escritura.** La fuente de verdad es `services/agent/tools.py`: modelos pydantic con `extra="forbid"`. Los JSON Schema se exportan a `docs/tool_schemas/<tool>.json` con `uv run python eval/agent/export_tool_schemas.py`. Los permisos por rol están en `configs/roles.yaml` y los límites en `configs/policy.yaml`.

## Reglas comunes

- **Identidad verificada.** La API exige `Authorization: Bearer <token>` firmado con HMAC (`services/agent/auth.py`). Usuario, rol y plantas salen del token, nunca del cuerpo. En producción esto es un token OIDC de Entra ID.
- **Contexto inyectado.** `session_id`, `plant_id`, `line_id`, `user_id`, `role` y los relojes llegan en `ToolContext`, no como argumentos del LLM.
  - `line_id` es opcional y solo acepta líneas de la misma planta; si no, devuelve `scope_violation`.
  - Cualquier argumento no declarado (p. ej. `plant_id`) se rechaza con `invalid_args`.
- **Guard previo.** Antes de ejecutar se verifica que la tool exista, el esquema, el RBAC, los duplicados/bucles, el presupuesto de tokens y el tiempo del turno. Además, el ejecutor **re-chequea por cada llamada** de un lote paralelo (tiempo, tokens, pasos y `max_write_calls_per_turn`) justo antes del efecto.
- **Fallas.**
  - `ToolError`: se devuelve estructurado.
  - Excepción inesperada: `internal_error`, sin stacktrace.
  - `tool_timeout` a los 20 s: el turno no espera al worker (`shutdown(wait=False)`).
- **Qué ve el LLM.**
  - JSON estructurado; si supera el límite se recorta, pero siempre queda JSON válido.
  - Emails y teléfonos se redactan en **todas** las cadenas de salida de las tools, en las preferencias y en los resúmenes.
  - Nombres de operarios enmascarados.
  - El texto libre de terceros va como `{"untrusted_text", "possible_prompt_injection"}`.
  - Nunca ve bytes de imagen, rutas ni tokens.
- **Auditoría.** Toda ejecución o bloqueo, las aprobaciones, las confirmaciones de etiqueta y los fallos de autenticación quedan en `audit_log`.
- **Relojes.**
  - Reloj **simulado** de planta, para datos y ventanas de turno.
  - Reloj **real** de seguridad, para la expiración de cargas, aprobaciones, tokens y memoria. Las lecturas respetan `uploads.expires_at`. `review.purge_expired()` borra archivos y filas vencidas; se ejecuta al arrancar y en `POST /v1/admin/purge`.

## Lectura (8)

| Tool | Entrada | Salida | Notas |
|---|---|---|---|
| `inspect_images` | `image_ids[1..6]`, `top_k` | Por imagen: decisión ACT/REVIEW/REJECT del servicio, clase y confianza, ángulo e incertidumbre, calidad, razones explicadas, acción recomendada y vistas similares con URL | Solo en plantas vinculadas a la instancia de visión (si no, `vision_not_bound`). Solo imágenes de la planta/línea **no expiradas**. Si visión falla (5xx, timeout o circuito abierto): `degraded`, `HOLD`, sin comando al robot. Registra un log de inspección |
| `list_review_queue` | `status` pending\|label_proposed\|labeled, `line_id?`, `limit` | Casos de la planta con evidencia y predicción; si hay propuesta pendiente, también la propuesta | Solo la planta de la sesión |
| `get_line_metrics` | `shift?`, `date?`, `start?`/`end?`, `line_id?` | Resumen, línea base de 24 h, serie horaria, `model_version_changes_in_window`, eventos de línea, estado y umbrales | — |
| `get_camera_health` | `line_id?`, `hours_back` o `start`/`end` | Lecturas, rango nominal, anomalías y limpiezas | — |
| `get_model_versions` | — | Registro **filtrado por planta** (versiones base compartidas y candidatos de la planta), despliegues y umbrales | Los candidatos de otras plantas no se ven |
| `get_robot_events` | `line_id?`, `hours_back`/ventana, `gripper_id?` | Fallos por ventosa y hora, vacío, eventos con operario enmascarado, mantenimiento | — |
| `search_procedures` | `query`, `k` | Secciones `[DOC §SEC]` con `trust` y bandera de inyección | Las fuentes no oficiales van envueltas |
| `list_tickets` | `status`, `line_id?` | Tickets de la planta, con título redactado y descripción como `untrusted_text` | — |

## Escritura (8)

| Tool | Efecto | Idempotencia / atomicidad |
|---|---|---|
| `enqueue_for_review` | Crea un caso a partir de la última inspección no-ACT | Índice `UNIQUE(plant_id, image_id)`. Repetir devuelve `already_queued` con el mismo caso, también ante carreras (IntegrityError) |
| `submit_review_label` | **Solo propone** la etiqueta y el ángulo; el caso queda `label_proposed`. Ver el flujo de confirmación más abajo | Si ya está etiquetado con los mismos valores: `already_labeled`. Con valores distintos: `conflict` |
| `create_ticket` | Inserta un ticket con citas | Clave planta+línea+categoría+día: devuelve `already_exists` |
| `escalate_ticket` | Asigna equipo y sube la severidad | Si ya está escalado al mismo equipo: `already_escalated` |
| `stage_new_sku` | Valida antes de llamar a staging; ver "Alta de SKU" más abajo | Clave planta+sku+**(imagen, ángulo)**: `already_staged` |
| `request_risky_operation` | Valida rol, estado, límites, gates y vínculo de planta. Crea una aprobación pendiente (TTL de 60 min en reloj real) **sin ejecutar nada** y pausa el grafo | Una solicitud pendiente idéntica del mismo usuario devuelve el mismo `approval_id` |
| `generate_shift_report` | Persiste el reporte de entrega | Upsert por planta+línea+turno |
| `save_user_preference` | Preferencia con clave cerrada, valor redactado, TTL de 30 días en reloj real | Upsert. Rechaza valores con patrón de inyección |

### Confirmación de etiquetas (`submit_review_label`)

1. El LLM solo deja el caso en `label_proposed`.
2. Un humano con token y rol de etiquetado (supervisor, quality, ml_engineer) de esa planta confirma en `POST /v1/review-labels/{case_id}` (`decision`, `label`, `angle_deg`). Los valores deben coincidir con la propuesta mostrada.
3. Al confirmar, en **una transacción**, el caso pasa a `labeled` y se inserta en `retraining_dataset`.
4. Un LLM comprometido no puede escribir en el dataset (`test_compromised_llm_cannot_write_dataset`).

### Alta de SKU (`stage_new_sku`)

Validación previa a staging:
- ≥ 12 vistas.
- Ángulos únicos.
- Cobertura **circular** ≥ 270°.
- Hueco máximo ≤ 60°.
- Sin vistas duplicadas por sha256.
- Planta vinculada a la instancia de visión.

Si pasa, el candidato se registra en `staging` **con `plant_id`** y se calculan los gates.

## Gates de promoción (`_promotion_gates`, siempre con alcance de planta)

- **Candidato de modelo completo:**
  - accuracy ≥ 0.90.
  - Caída ≤ 1 punto frente al modelo que corre hoy en la línea.
  - MAE angular sin empeorar más de 1°.
- **Candidato de alta de SKU** (métricas LOO del endpoint de staging):
  - El servicio lo marca suficiente.
  - Sin `confusable_with_known_sku`.
  - Accuracy LOO de clase ≥ 0.90.
  - MAE angular LOO ≤ 15°.
  - Dentro de ±10° ≥ 70 %.
- **Limitación:** el endpoint no reporta regresión sobre las clases existentes. Se usa la confusión como proxy, y esa verificación queda para la validación offline.

## Operaciones que modifican el servicio de visión

La demo tiene **una** instancia de visión, vinculada a `P1` (`configs/agent.yaml › vision.bound_plants`). `inspect_images`, `set_threshold`, `promote_model` y `stage_new_sku` se rechazan con `vision_not_bound` en plantas no vinculadas: P2 no puede usar ni modificar la instancia de P1. En producción hay una instancia de visión en el edge por planta/línea (stamp).

Promover:
- actualiza `lines.model_version` de las líneas de la planta;
- archiva solo versiones de esa planta.

Un cambio de umbral se registra en todas las líneas de la planta.

## Ejecución de operaciones riesgosas (`services/agent/approvals.py::decide`)

Solo se invoca con `POST /v1/approvals/{id}`, y el aprobador es la identidad del token.
1. Comprueba que el aprobador:
   - exista con ese rol;
   - pertenezca a la misma planta;
   - no sea el solicitante;
   - tenga un rol en `can_approve`.
2. Comprueba que la solicitud siga pendiente y no haya expirado (reloj real).
3. **Re-valida** precondiciones, límites, gates y vínculo de planta.
4. Ejecuta: estado de línea, `PUT /v1/admin/thresholds`, `robot_commands` (UNIQUE por aprobación) o `POST /v1/admin/promote`.
5. Audita y reanuda el hilo de conversación.

Las decisiones se serializan con un lock **dentro de un proceso**. Un despliegue con varios procesos necesitaría un lock en la base o un UPDATE condicional (ver limitaciones en `agent_design.md`).
