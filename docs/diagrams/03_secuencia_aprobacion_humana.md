# Diagrama 3 — Flujo agéntico complejo con aprobación humana

Ejemplo: un técnico de mantenimiento reporta muchos casos en REVIEW en la línea 2. El agente
diagnostica, cita el SOP y **propone** un cambio de umbral (o un rollback de modelo). Como es una
operación de riesgo, el grafo se pausa (`interrupt`) hasta que un supervisor **distinto** de quien
lo pidió la apruebe. Solo entonces un ejecutor determinista la aplica y la audita.

```mermaid
sequenceDiagram
    autonumber
    actor M as Técnico de mantenimiento<br/>(role=maintenance)
    participant AG as Agente LangGraph
    participant LLM as LLM
    participant T as Tools de lectura<br/>(métricas, cola, cámara, modelo)
    participant SOP as Recuperación de SOP
    participant POL as Política determinista<br/>(RBAC + riesgo)
    participant AP as Aprobaciones
    actor S as Supervisor<br/>(role=supervisor)
    participant EX as Ejecutor determinista
    participant VIS as API de visión (admin)
    participant AUD as Auditoría (append-only)

    M->>AG: "La línea 2 manda muchos objetos a revisión desde las 14:00, ¿qué pasa?"
    AG->>LLM: Mensaje + catálogo de tools (filtrado por rol)
    LLM-->>AG: Plan: consultar métricas, cola y estado de cámara
    AG->>T: get_line_metrics, get_review_queue, get_camera_status
    T-->>AG: REVIEW 18% (normal 4%), motivo dominante high_angle_uncertainty, brillo -30%
    AG->>SOP: buscar "incertidumbre de ángulo exposición cámara umbral"
    SOP-->>AG: SOP-CAM-01 [CAM-01.4] síntomas de deriva de exposición y SOP-ML-04 [ML-04.4] cambio de umbrales
    AG->>LLM: Evidencia + fragmentos SOP
    LLM-->>AG: Diagnóstico + propuesta: ticket de iluminación y subir max_angle_uncertainty_deg 10→12 por 8 h
    AG->>T: create_ticket (bajo riesgo, permitido a maintenance)
    T-->>AG: TCK-1042
    AG->>POL: ¿update_thresholds por maintenance?
    POL-->>AG: Riesgo ALTO → requiere aprobación de supervisor o ml_engineer
    AG->>AP: Crear solicitud (requester=M, diff de umbrales, justificación, SOP citados)
    AG->>AUD: approval_requested
    AG-->>M: Diagnóstico con citas + TCK-1042 + "cambio pendiente de aprobación APR-77"
    Note over AG: Grafo en pausa (interrupt). Estado guardado en checkpoint.

    S->>AP: POST /v1/approvals/APR-77 decision=approve
    AP->>POL: Validar: rol aprobador permitido y approver ≠ requester
    alt Aprobador inválido (mismo usuario o rol sin permiso)
        POL-->>AP: Rechazado
        AP->>AUD: approval_denied_policy
    else Válido
        POL-->>AP: OK
        AP->>AG: Reanudar grafo desde checkpoint
        AG->>EX: Ejecutar exactamente el diff aprobado (sin pasar por el LLM)
        EX->>VIS: PUT /v1/admin/thresholds (X-Admin-Token del servicio)
        VIS-->>EX: Umbrales vigentes
        EX->>AUD: action_executed (antes/después, aprobador, requester, ticket)
        AG-->>M: "Aplicado por aprobación de S. Revertir a las 22:00 (programado)"
        AG-->>S: Confirmación con enlace a la auditoría
    end
```

**Puntos a defender**
- El LLM nunca llama directamente a la acción riesgosa: la política es código, no prompt.
- El ejecutor aplica *el diff aprobado*, no una nueva generación del LLM (evita que lo aprobado y lo ejecutado difieran).
- Separación de funciones: quien pide no aprueba. Todo queda en una auditoría de solo inserción.
- Variante rollback de modelo: el mismo flujo con `POST /v1/admin/promote` hacia la versión anterior (SOP-ML-04 [ML-04.2]).
- Los SOP citados existen en `docs/sop/`; las cifras del ejemplo (18%, 14:00, TCK-1042, APR-77) son ilustrativas.
