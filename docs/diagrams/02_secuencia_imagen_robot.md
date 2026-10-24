# Diagrama 2 — Secuencia "imagen → robot" (ciclo en tiempo real)

Todo este ciclo es **determinista y corre en el borde** (PC industrial junto a la línea).
No hay LLM ni dependencia de internet en este camino. La nube solo recibe, después y de
forma asíncrona, la evidencia de los casos dudosos.

```mermaid
sequenceDiagram
    autonumber
    participant CAM as Cámara + iluminación
    participant EDGE as Visión en el borde<br/>(mismo código que el puerto 8001)
    participant QG as Compuerta de calidad
    participant CLS as Clasificador + ángulo
    participant DEC as Decisión
    participant PLC as PLC / controlador del robot
    participant RB as Brazo robótico
    participant Q as Cola de revisión<br/>(local, sincroniza a nube)
    participant H as Humano (calidad / supervisor)

    CAM->>EDGE: Trigger del sensor de presencia → captura (~10-20 ms)
    EDGE->>QG: Preprocesar (recorte ROI, normalización)
    alt Imagen no apta (oscura, borrosa, tamaño)
        QG-->>DEC: quality.ok = false
        DEC->>PLC: HOLD (reason: image_quality)
        PLC->>RB: No toma el objeto, lo desvía
        DEC->>Q: Encolar con evidencia (REJECT)
    else Imagen apta
        QG->>CLS: Extraer características + clasificar
        CLS->>CLS: Estimar ángulo + incertidumbre
        CLS->>DEC: label, confianza, ángulo, incertidumbre, ambigüedad
        alt Desconocido o baja confianza o ángulo dudoso o ambiguo
            DEC->>PLC: HOLD (reason: unknown_object / low_class_confidence / high_angle_uncertainty / rotational_ambiguity)
            PLC->>RB: No toma el objeto, lo desvía
            DEC->>Q: Encolar REVIEW o REJECT + vecinos similares
        else Confiable
            DEC->>PLC: PICK object=objN rotate_deg=-θ (OPC UA, ~5-20 ms)
            PLC->>RB: Tomar, compensar rotación, dejar
            DEC-->>Q: (solo muestra ~2% para auditoría)
        end
    end
    Note over CAM,PLC: Pipeline de visión medido p50 18,0 ms / p95 24,6 ms. Total hasta el PLC ≈ 33-65 ms<br/>(presupuesto < 100 ms) vs ciclo de ~6 s por objeto. El robot NUNCA recibe un ángulo dudoso.
    Q-->>H: Caso visible en la UI de revisión / en el chat del agente
    H->>Q: Etiqueta correcta + ángulo (o descarte) → dato para reentrenar
```

| Paso | Dónde | ¿Determinista? | Latencia estimada |
|---|---|---|---|
| Captura | Cámara | Sí | 10–20 ms (estimado: exposición + transferencia GigE/USB3) |
| Pipeline de visión completo (calidad + características + clase + ángulo + decisión) | PC de borde | Sí | **Medido (vision-1.0.1): p50 18,0 ms · p95 24,6 ms · media 16,8 ms** |
| Envío al PLC | Red OT local | Sí | 5–20 ms (estimado) |
| **Total hasta el PLC** | | | **≈ 33–65 ms; presupuesto < 100 ms** |

*Captura y PLC son estimaciones de diseño. El pipeline de visión está medido en la demo (CPU de portátil,
`docs/report/results.json` → `cost.latency`); hay que repetir la medición en el PC industrial del piloto.*
