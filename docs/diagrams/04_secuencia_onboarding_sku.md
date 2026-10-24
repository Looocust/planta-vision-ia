# Diagrama 4 — Alta de un SKU nuevo (staging → métricas → aprobación → promoción)

```mermaid
sequenceDiagram
    autonumber
    actor Q as Calidad (role=quality)
    participant AG as Agente
    participant VIS as API de visión
    participant STG as Modelo candidato<br/>(staging)
    participant POL as Política + aprobaciones
    actor ML as ML Engineer (role=ml_engineer)
    participant REG as Registro de modelos<br/>(Azure ML / SageMaker en nube)
    participant EDGE as Borde (canario → todas)
    participant AUD as Auditoría

    Q->>AG: "Alta del SKU obj101" + fotos con su ángulo
    AG->>VIS: POST /v1/skus/stage (sku, files[], angles)
    VIS->>STG: Reentrenar con el SKU nuevo (minutos, CPU)
    STG-->>VIS: candidate_version, n_views, sufficient, issues, metrics
    alt Vistas insuficientes o métricas por debajo del umbral
        VIS-->>AG: sufficient=false + issues (p. ej. faltan ángulos 180-355)
        AG-->>Q: Qué fotos faltan y por qué. Nada se promueve
    else Candidato válido
        VIS-->>AG: métricas: exactitud global, del SKU nuevo, regresión en SKUs existentes
        AG->>POL: Solicitar promoción (riesgo ALTO)
        POL-->>AG: Requiere ml_engineer o plant_manager, ≠ solicitante
        AG-->>Q: Pendiente de aprobación con tabla de métricas
        ML->>POL: Aprobar tras revisar métricas y regresión
        POL->>VIS: POST /v1/admin/promote candidate_version
        VIS->>REG: Registrar versión (linaje: datos, hash, métricas)
        REG->>EDGE: Despliegue por anillos: canario 1 estación (modo sombra → activo)
        EDGE-->>REG: Telemetría: % ACT, % REVIEW, latencia vs versión anterior
        alt Degradación en canario
            REG->>EDGE: Rollback automático a la versión previa
        else Sano tras ventana de observación
            REG->>EDGE: Desplegar al resto de estaciones
        end
        VIS->>AUD: model_promoted (versión, aprobador, métricas)
    end
```

En la demo los pasos de staging, aprobación y promoción son reales (endpoints de la API de visión);
el registro en la nube y el despliegue por anillos al borde son el diseño de producción
(ver `docs/cloud/azure_architecture.md` §MLOps).
