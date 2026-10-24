# 08 · Verificación EN VIVO del Reto 3 (asistente agéntico)

Fecha: 2026-10-24 · Sistema: visión `http://localhost:8001` (`vision-1.0.2`), agente `http://localhost:8002` (`gemini-3.1-flash-lite`, `DEMO_LOGIN=true`).
Alcance pedido: los 7 casos obligatorios de §6.1, operaciones de riesgo con permisos + aprobación humana, escenarios de §6.5, contra los servicios ya levantados (sin reiniciarlos).

## Veredicto

**Bloqueado. No se pudo verificar ningún caso de uso de §6.1 en vivo.** El agente de `:8002` está levantado y `/health` responde `ok`, pero **todas las rutas que necesitan `AgentService` fallan**: `/v1/chat`, `/v1/uploads` (para un usuario autorizado), `GET /v1/review-labels` y `/v1/approvals`. Al construir el cliente de Gemini sale `ssl.SSLError` (detalle en B1). Las llamadas LLM consumidas son **0 de 14**.

Lo único que pasa en vivo es la capa de autenticación/RBAC del borde HTTP, porque no depende del servicio.

## Resultados por caso de uso

| # | Caso (§6.1) | Rol | Estado | Tools llamadas | Evidencia | Efecto lateral verificado |
|---|---|---|---|---|---|---|
| 1 | Inspección de imagen (ACT + REVIEW) | u_sup_p1 | ❌ no ejecutable | — | `POST /v1/uploads` con token válido → sin respuesta en 10 s (timeout, el servicio se reconstruye en cada request y falla) | — |
| 2 | Cola de revisión + confirmación humana | u_sup_p1 | ❌ no ejecutable | — | `GET /v1/review-labels?plant_id=P1` con token válido → timeout/500 | — |
| 3 | Consulta operativa (turno 2 / rechazos 3 a. m.) | u_pm_p1 | ❌ no ejecutable | — | `/v1/chat` → 500 (B1) | — |
| 4 | Diagnóstico de falla + ticket idempotente | u_mnt_p1 | ❌ no ejecutable | — | `/v1/chat` → 500 (B1) | — |
| 5 | Alta de SKU (staging + aprobación, sin promover) | u_ml | ❌ no ejecutable | — | uploads/chat → 500 (B1) | — |
| 6 | Operación de riesgo (pausa con 4 ojos; ángulo manual de operador) | u_sup_p1 / u_sup2_p1 / u_op_p1 | ❌ no ejecutable | — | `/v1/chat` → 500 (B1) | — |
| 7 | Reporte de entrega de turno | u_sup_p1 | ❌ no ejecutable | — | `/v1/chat` → 500 (B1) | — |

## Chequeos adversariales sin LLM (contra :8002)

| Chequeo | Esperado | Obtenido | Estado |
|---|---|---|---|
| `POST /v1/approvals/xyz` sin token | 401 | 401 | ✅ |
| `POST /v1/approvals/xyz` con token falso | 401 | 401 | ✅ |
| `POST /v1/chat` sin token | 401 | 401 | ✅ |
| u_sup_p2 (planta P2) → `POST /v1/chat` en P1 | 403 | 403 `u_sup_p2 no está autorizado para la planta P1` | ✅ |
| u_sup_p2 → `GET /v1/review-labels?plant_id=P1` | 403 | 403 | ✅ |
| u_sup_p2 → `GET /v1/approvals?plant_id=P1` | 403 | 403 | ✅ |
| u_sup_p2 → `POST /v1/uploads` en P1 | 403 | 403 | ✅ |
| `demo-login` con usuario inexistente (`u_hacker`) | 4xx | 404 `usuario desconocido` | ✅ |
| u_op_p1 manda `role: plant_manager` en el cuerpo de `/v1/chat` | 403 | **500**: la primera vez tardó unos 4 min, la siguiente 0,1 s (B2) | ❌ |

Escenarios de §6.5 (fuera de dominio, resultado no confiable, tool o argumentos inválidos, 5xx o timeout de visión, loop): **no verificables en vivo**, porque todos pasan por `/v1/chat`.

## Bugs y hallazgos con reproducción exacta

### B1 · El agente de :8002 no puede crear el cliente LLM (bloqueante)

Traza en `data/runtime/logs/agent.log`:

```
services/agent/app.py:48 get_service -> make_chat_model()
services/agent/llm.py:86 init_chat_model(...)
google/genai/_api_client.py:1389 ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE', certifi.where()), ...)
ssl.SSLError: [X509: NO_CERTIFICATE_OR_CRL_FOUND] no certificate or crl found (_ssl.c:4147)
```

Para reproducirlo:

1. `curl -X POST localhost:8002/v1/auth/demo-login -d '{"user_id":"u_op_p1"}' -H 'content-type: application/json'` devuelve un token.
2. Cualquier `POST /v1/chat` con ese token devuelve 500 y deja la traza de arriba.

Causa raíz, según el diagnóstico:
- El proceso del agente (PID 71708) fue lanzado desde una shell de Claude Code: su entorno incluye `AI_AGENT=claude-code_…`. Esa shell corre en un **sandbox** que impide que OpenSSL cargue el bundle de CAs.
- El fallo es reproducible en el sandbox con `.venv/bin/python -c "import ssl,certifi; ssl.create_default_context(cafile=certifi.where())"`, que da el mismo `SSLError`.
- **Fuera del sandbox el mismo comando funciona** (`ssl ok`). El `cacert.pem` está íntegro: 235 KB y 118 certificados.
- Por tanto es un **problema de entorno, no del código del repo**. Aunque se arreglara SSL, ese sandbox probablemente también bloquearía la salida de red hacia Gemini.

Arreglo: relanzar `scripts/run_local.sh` (o `make up`) desde una terminal normal del usuario, fuera del sandbox del agente de código.

### B2 · Una suplantación de rol responde 500 en vez de 403 si el servicio no inicializa (menor, código)

`services/agent/app.py:60-64` (`_check_claims`) llama a `get_service().log_auth_failure(...)` **antes** de levantar el 403. Si `AgentService` no se puede construir (B1, cuota, red, etc.), la petición de suplantación devuelve 500. Además, la primera vez se quedó colgada unos 4 min mientras se importaba `langchain`/`google-genai` en una máquina con poca memoria. El contrato (`docs/CONTRACTS.md`) promete 403.

Para reproducirlo, con el servicio en el estado de B1: `POST /v1/chat` con token de `u_op_p1` y cuerpo `{"session_id":"adv2","plant_id":"P1","line_id":"P1-L1","message":"x","image_ids":[],"role":"plant_manager"}` → 500.

Arreglo sugerido: levantar primero el 403 y auditar en *best effort* (con `try/except` alrededor del log), o auditar sin depender del modelo LLM.

### B3 · `get_service()` reintenta construir todo en cada request cuando falla (menor)

`@lru_cache` no cachea excepciones. Mientras B1 persista, cada request vuelve a importar o construir el modelo, el checkpointer y el grafo, y vuelve a fallar. Lo correcto sería construir el servicio en el `lifespan` y fallar el arranque (o poner `/health` en `degraded`). Ahora mismo **`/health` dice `ok` aunque el agente no pueda responder ningún chat**, y eso hace que `run_local.sh` anuncie "agent listo" en falso.

### Observaciones de entorno

- La máquina estaba con **presión de memoria severa**: `vm_stat` daba unas 4 200 páginas libres (≈66 MB) y la carga media era de 5,5 a 8. Un segundo agente que levanté fuera del sandbox en `:8003` para intentar verificar los casos tardó más de 17 min sin terminar los imports (estaba bloqueado en lecturas de disco). Lo detuve sin llegar a usarlo y no hizo ninguna llamada al LLM.
- El primer `uv run python -m services.agent.plant.db` falló con `sqlite3.DatabaseError: file is not a database` en `PRAGMA journal_mode=WAL`. El reintento inmediato funcionó. Parece transitorio, coincidiendo con la escritura del arranque. Si se repite, conviene investigarlo.

## Estado final

- La BD de planta se resembró al inicio y al final (`metrics_hourly 78, camera_health 78, robot_events 49, tickets 4, review_queue 8, model_registry 7`), así que la demo arranca limpia. `audit_log` quedó vacío tras la resiembra.
- No se aprobó ninguna promoción ni se tocó `:8001`. No se reiniciaron los servicios de :8001/:8002 y no se modificó ningún archivo del repo salvo este informe.
- **Llamadas LLM usadas: 0** (ningún `/v1/chat` llegó al modelo).

## Siguiente paso recomendado

Relanzar el sistema desde una terminal normal (`make up` o `scripts/run_local.sh`, fuera del sandbox) y repetir esta verificación. El presupuesto de unas 14 llamadas `/v1/chat` sigue intacto. El script cliente usado está en el scratchpad de la sesión (`c.py`) y se puede reutilizar tal cual.
