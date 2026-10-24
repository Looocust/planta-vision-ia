# Auditoría de cumplimiento independiente (Claude, contexto limpio)

Un revisor sin acceso al proceso de construcción contrastó el repositorio completo con `prueba_tecnica.md`, verificando las afirmaciones con comandos (tests, notebook, PDF, JSON de resultados, secretos en el historial y servicios en ejecución).

**Veredicto:** cumplimiento sustancial, ≈ 90% con evidencia verificable.

**Verificado:**
- 86 tests en verde.
- Notebook con 15/15 celdas ejecutadas y 0 errores.
- PDF de 10 páginas con resultados por protocolo, análisis de errores y model card.
- 0 secretos en el historial de git y `.env` ignorado.
- Servicios sanos: las imágenes de demo devuelven ACT, REVIEW y REJECT como se espera.

**Único requisito faltante (❌):** el plan de proyecto y la dirección de equipo, que la ficha pide como "Resultado". Se resolvió con `docs/plan_proyecto.md`.

**Parciales y mejoras señaladas (⚠️), con la acción tomada:**

| Hallazgo | Acción |
|---|---|
| El notebook no embebe las figuras | Figuras inline y re-ejecución |
| Re-ejecutar el notebook sobrescribía la versión publicada | El export escribe en una versión de desarrollo |
| Artefactos de MLflow repartidos en dos carpetas | Raíz de artefactos absoluta |
| Comparación con ResNet18 con umbrales no recalibrados | Recalibración en su propia validación |
| SKU casi idénticos (obj61/obj92) por encima del criterio del 5% | Segunda compuerta evaluada |
| Caso de negocio apoyado en la cifra más favorable | Se presenta como rango (73,7–90,3%) |
| Inconsistencias de documentos: commit del artefacto en PDF y model card, "13 tools", `vision-1.0.0`, cobertura 42/43 | Corregidas |
| Adversos guionados y prueba de visión 5xx/timeout solo sobre el stub | Integración contra el servicio real y aclaración de qué casos son guionados |
| Catálogo de tools sin tabla por tool de permisos y errores | Tabla añadida |

Las correcciones están en los commits posteriores a este documento.
