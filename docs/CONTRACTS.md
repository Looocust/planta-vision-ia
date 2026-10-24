# Contratos entre componentes

Este documento fija las interfaces entre las piezas del sistema para que cada una pueda construirse y probarse por separado.

## Mapa del repositorio

| Ruta | Responsabilidad |
|---|---|
| `src/vision/` | Librería de visión: carga de datos, protocolos, características, modelos, ángulo, rechazo, inferencia |
| `scripts/train_vision.py` | Entrena y exporta el artefacto productivo en `models/vision-<version>/` |
| `notebooks/01_experimentacion.ipynb` | Notebook de punta a punta (datos → EDA → experimentos → selección → errores → exportación) |
| `services/vision_api/` | Servicio de inferencia FastAPI (puerto 8001), independiente del agente |
| `services/agent/` | Asistente agéntico LangGraph + API FastAPI + UI de chat mínima (puerto 8002) |
| `services/agent/plant/` | Backends simulados de planta (SQLite): métricas, cámara, robot, tickets, cola, aprobaciones, auditoría |
| `docs/sop/` | Procedimientos (SOP) y manuales simulados que el agente consulta con citas |
| `eval/agent/` | Set reproducible de ≥30 casos y runner de evaluación del agente |
| `configs/` | Configuración (umbrales, políticas, roles). Nada de secretos |
| `models/` | Artefactos versionados del modelo de visión |
| `docs/` | Arquitectura, diagramas, catálogo de tools, threat model, cloud (Azure/AWS), FinOps, reporte |

## Dataset

- Ubicación: `data/raw/coil-100/objN__A.png`, N ∈ 1..100, A ∈ {0,5,…,355}.
- SHA-256 del zip oficial: `fcb4b8b01c0778f416d8d7a09f45082c4f7075cc74743dcddd5c44307bd4afe7`.
- Etiquetas de clase: `obj1` … `obj100`. Semilla global: `42`.

## Servicio de visión (HTTP, puerto 8001)

### `GET /health`
`{"status": "ok", "model_version": "vision-1.0.1"}`

### `GET /v1/model`
Metadatos del artefacto: versión, fecha, hash de datos, clases conocidas, umbrales vigentes, métricas resumidas.

### `POST /v1/predict`
`multipart/form-data`: `file` (imagen PNG/JPG), opcionales `top_k` (int, por defecto 5) y `station_id` (str).

Respuesta 200:

```json
{
  "request_id": "uuid4",
  "model_version": "vision-1.0.1",
  "quality": {"ok": true, "issues": [], "brightness": 0.42, "sharpness": 812.3, "width": 128, "height": 128},
  "object": {"label": "obj42", "confidence": 0.93, "known": true},
  "angle": {"degrees": 135.0, "uncertainty_deg": 4.2, "ambiguous": false},
  "decision": "ACT",
  "reasons": [],
  "robot_command": {"action": "PICK", "object": "obj42", "rotate_deg": -135.0},
  "similar": [{"label": "obj42", "angle_deg": 130, "similarity": 0.97, "image_id": "obj42__130"}],
  "latency_ms": 12.3
}
```

- `decision` ∈ `ACT` (el robot puede actuar), `REVIEW` (objeto conocido pero la clase o el ángulo no son confiables → cola humana), `REJECT` (calidad de imagen insuficiente u objeto desconocido).
- Si `decision != "ACT"`: `robot_command = {"action": "HOLD", "reason": "<código>"}`; el robot nunca recibe un ángulo dudoso.
- `object.label` es `null` cuando el objeto se considera desconocido. `angle.degrees` es `null` cuando no se puede estimar.
- Códigos de `reasons`: `image_quality`, `unknown_object`, `low_class_confidence`, `high_angle_uncertainty`, `rotational_ambiguity`.
- Errores: 400 imagen inválida, 413 archivo > 5 MB, 415 tipo no soportado, 503 modelo no cargado.

### `GET /v1/images/{image_id}`
Devuelve la vista de referencia PNG (evidencia para el usuario).

### Administración (cabecera `X-Admin-Token`, valor en `.env`)
- `PUT /v1/admin/thresholds` cuerpo `{"min_class_confidence": float, "max_angle_uncertainty_deg": float, "unknown_distance": float}` (campos opcionales) → umbrales vigentes.
- `POST /v1/skus/stage` multipart: `sku` (str), `files[]`, `angles` (lista JSON de grados, misma longitud). Crea un modelo candidato en staging que incluye el SKU y devuelve `{"candidate_version", "sku", "n_views", "sufficient": bool, "issues": [], "metrics": {...}}`.
- `POST /v1/admin/promote` cuerpo `{"candidate_version": str}` → nueva versión activa.

### Inyección de fallas (solo si `ALLOW_FAULT_INJECTION=true`)
Cabecera `X-Fault: 500 | timeout` en `/v1/predict` para demostrar los escenarios adversos.

## Agente (HTTP, puerto 8002)

- `POST /v1/chat` `{"session_id", "plant_id", "line_id", "user_id", "role", "message", "image_ids": []}` → `{"reply", "tool_calls": [...], "pending_approval": {...} | null, "usage": {"input_tokens", "output_tokens", "cost_usd"}}`.
- `POST /v1/uploads` multipart `file` → `{"image_id"}` (las imágenes se guardan en `data/runtime/uploads/<plant>/<line>/`).
- `POST /v1/approvals/{approval_id}` `{"approver_id", "role", "decision": "approve" | "reject", "comment"}` → reanuda el flujo pausado.
- `GET /` → UI de chat mínima.

Roles: `operator`, `supervisor`, `quality`, `maintenance`, `ml_engineer`, `plant_manager`.

## Proveedor LLM

Configurable por entorno, nunca en código: `LLM_PROVIDER` (por defecto `google_genai`), `LLM_MODEL`, `GOOGLE_API_KEY`. Los precios por token para estimar costo se leen de `configs/llm_pricing.yaml`.
