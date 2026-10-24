# Model card — vision-1.0.1

*Generada automáticamente por `scripts/build_vision_report.py` el 2026-10-24 a partir de `docs/report/results.json` y `models/vision-1.0.1/metadata.json`.*

## Uso previsto
Identificar el objeto (SKU) de una imagen de estación, estimar su ángulo de rotación en el plano [0, 360) con un score de incertidumbre en grados, y devolver las vistas de referencia más parecidas como evidencia. La salida incluye una decisión **ACT / REVIEW / REJECT**: el robot solo recibe un comando `PICK` con ángulo cuando la decisión es ACT; en cualquier otro caso recibe `HOLD` y el caso va a cola humana. ACT no garantiza acierto: el riesgo residual observado se reporta abajo.

## Modelo desplegado
- Configuración `classic-logreg-v1` (`src/vision/recipe.py`): preprocesado (mediana 3×3 + máscara (Gauss 5×5, gris>40, apertura 3×3) + fondo a 0) → HSV(16x4x4)+HOG(64px,9or,8x8)+LBP(u8,2x2)+Hu → StandardScaler → PCA(150) → `logreg` {"C": 1.0}.
- Aumentación: 1 copia por vista de train: ruido σ~U(0,6), brillo ×U(0.8,1.2), blur σ~U(0.5,1) con p=0.3; validación = vistas limpias + 1 copia perturbada con la misma distribución.
- Ángulo: recuperación condicionada al objeto; k de interpolación elegido en validación (artefacto: k = 3).
- Score de incertidumbre angular: separación angular equivalente a la distancia al vecino más cercano según la curva distancia-vs-ángulo del objeto. Es un **score heurístico en grados, no una cota del error**; su umbral se elige para que el riesgo observado en validación P(clase incorrecta o |error| > 10° | ACT) sea ≤ 2 %.
- Umbrales: confianza ≥ 0.99, score ≤ 30.0°, distancia de desconocido ≤ 19.84. Validación de producción (90 conocidos paso 10° + 10 pseudo-desconocidos, vistas limpias + perturbadas): 5693 ACT, error 1.81% (IC95 [1.49, 2.19]%); pseudo-desconocidos que llegan a ACT 4.10% (criterio ≤ 5 %). Esta validación comparte imágenes con tests de los protocolos, así que no es una estimación independiente.
- Clases con ambigüedad rotacional (→ REVIEW): obj35, obj47, obj67, obj84, obj94, obj95, obj98.
- Artefacto: 17.59 MB, SHA-256 `4fce6c32cb4abe36…`, snapshot de código `5877577c981c4e46…` (incluido como code_snapshot.tar.gz), commit `4d6b14902c` (árbol sucio: True); latencia p50 ≈ 18.0 ms/imagen en CPU.

## Datos
- COIL-100: 100 objetos × 72 vistas (paso 5°), 128×128 RGB, fondo oscuro y luz controlada. Hash de datos `e9e73124fbf0e964…`. Semilla 42.
- El artefacto usa las 7.200 vistas (+1 copia aumentada por vista) y **no tiene test propio**.

## Métricas independientes (selección anidada por protocolo, test evaluado una vez)

Familia, hiperparámetros, k y umbrales se eligen solo con la validación interna del train de cada protocolo.

| Protocolo | Modelo elegido | Exactitud | MAE ángulo | Éxito e2e | % ACT | n ACT | Error entre ACT (IC95) |
|---|---|---|---|---|---|---|---|
| A · Interpolación | svm_rbf:{'C': 10.0} | 100.00% | 2.91° | 98.5% | 90.3% | 3251 | 0.09% [0.03, 0.27]% |
| B · Sector ciego 60° | logreg:{'C': 1.0} | 96.17% | 65.52° | 28.5% | 0.0% | 0 | N/A N/A |
| C · Escasez (12 vistas) | logreg:{'C': 0.1} | 98.78% | 13.01° | 69.7% | 0.0% | 0 | N/A N/A |
| D estricto · desconocidos | knn:{'n_neighbors': 1} | N/A | — | N/A | 12.6% | 91 | 100.00% [95.95, 100.00]% |
| D variante · mixto | knn:{'n_neighbors': 1} | 99.63% | 2.74° | 98.3% | 74.9% | 2967 | 2.56% [2.05, 3.19]% |

## Métricas de la configuración desplegada (con salvedad)

Familia/C de la configuración desplegada se fijaron con validaciones agregadas de A–D que, en conjunto, se solapan con tests de otros protocolos: estas cifras **no** son independientes.

| Protocolo | Exactitud | MAE ángulo | % ACT | n ACT | Error entre ACT (IC95) |
|---|---|---|---|---|---|
| A · Interpolación | 100.00% | 2.91° | 90.3% | 3250 | 0.09% [0.03, 0.27]% |
| B · Sector ciego 60° | 96.17% | 65.52° | 0.0% | 0 | N/A N/A |
| C · Escasez (12 vistas) | 98.83% | 13.01° | 0.0% | 0 | N/A N/A |
| D estricto · desconocidos | N/A | — | 6.4% | 46 | 100.00% [92.29, 100.00]% |
| D variante · mixto | 100.00% | 2.90° | 73.7% | 2917 | 0.93% [0.64, 1.34]% |

Desconocidos (selección anidada) — D estricto: rechazados 71.2%, llegan a ACT 12.64% (IC95 [10.41, 15.27]%). D variante: AUROC combinado 0.975, conocidos rechazados 1.79%.

## Robustez a perturbaciones (configuración desplegada, test de A)

| Perturbación | Exactitud | MAE ángulo | % ACT | Error entre ACT (IC95) |
|---|---|---|---|---|
| limpia | 100.00% | 2.91° | 90.3% | 0.09% [0.03, 0.27]% |
| ruido σ=2 | 100.00% | 3.15° | 90.3% | 0.15% [0.07, 0.36]% |
| ruido σ=4 | 100.00% | 3.97° | 89.8% | 1.11% [0.81, 1.54]% |
| ruido σ=8 | 99.97% | 6.12° | 85.5% | 2.40% [1.92, 3.01]% |
| brillo ×0.7 | 99.94% | 7.67° | 80.6% | 1.48% [1.10, 1.99]% |
| brillo ×1.3 | 100.00% | 5.50° | 86.5% | 1.67% [1.28, 2.18]% |
| blur σ=1 | 100.00% | 3.24° | 90.0% | 0.49% [0.30, 0.80]% |

Aumentación cubre ruido σ≤6 y brillo ×0.8–1.2; σ=8 y ×0.7/×1.3 son extrapolación. Desconocidos bajo perturbación: ver reporte (D estricto).

*Éxito e2e* = clase correcta y error angular circular ≤ 10°. *Error entre ACT* = fracción de acciones del robot que serían empaque equivocado, desconocido aceptado o agarre fallido. N/A = política efectiva sin acciones.

## Limitaciones conocidas
- Dataset de laboratorio: fondo oscuro uniforme, iluminación fija, cámara fija, un eje de rotación, sin oclusión, sin desenfoque por movimiento ni cambios de escala. El desempeño en planta **no** está demostrado.
- Perturbaciones fuera del rango de aumentación (ruido fuerte, cambios de brillo > ±20 %) degradan ángulo y cobertura y elevan el riesgo observado.
- Objetos rotacionalmente simétricos o con vistas opuestas parecidas: ángulo ambiguo → REVIEW.
- Arco ciego (B) y 12 vistas por SKU (C): política efectiva sin ACT.
- Desconocidos casi duplicados de un conocido (obj61 ≈ obj92) y algunos parecidos a varios conocidos (obj69) llegan a ACT; el criterio de falsa aceptación medido en validación (pseudo-desconocidos) no se trasladó por completo al test.
- El score de incertidumbre no es una probabilidad ni una cota.

## Cuándo NO usarlo
- Con cámaras, fondos o iluminación distintos sin re-captura de referencias y recalibración de umbrales.
- Para rotaciones fuera del plano, objetos parcialmente ocultos o varios objetos en el cuadro.
- Como única barrera de seguridad: el robot debe mantener sus propias verificaciones (peso, código de barras, sensores de agarre).
- Para SKUs nuevos sin staging (≥ 12 vistas cubriendo ≥ 300°, métricas leave-one-out) y promoción explícita.

## Recomendaciones para planta
Piloto en sombra (100 % revisión humana) para recalibrar umbrales con imágenes reales; ampliar la aumentación al rango de iluminación medido; monitorear ACT/REVIEW/REJECT, la distancia de novedad y las razones de REVIEW; versionar cada re-entrenamiento (hash de datos, SHA del artefacto, snapshot de código, MLflow) sin sobrescribir versiones publicadas.
