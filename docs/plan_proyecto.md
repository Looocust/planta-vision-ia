# Plan de proyecto: de la prueba a la línea de producción

La prueba pide liderar **de punta a punta** la sustitución de una tarea manual crítica y **dirigir un equipo pequeño**. Este plan describe cómo pasar del MVP de este repositorio a una estación en producción, y después a varias. Lo guían dos principios:

1. **No se retira al operario hasta que los datos de planta lo justifiquen.** El 90,3% de automatización medido en COIL-100 es una hipótesis para la planta, no un hecho.
2. **Cada fase termina en un criterio go/no-go medible**, decidido con negocio, operación y seguridad.

## 1. Equipo (4–5 personas + apoyos de planta)

| Rol | Dedicación | Responsabilidad principal |
|---|---|---|
| **AI Engineer / Tech Lead** (candidato) | 100% | Arquitectura, decisiones técnicas, evaluación y criterios de aceptación; interlocución con negocio y operación; revisión de todo cambio de riesgo |
| **ML Engineer** | 100% | Datos de planta, etiquetado, entrenamiento, calibración, robustez, monitoreo de drift, alta de SKU |
| **Backend / MLOps Engineer** | 100% | Servicios, edge (IoT Edge), CI/CD, observabilidad, seguridad, despliegues canario y rollback |
| **Ingeniero de automatización (PLC/robot)** | 50% | Integración cámara–PLC–robot, calibración mano-ojo, protocolo HOLD, pruebas de seguridad de máquina |
| **QA / Operaciones (supervisor de línea)** | 25% | Casos reales, criterios operativos, validación del copiloto, capacitación de los turnos |
| Apoyos puntuales | según fase | Seguridad de la información, legal (datos y cámaras), finanzas (seguimiento FinOps) |

**Cómo dirijo al equipo:**
- **Ritmo:** sprints de 2 semanas, daily de 15 minutos y una demo al final de cada sprint ante supervisores de planta, no solo ante TI.
- **Definición de "terminado":**
  - Tests en verde.
  - Evaluación reproducible sin regresiones (visión por protocolo + set del agente).
  - Revisión cruzada por otra persona; los cambios de riesgo los revisan dos.
  - Documentación actualizada.
- **Decisiones técnicas:** se registran como ADR de una página (contexto, opciones, decisión, consecuencias).
- **Gates de calidad automáticos en CI:**
  - Un modelo no se promueve si empeora el error entre ACT o la falsa aceptación de desconocidos.
  - El agente no se despliega si baja el éxito del holdout o aparece una violación de seguridad.

## 2. Fases y cronograma (≈ 16 semanas hasta 1 estación en producción)

```
Semana   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15  16
F0 Descubrimiento  ███
F1 Datos de planta     ████████
F2 Integración edge            ████████
F3 Piloto en sombra                    ████████████
F4 Canario asistido                                ████████
F5 Producción 1 estación                                   ████ → escalar
```

| Fase | Qué se hace | Entregable | Criterio go/no-go |
|---|---|---|---|
| **F0 Descubrimiento** (sem 1–2) | Medir la línea actual: throughput, errores de empaque, agarres fallidos, costos reales. Inventario de SKU (cuántos son simétricos o casi idénticos). Requisitos de seguridad de máquina. | Línea base firmada por operación y finanzas; supuestos del Excel FinOps reemplazados por datos reales | Hay caso de negocio con datos reales (no supuestos) |
| **F1 Datos de planta** (sem 2–5) | Cámara e iluminación definitivas; capturar vistas por SKU en la estación; etiquetado asistido; anonimización en el edge (personas en el encuadre) | Dataset de planta versionado; modelo re-entrenado y evaluado con los mismos protocolos A–D más robustez | Error entre ACT ≤ 2% (IC95) y falsa aceptación de desconocidos ≤ 5% **con datos de planta** |
| **F2 Integración edge** (sem 5–8) | Despliegue en el PC industrial; integración con PLC/robot; calibración mano-ojo; protocolo HOLD; pruebas de corte de red | Lazo imagen → robot funcionando en banco de pruebas, < 100 ms p95 | 0 comandos al robot con decisión distinta de ACT en pruebas de falla; la línea funciona sin internet |
| **F3 Piloto en sombra** (sem 8–12) | El sistema predice en paralelo, pero **el operario sigue haciendo la tarea**. Se comparan predicción y realidad en cada objeto. El copiloto se usa con supervisores reales. | Informe de sombra: cobertura ACT real, error entre ACT, causas de REVIEW, costo real | Cobertura ACT real ≥ **% de equilibrio + 5 puntos** (ver FinOps) y error entre ACT dentro de la tolerancia |
| **F4 Canario asistido** (sem 12–14) | El robot actúa con las decisiones ACT del sistema en **una** estación y **un** turno; el operario pasa a atender la cola de revisión | Métricas de calidad y throughput frente a la línea base | Calidad y throughput ≥ línea base durante 2 semanas; 0 incidentes de seguridad |
| **F5 Producción** (sem 14–16) | Tres turnos; el operario se reubica en revisión, calidad o mantenimiento; monitoreo y alertas | Runbook de operación, SOP actualizados, capacitación | Payback proyectado con datos reales; aprobación de negocio para escalar |
| **Escalado** | Estación por estación, con el mismo ciclo sombra → canario | Plantilla de despliegue por planta ("stamp" en Azure) | Cada estación pasa su propio piloto en sombra |

## 3. Riesgos principales y mitigación

| Riesgo | Prob. | Impacto | Mitigación | Señal temprana |
|---|---|---|---|---|
| La cobertura ACT real queda por debajo del equilibrio (80–86%) | Media | Alto: el proyecto no ahorra | Piloto en sombra antes de retirar al operario; mejorar iluminación y vistas por SKU; dato complementario (código de barras/peso) para SKU problemáticos | Cobertura en sombra por semana |
| SKU casi idénticos (como obj61/obj92) | Alta | Alto: producto equivocado | Lista de SKU "confundibles" que siempre van a REVIEW o se verifican con código de barras | Matriz de confusión por SKU |
| Productos simétricos | Alta | Medio | Marcar el SKU como "agarre independiente del ángulo" cuando el robot lo permita | % de REVIEW por `rotational_ambiguity` |
| Drift (iluminación, suciedad de lente, empaques nuevos) | Alta | Medio | Compuerta de calidad, monitoreo de distancia de novedad, SOP de limpieza con recalibración, alertas | Subida de REJECT por `image_quality` |
| Rechazo del cambio por parte de operarios | Media | Alto | Involucrar a los turnos desde F0; el operario pasa a un rol de revisión y calidad, sin despidos sorpresa | Uso real del copiloto, feedback en demos |
| Seguridad (prompt injection, accesos) | Media | Alto | El LLM nunca actúa sin política en código y aprobación; Entra ID; threat model revisado en cada fase | Eventos de auditoría bloqueados |
| Costo del LLM | Baja | Bajo | Modelo pequeño por defecto, presupuesto de tokens por turno, showback por planta | Costo por conversación |

## 4. KPIs que se reportan a negocio cada semana

- **Cobertura ACT** (% de objetos que el robot maneja solo) y su distancia al punto de equilibrio.
- **Error entre ACT** (producto equivocado o agarre fuera de ±10°), con intervalo de confianza.
- **Throughput** y **paradas de línea** frente a la línea base.
- **Tiempo medio en cola de revisión** y casos por turno.
- **Costo total mensual** (nube + edge + humano residual) frente a la operación actual.
- **Copiloto:** consultas por turno, % resueltas sin escalar, tickets creados y aprobaciones de riesgo.

## 5. Qué pasa del MVP a producción (backlog priorizado)

1. Identidad real con Entra ID (OIDC) en lugar del login de demo; revocación de tokens.
2. Despliegue edge con IoT Edge y rollback atómico del modelo; inferencia en el PC industrial.
3. Re-entrenamiento con datos de planta y la misma batería de evaluación (protocolos + robustez).
4. Segunda compuerta para SKU casi idénticos (margen entre clases o verificación por código de barras).
5. Persistencia en PostgreSQL con checkpointer de LangGraph y auditoría inmutable.
6. Monitoreo de drift y alertas; tablero operativo por línea.
7. Evaluación continua del agente en CI (set principal + holdout + adversos) con repeticiones para medir varianza.
