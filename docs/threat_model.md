# Modelo de amenazas del asistente agéntico

**Alcance:** API del agente (8002) y su UI, grafo LangGraph, tools sobre los backends de planta (SQLite), cliente del servicio de visión (8001) y el proveedor LLM (Gemini).

**Activos:**
- seguridad física (robot y línea);
- integridad del modelo productivo y de sus umbrales;
- datos de cada planta;
- dataset de reentrenamiento;
- disponibilidad y costo.

La evidencia se cita por test (`tests/test_agent_core.py`) o por caso de evaluación (`eval/agent/cases.yaml`, `holdout.yaml`). Un control solo se marca como cubierto donde hay evidencia; los riesgos residuales se listan al final.

## STRIDE

| # | Amenaza | Control | Evidencia |
|---|---|---|---|
| S1 | Suplantación: el cliente declara otra identidad o rol | Token bearer firmado con HMAC-SHA256 (`AUTH_SECRET`). Usuario, rol y plantas salen del token y se re-contrastan con el directorio. Un `user_id`/`role` en el cuerpo que no coincide devuelve 403. Sin token o con firma falsa: 401. Token expirado: 401. **Límite:** `demo-login` (solo con `DEMO_LOGIN=true` y solo para clientes locales) emite cualquier identidad por diseño; ver riesgos residuales | `test_http_api_end_to_end`, `test_demo_login_is_local_only_and_off_by_default` |
| S2 | Auto-aprobación | El aprobador es **la identidad del token**, no un campo del cuerpo. Debe ser distinto del solicitante, de la misma planta y con rol aprobador | `test_http_api_end_to_end`, `test_risky_op_requires_distinct_authorized_approver`, UC6-03 |
| T1 | Parámetros riesgosos manipulados | Límites absolutos y relativos, rango de ángulo, línea pausada para ángulo manual, re-validación al ejecutar | `test_threshold_bounds_and_relative_change`, `test_manual_angle_requires_paused_line_and_range` |
| T2 | Envenenamiento del dataset (por un LLM comprometido o una inyección) | El LLM solo **propone**. Un humano con token y rol de etiquetado confirma los valores mostrados. Escritura transaccional. UNIQUE por imagen en la cola | `test_compromised_llm_cannot_write_dataset`, `test_low_confidence_goes_to_review_queue_and_label_to_dataset`, `test_http_label_confirmation_by_verified_human`, `test_review_queue_unique_per_image`, H-09 |
| T3 | Promoción de un modelo malo | Gates por planta: accuracy, caída frente al modelo de la línea, MAE angular; para SKU: métricas LOO de ángulo y confusión. Vistas únicas por hash y cobertura circular | `test_promotion_gates_and_flow`, `test_sku_candidate_with_bad_angle_metrics_fails_gates`, `test_sku_onboarding_validation_and_staging`, UC5-04, H-11 |
| R1 | Repudio | `audit_log`: tools ejecutadas y bloqueadas, aprobaciones, denegaciones, confirmaciones de etiqueta, intentos de suplantación | `test_risky_op_requires_distinct_authorized_approver` |
| I1 | Fuga entre plantas | Planta y línea desde la sesión verificada. `extra=forbid`. Consultas filtradas por planta, **incluido el registro de modelos y los candidatos**. Gates exigen que el candidato sea de la planta. `thread_id` incluye planta, línea y usuario | `test_plant_isolation_of_model_registry_and_vision_ops`, `test_cross_plant_line_argument_is_scope_violation`, `test_image_from_other_plant_not_accessible`, `test_session_isolation_between_users`, ADV-10 |
| I2 | Impacto o uso entre plantas vía el servicio de visión compartido | La instancia de demo se vincula a P1. Inspección, umbrales, staging y promoción se rechazan en otras plantas (`vision_not_bound`) | `test_plant_isolation_of_model_registry_and_vision_ops`, `test_inspection_refused_for_plant_not_bound_to_vision`, H-08 |
| I3 | Exposición de PII y secretos | Redacción por regex de emails y teléfonos en todas las salidas de tools, preferencias y resúmenes. Nombres de operarios enmascarados. Imágenes y tokens nunca entran al contexto | `test_redaction_and_valid_truncation`, `test_robot_events_mask_operator_names` |
| D1 | Bucles y costo descontrolado | Límites de pasos, llamadas, duplicados y bloqueos. Presupuesto de tokens, tiempo del turno y máximo de escrituras re-verificados **por llamada**, también dentro de un lote de llamadas paralelas, antes del efecto | `test_loop_detection_stops_turn`, `test_max_steps_limit`, `test_token_budget_checked_before_side_effects`, `test_parallel_write_calls_rechecked_per_call`, `test_parallel_calls_stop_when_turn_time_expires_mid_batch`, ADV-06 |
| D2 | Tools o visión lentas o caídas | Timeout efectivo (no espera al worker), reintentos con backoff, circuit breaker, degradación a HOLD | `test_tool_timeout`, `test_vision_5xx_degrades_gracefully_and_opens_circuit`, ADV-03/04 |
| D3 | Archivos maliciosos o retención indefinida | Máx. 5 MB, verificación con PIL, solo PNG/JPEG, nombre generado por el servidor. Expiración en reloj real y purga | `test_upload_validation`, `test_expiry_uses_security_clock_and_purge` |
| E1 | Elevación vía LLM | Solo se exponen las tools del rol, y el guard re-verifica | `test_rbac_operator_cannot_request_risky_or_tickets`, UC6-04 |

## Riesgos específicos de LLM

| # | Amenaza | Control | Evidencia |
|---|---|---|---|
| L1 | Inyección directa | El LLM no tiene capacidad de actuar sobre el robot ni de escribir en el dataset: solo crea solicitudes o propuestas que confirma otra persona (o el mismo humano verificado, en el caso de etiquetas) | ADV-07, H-09, `test_compromised_llm_cannot_write_dataset` |
| L2 | Inyección indirecta (tickets, notas, SOP) | Texto envuelto como no confiable, con bandera heurística. Las barreras de L1 aplican igual | `test_prompt_injection_in_ticket_is_marked_as_untrusted`, ADV-08, ADV-09 |
| L3 | Agencia excesiva | Tools mínimas por rol; sin SQL, shell ni HTTP genérico. Escrituras idempotentes. Prompt de no ejecutar escrituras no pedidas (control **probabilístico**) | catálogo; UC3-02/UC6-01 en la evaluación |
| L4 | Manejo inseguro de la salida | UI con `textContent`. Pie determinista de aprobación o etiqueta pendiente. Argumentos validados | `test_http_api_end_to_end` |
| L5 | Envenenamiento de memoria | Claves cerradas, valores redactados, rechazo heurístico, TTL | `test_memory_preference_and_poisoning` |
| L6 | Alucinación de datos o citas | Prompt y IDs generados en código. Es control **parcial**: la evaluación muestra casos con respuestas incompletas (ver `docs/report/agent_eval.md`) | UC3-02, UC4-01 |
| L7 | Datos enviados al proveedor | Minimización (sin imágenes ni nombres). En free tier el proveedor puede usar datos: en producción, tier de pago o Vertex con no-retención | `agent_design.md` |
| L8 | Consumo no acotado | Presupuesto por turno, throttle, costo por modelo en cada respuesta | `eval/agent/results/*.json` |

## Lo que la evaluación NO prueba

- Que los escenarios adversos pasen se debe a una **combinación**: barreras deterministas (permisos, límites, aprobación, confirmación, alcance) **más** el comportamiento del LLM (no intentar la acción, explicar). Solo lo primero es garantía. Lo segundo varía por modelo y redacción.
- La detección de inyección por regex es evadible. Es una señal para el LLM, no una barrera.

## Riesgos residuales

- **Identidad de demo (riesgo aceptado, solo demo):** con `DEMO_LOGIN=true`, `/v1/auth/demo-login` permite a cualquiera **elegir cualquier identidad** del directorio. Es intencional para la demostración.
  - Por defecto `DEMO_LOGIN=false` (el endpoint responde 404).
  - Con `true`, solo acepta clientes locales (127.0.0.1/::1) y el arranque emite un warning.
  - Por lo tanto, la separación solicitante/aprobador de la demo prueba la **lógica**, no la autenticación de personas reales.
  - Firma HMAC con secreto compartido. Sin revocación individual (expiración de 8 h y baja en el directorio).
  - En producción: OIDC de Entra ID y este endpoint deshabilitado.
- **Atomicidad entre procesos:** decisiones de aprobación y confirmación serializadas con un lock en proceso. Con varios workers hace falta un UPDATE condicional o un lock en la base. Una llamada remota (promote/umbrales) que se completa justo antes de un crash puede quedar sin registrar como `executed`: la idempotencia remota depende del servicio de visión.
- **Una tool de escritura que agota el timeout puede completar después:** el usuario ve `tool_timeout` sobre algo que sí ocurrió. La idempotencia evita duplicados.
- **Redacción por regex:** no cubre todos los formatos de PII (p. ej. nombres en texto libre de tickets).
- **Regresión de clases existentes al dar de alta un SKU:** no la mide el endpoint de staging; queda como validación offline.
