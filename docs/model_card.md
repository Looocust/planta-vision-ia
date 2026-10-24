# Model card — vision-1.0.0

*Generada automáticamente por `scripts/build_vision_report.py` el 2026-10-24 a partir de `docs/report/results.json` y `models/vision-1.0.0/metadata.json`.*

## Uso previsto
Identificar el objeto (SKU) de una imagen de estación, estimar su ángulo de rotación en el plano [0, 360) con un score de incertidumbre en grados, y devolver las vistas de referencia más parecidas como evidencia. La salida incluye una decisión **ACT / REVIEW / REJECT**: el robot solo recibe un comando `PICK` con ángulo cuando la decisión es ACT; en cualquier otro caso recibe `HOLD` y el caso va a cola humana. ACT no garantiza acierto: el riesgo residual medido se reporta abajo.

## Modelo (receta congelada)
- Receta `classic-logreg-v1` (`src/vision/recipe.py`): HSV(16x4x4)+HOG(64px,9or,8x8)+LBP(u8,2x2)+Hu → StandardScaler → PCA(150) → `logreg` {"C": 1.0}. Clasificador elegido en la exploración por exactitud media de **validación**.
- Ángulo: recuperación condicionada al objeto; k de interpolación elegido en validación (artefacto: k = 1).
- Score de incertidumbre angular: separación angular equivalente a la distancia al vecino más cercano según la curva distancia-vs-ángulo del objeto. Es un **score heurístico en grados, no una cota del error**; su umbral se elige para que P(clase incorrecta o |error| > 10° | ACT) ≤ 2 % en validación.
- Novedad: distancia al vecino más cercano en el espacio PCA.
- Umbrales del artefacto: confianza ≥ 0.99, score de incertidumbre ≤ 30.0°, distancia de desconocido ≤ 19.47; elegidos en validación (90 conocidos paso 10° + 10 pseudo-desconocidos): 2901 ACT, error 0.72% (IC95 [0.47, 1.10]%). Esa validación comparte imágenes con el test del protocolo A, por lo que **no** es una estimación independiente. Ajustables en caliente vía `PUT /v1/admin/thresholds` (validados: finitos y en rango).
- Clases marcadas con ambigüedad rotacional (→ REVIEW): obj47, obj67, obj84, obj94, obj95, obj98.
- Artefacto: 9.4 MB, SHA-256 `19cbc8a4516b0ecb…`, commit `3aa0bbbfc3` (árbol con cambios sin commit: True); latencia p50 ≈ 13.3 ms/imagen en CPU.

## Datos
- COIL-100 (Columbia): 100 objetos × 72 vistas (paso 5°), 128×128 RGB, fondo oscuro y luz controlada. Hash de versión de datos: `e9e73124fbf0e964…`. Semilla 42.
- El artefacto usa las 7.200 vistas, así que **no tiene test propio**: sus métricas esperadas son las de la receta, evaluada en cada protocolo con test independiente (tabla siguiente).

## Métricas de la receta (test independiente por protocolo, una evaluación)

| Protocolo | Exactitud | MAE ángulo | ≤10° | Éxito e2e | % ACT | n ACT | Error entre ACT (IC95) |
|---|---|---|---|---|---|---|---|
| A · Interpolación | 100.00% | 2.71° | 98.1% | 98.1% | 92.2% | 3318 | 0.21% [0.10, 0.43]% |
| B · Sector ciego 60° | 97.00% | 66.47° | 28.6% | 27.8% | 0.0% | 0 | N/A N/A |
| C · Escasez (12 vistas) | 99.12% | 13.13° | 70.5% | 69.8% | 0.0% | 0 | N/A N/A |
| D estricto · desconocidos | N/A | — | N/A | N/A | 4.9% | 35 | 100.00% [90.11, 100.00]% |
| D variante · mixto | 100.00% | 3.07° | 98.4% | 98.4% | 77.0% | 3051 | 0.66% [0.42, 1.01]% |

Desconocidos — D estricto (90 conocidos con todas sus vistas; test = 720 vistas de 10 desconocidos): rechazados 77.5% (IC95 [74.31, 80.40]%), llegan a ACT 4.86% (IC95 [3.52, 6.69]%). D variante (mixto): AUROC combinado 0.993, conocidos rechazados 1.51%.

*Éxito e2e* = clase correcta y error angular circular ≤ 10° (tolerancia de agarre). *Error entre ACT* = fracción de acciones del robot que serían empaque equivocado, objeto desconocido aceptado o agarre fallido. N/A = la receta no autorizó ninguna acción (automatización desactivada por falta de evidencia en validación).

## Limitaciones conocidas
- Dataset de laboratorio: fondo oscuro uniforme, iluminación fija, cámara fija, un solo eje de rotación, sin oclusión, sin desenfoque por movimiento, sin variación de escala. El desempeño en planta **no** está demostrado.
- Objetos rotacionalmente simétricos o con vistas opuestas parecidas: el ángulo es intrínsecamente ambiguo; el sistema los envía a REVIEW.
- Ángulos nunca vistos (protocolo B, arco ciego de 60°) y pocas vistas por SKU (protocolo C, 12 vistas): la receta desactiva la automatización (todo a REVIEW).
- Desconocidos casi duplicados de un conocido (misma familia de empaque, p. ej. obj61 ≈ obj92) pueden aceptarse como el conocido y llegar a ACT.
- El score de incertidumbre angular no es una probabilidad ni una cota; solo su umbral está validado empíricamente.

## Cuándo NO usarlo
- Con cámaras, fondos o iluminación distintos sin re-captura de referencias y recalibración de umbrales.
- Para rotaciones fuera del plano (vuelcos), objetos parcialmente ocultos o varios objetos en el cuadro.
- Como única barrera de seguridad: el robot debe mantener sus propias verificaciones (peso, código de barras, sensores de agarre).
- Para SKUs nuevos sin pasar por staging (`POST /v1/skus/stage`: ≥ 12 vistas cubriendo ≥ 300°, métricas leave-one-out) y promoción explícita.

## Recomendaciones para planta
Piloto en sombra (el robot no actúa; 100 % revisión humana) para recalibrar umbrales con datos reales; aumentación (brillo, blur, ruido, escala) y captura de referencias por estación; monitoreo de la tasa ACT/REVIEW/REJECT, de la distancia de novedad y de las razones de REVIEW; re-entrenamiento versionado con hash de datos, SHA del artefacto y registro en MLflow.
