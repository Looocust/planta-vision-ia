# Re-auditoría Reto 3 (parcial)

La re-auditoría de Codex sobre el agente endurecido se interrumpió porque Codex alcanzó su límite de uso antes de emitir el informe final. Este documento transcribe sus conclusiones intermedias, tomadas de su log de ejecución, para dejar trazabilidad.

**Corregido según Codex:**
- La aprobación sin token o con identidad declarada queda bloqueada.
- El LLM ya no puede escribir en el dataset de reentrenamiento sin confirmación humana.
- Las cifras de evaluación coinciden con los JSON de resultados: 38/43 en el set principal y 11/11 en el holdout.

**Hallazgos pendientes señalados:**
1. El login de demo permite obtener cualquier identidad (por diseño de la demo; debe documentarse y quedar deshabilitado por defecto).
2. Faltan checks de regresión sobre clases existentes al promover un SKU.
3. Con llamadas a tools en paralelo, una segunda tool de escritura puede ejecutarse después de alcanzado el límite del turno.
4. La cobertura de checks de la evaluación cuenta expectativas vacías.
5. Aislamiento incompleto: P2 puede inspeccionar con la instancia de visión vinculada a P1.
6. La URL de evidencia de una imagen cargada devuelve 422 porque omite un parámetro requerido.

Las correcciones de estos puntos están en el historial de commits posterior a este documento.
