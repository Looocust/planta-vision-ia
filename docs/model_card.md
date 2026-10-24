# Model card — vision-1.0.0

*Generada automáticamente por `scripts/build_vision_report.py` el 2026-10-24 a partir de `docs/report/results.json` y `models/vision-1.0.0/metadata.json`.*

## Uso previsto
Identificar el objeto (SKU) de una imagen de estación, estimar su ángulo de rotación en el plano [0, 360) con una medida de incertidumbre en grados, y devolver las vistas de referencia más parecidas como evidencia. La salida incluye una decisión **ACT / REVIEW / REJECT**: el robot solo recibe un comando `PICK` con ángulo cuando la decisión es ACT; en cualquier otro caso recibe `HOLD` y el caso va a cola humana.

## Modelo
- Familia: clásico: HSV+HOG+LBP+Hu → estandarización → PCA → logreg (ML clásico; sin redes profundas en producción).
- Clasificador: `logreg` {"C": 1.0}, elegido por exactitud media de **validación** en los 4 protocolos (nunca con test).
- Ángulo: recuperación condicionada al objeto (vecino más cercano + media circular ponderada de 2 vecinos); incertidumbre = separación angular equivalente a la distancia al vecino más cercano según la curva distancia-vs-ángulo del propio objeto.
- Novedad: distancia al vecino más cercano en el espacio PCA.
- Umbrales vigentes: confianza mínima 0.99, incertidumbre angular máxima 30.0°, distancia de desconocido 19.47 (elegidos en validación con 10 objetos pseudo-desconocidos; error objetivo entre ACT ≤ 2 %). Ajustables en caliente vía `PUT /v1/admin/thresholds`.
- Clases marcadas como rotacionalmente simétricas (→ REVIEW): obj47, obj67, obj84, obj94, obj95, obj98.
- Tamaño del artefacto: 9.4 MB; latencia p50 ≈ 14.8 ms/imagen en CPU.

## Datos
- COIL-100 (Columbia): 100 objetos × 72 vistas (paso 5°), 128×128 RGB, fondo oscuro y luz controlada. Hash de versión de datos: `e9e73124fbf0e964…`. Semilla 42.
- El modelo productivo se entrena con las 7.200 vistas; las métricas provienen de los protocolos A–D (abajo).

## Métricas (test, una sola evaluación por protocolo)

| Protocolo | Exactitud | MAE ángulo | ≤10° | Éxito e2e | % ACT | Error entre ACT |
|---|---|---|---|---|---|---|
| A · Interpolación | 99.97% | 2.71° | 98.1% | 98.1% | 92.1% | 0.21% |
| B · Sector ciego 60° | 97.00% | 67.47° | 16.9% | 16.4% | 0.0% | 0.00% |
| C · Escasez (12 vistas) | 99.12% | 9.39° | 89.9% | 89.1% | 0.0% | 0.00% |
| D · Objeto desconocido | 100.00% | 2.63° | 98.5% | 98.5% | 76.3% | 1.46% |

Desconocidos (D): AUROC distancia 0.980, confianza 0.989, combinado 0.991; desconocidos rechazados 71.2%, desconocidos que llegan a ACT 5.14%, conocidos rechazados 1.51%.

*Éxito e2e* = clase correcta y error angular circular ≤ 10° (tolerancia de agarre). *Error entre ACT* = fracción de acciones del robot que serían empaque equivocado o agarre fallido.

## Limitaciones conocidas
- Dataset de laboratorio: fondo oscuro uniforme, iluminación fija, cámara fija, un solo eje de rotación, sin oclusión, sin desenfoque por movimiento, sin variación de escala. El desempeño en planta **no** está demostrado.
- Objetos rotacionalmente simétricos o con vistas opuestas parecidas: el ángulo es intrínsecamente ambiguo; el sistema los envía a REVIEW.
- Ángulos nunca vistos (protocolo B, arco ciego de 60°): la recuperación no puede extrapolar; casi todo va a REVIEW y la automatización se desactiva si la validación no alcanza el objetivo de error.
- Pocas vistas por SKU (protocolo C, 12 vistas): la clase es fiable pero el ángulo no alcanza la confiabilidad exigida para ACT.
- La novedad se mide por distancia en el espacio de características: un objeto desconocido muy parecido a uno conocido (misma familia de empaque) puede aceptarse como conocido.

## Cuándo NO usarlo
- Con cámaras, fondos o iluminación distintos sin re-captura de referencias y recalibración de umbrales.
- Para rotaciones fuera del plano (vuelcos), objetos parcialmente ocultos o varios objetos en el cuadro.
- Como única barrera de seguridad: la decisión ACT reduce pero no elimina el error; el robot debe mantener sus propias verificaciones (peso, código de barras, sensores de agarre).
- Para SKUs nuevos sin pasar por staging (`POST /v1/skus/stage`: ≥ 12 vistas cubriendo ≥ 300°, métricas leave-one-out) y promoción explícita.

## Recomendaciones para planta
Piloto en sombra (el robot no actúa; 100 % revisión humana) para recalibrar umbrales con datos reales; aumentación (brillo, blur, ruido, escala) y captura de referencias por estación; monitoreo de la tasa ACT/REVIEW/REJECT, de la distancia de novedad y de las razones de REVIEW; re-entrenamiento versionado con hash de datos y registro en MLflow.
