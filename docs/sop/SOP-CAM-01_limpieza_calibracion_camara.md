# SOP-CAM-01 — Limpieza y calibración de cámaras de visión

Versión 3.2 · Dueño: Mantenimiento + Calidad · Aplica a: CAM-*-A de todas las líneas de picking.

## [CAM-01.1] Alcance y frecuencia
La lente de la cámara se limpia cada 7 días o cuando el indicador `focus_score` cae por debajo de 0.75. La limpieza se registra como evento `camera_cleaning` en la bitácora de línea.

## [CAM-01.2] Procedimiento de limpieza
1. Pausar la línea según SOP-LIN-01 o esperar ventana sin producción.
2. Limpiar la lente con paño de microfibra y alcohol isopropílico al 70 %. No usar aire comprimido directo.
3. Verificar que la carcasa quede sellada y sin huellas.

## [CAM-01.3] Recalibración obligatoria posterior a la limpieza
Tras cualquier limpieza se DEBE recalibrar la exposición con la carta gris de referencia:
- Exposición nominal: 8.0 ms ± 0.5 ms.
- Brillo medio esperado de la imagen (`brightness_mean`): 0.35 – 0.60.
Si la limpieza se hace sin recalibrar, el auto-exposición puede derivar (p. ej. a 12–13 ms) y producir imágenes sobreexpuestas que reducen la confianza del modelo y aumentan rechazos y casos de revisión.

## [CAM-01.4] Síntomas de deriva de exposición
- `brightness_mean` > 0.60 sostenido por más de 1 hora.
- Aumento de rechazos por `image_quality` o caída de la confianza media del modelo > 10 puntos.
- Estado de cámara `warning` en el monitor de salud.
Acción: crear ticket de categoría `vision` con severidad `high` y ejecutar CAM-01.3. No cambiar umbrales del modelo para compensar una deriva de cámara.

## [CAM-01.5] Validación
Tras recalibrar, inspeccionar 20 piezas de referencia; la tasa de decisiones `ACT` debe ser ≥ 95 %.
