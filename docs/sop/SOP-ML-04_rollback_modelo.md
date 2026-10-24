# SOP-ML-04 — Gestión de versiones del modelo de visión y rollback

Versión 1.1 · Dueño: Ingeniería ML + Calidad.

## [ML-04.1] Despliegues canary
Toda versión nueva se despliega primero como `canary` en una sola línea durante al menos un turno completo. Se comparan rechazos, revisiones y confianza media contra la línea de control.

## [ML-04.2] Criterios de rollback
Hacer rollback si en la línea canary, durante 2 horas consecutivas: tasa de rechazos > 2× la línea de control o confianza media cae > 10 puntos. Antes de culpar al modelo, descartar causas de captura (cámara, iluminación: ver CAM-01.4), porque un cambio de distribución de imagen afecta a cualquier versión.

## [ML-04.3] Promoción a producción
Solo se promueve una versión `staging` con accuracy ≥ 0.90 y sin caída > 1 punto frente a producción, con aprobación explícita de un rol autorizado distinto del solicitante.

## [ML-04.4] Cambio de umbrales
Los umbrales (`min_class_confidence`, `max_angle_uncertainty_deg`) no deben bajarse para "ocultar" rechazos. Cualquier cambio requiere aprobación de Calidad y queda auditado.
