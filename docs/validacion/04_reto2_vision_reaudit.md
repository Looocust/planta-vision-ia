**1) Veredicto: CAMBIOS REQUERIDOS**

De los siete errores anteriores: **5 corregidos, 2 parcialmente corregidos y ninguno sin corregir por completo**. Persisten la contaminación de la selección global y la trazabilidad incompleta del código. Encontré además dos defectos introducidos por los cambios.

No modifiqué archivos. Extraje el texto de las **10 páginas** de :codex-file-citation{path="/Users/nico/Documents/IA/docs/report/vision_report.pdf" purpose="source"} y contrasté sus cifras con `results.json`.

El comando solicitado:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vision_core.py tests/test_vision_api.py
```

falló antes de ejecutar pruebas: `FileNotFoundError: No usable temporary directory found`. Con `-s --tb=no`, el resultado fue **19 passed, 14 errors in 6.41s**. Los 14 errores son de preparación del fixture API por temporales no escribibles; **sus aserciones no se ejecutaron**.

**2) Tabla de requisitos**

| Requisito aplicable | Estado | Evidencia |
|---|---|---|
| Modelo productivo clásico; identificación, confianza y recuperación top-k | ✅ Cumple | Escalado/PCA, clasificador y recuperación en [models.py:42](/Users/nico/Documents/IA/src/vision/models.py:42) y [:73](/Users/nico/Documents/IA/src/vision/models.py:73). |
| Protocolos A, B y C exactos | ✅ Cumple | Particiones en [data.py:133](/Users/nico/Documents/IA/src/vision/data.py:133); tamaños y separación comprobados por pytest. |
| D obligatorio con todas las vistas de 90 objetos | ✅ Cumple | Ahora existe `D_strict`: **6.480 train / 720 test**, exclusivamente desconocidos. [data.py:150](/Users/nico/Documents/IA/src/vision/data.py:150). |
| Ningún hiperparámetro ajustado con test | ❌ Falta | La selección global agrega validaciones A–D que contienen imágenes de los tests de otros protocolos. [experiments.py:296](/Users/nico/Documents/IA/src/vision/experiments.py:296). |
| Comparar ≥3 clasificadores con validación | ✅ Cumple | kNN, logística, SVM y Random Forest con rejillas explícitas. [experiments.py:24](/Users/nico/Documents/IA/src/vision/experiments.py:24). |
| Ángulo circular, incertidumbre y justificación | ✅ Cumple | Selección angular en validación; se reconoce expresamente que la incertidumbre es heurística. [recipe.py:38](/Users/nico/Documents/IA/src/vision/recipe.py:38), [model_card.md:11](/Users/nico/Documents/IA/docs/model_card.md:11). |
| Rechazo y confianza válidos para automatización | ⚠️ Parcial | D estricto deja pasar **35/720 desconocidos a ACT: 4,86%**, IC95 **3,52–6,69%**. [results.json:5936](/Users/nico/Documents/IA/docs/report/results.json:5936). |
| Traducir tolerancia ±10° a aceptación | ⚠️ Parcial | Éxito e2e y error entre ACT están definidos; falta independencia respecto de la selección global. [recipe.py:69](/Users/nico/Documents/IA/src/vision/recipe.py:69). |
| Baseline, referencia moderna y costo/latencia | ✅ Cumple | Baseline D corregido; mediciones diferenciadas: clásico p50 **13,32 ms**, ResNet CPU solo embedding **30,46 ms**, baseline media en lote **0,274 ms**. [results.json:8209](/Users/nico/Documents/IA/docs/report/results.json:8209). |
| Dataset, errores y diferencias frente a planta | ✅ Cumple | EDA, simetrías, pares confundibles y mitigaciones en [build_vision_notebook.py:85](/Users/nico/Documents/IA/scripts/build_vision_notebook.py:85). |
| Notebook reproducible de punta a punta | ✅ Evidencia guardada | [Notebook](/Users/nico/Documents/IA/notebooks/01_experimentacion.ipynb): **15 celdas de código**, ejecuciones consecutivas 1–15, sin errores guardados; incluye exportación. No lo reejecuté porque escribe artefactos. |
| Trazabilidad y artefacto versionado | ⚠️ Parcial | Pesos y hashes actuales coinciden; faltan código completo reproducible e inmutabilidad de versiones. Véase hallazgo 5. |
| Reporte y model card coherentes | ⚠️ Parcial | Cifras principales coinciden con JSON; subsisten afirmaciones excesivas de independencia y seguridad. |
| Contrato API | ⚠️ Parcial | HOLD, NaN y rango angular corregidos; `top_k=0` sigue produciendo HTTP 500. |

**3) Alucinaciones o afirmaciones sin respaldo**

| Afirmación del informe anterior | Resultado de la reauditoría |
|---|---|
| «Garantiza…solo ángulos confiables» | **Parcialmente corregida.** La model card reconoce riesgo residual, pero persiste «Los cortes altos son seguros» en [build_vision_report.py:369](/Users/nico/Documents/IA/scripts/build_vision_report.py:369) y el notebook. La evidencia permite hablar de riesgo observado, no de seguridad general. |
| Riesgo-cobertura monótono y umbral operable en B | **Corregida.** Ahora reconoce subidas locales y ausencia de tramo aceptable en B. [build_vision_report.py:359](/Users/nico/Documents/IA/scripts/build_vision_report.py:359). |
| Test una sola vez pese a comparar k sobre test | **Corregida para k.** Los candidatos se comparan en validación; el barrido posterior de umbrales sobre test está identificado como descriptivo y no selecciona nada. [recipe.py:44](/Users/nico/Documents/IA/src/vision/recipe.py:44), [:93](/Users/nico/Documents/IA/src/vision/recipe.py:93). |
| Logística y SVM prácticamente empatadas | **Corregida.** Ahora declara unos tres puntos de diferencia; JSON respalda **95,52% frente a 92,56%**. [results.json:4572](/Users/nico/Documents/IA/docs/report/results.json:4572). |
| Dos vecinos reducen siempre el MAE | **Corregida.** La explicación condiciona la mejora a referencias cercanas y reconoce B/C. [build_vision_notebook.py:311](/Users/nico/Documents/IA/scripts/build_vision_notebook.py:311). |

**Afirmación adicional sin respaldo suficiente:** «test independiente por protocolo» y presentar esas cifras como métricas esperadas del artefacto final. La selección global ya utilizó imágenes de esos tests mediante otras validaciones; además, la propia model card reconoce que la calibración productiva comparte el test A. [model_card.md:13](/Users/nico/Documents/IA/docs/model_card.md:13), [:19](/Users/nico/Documents/IA/docs/model_card.md:19).

No encontré cifras principales inventadas: los resultados guardados respaldan los valores publicados.

**4) Errores técnicos**

| Error anterior | Estado | Verificación |
|---|---|---|
| **1. Denominador del baseline D** | **Corregido** | Incluye desconocidos: **791/3.960 = 19,97%**, frente al 2,19% anterior. [recipe.py:169](/Users/nico/Documents/IA/src/vision/recipe.py:169), [results.json:7581](/Users/nico/Documents/IA/docs/report/results.json:7581). |
| **2. Evaluación final y atribución de métricas** | **Parcialmente corregido** | Ahora separa exploración y receta `C=1`. Persiste selección global contaminada: reproduje **600 imágenes de test A dentro de val B**; la unión de validaciones toca **600/330/2.100/810/270** imágenes de los tests A/B/C/D/D estricto. [experiments.py:296](/Users/nico/Documents/IA/src/vision/experiments.py:296), [results.json:4560](/Users/nico/Documents/IA/docs/report/results.json:4560). Entrenar finalmente con todos los datos es aceptable; no convierte esa evaluación en independiente. |
| **3. NaN abre compuerta y deja cambios parciales** | **Corregido** | Validación finita y actualización atómica. Reproducción HTTP en memoria: **400, umbrales sin cambios**. [reject.py:18](/Users/nico/Documents/IA/src/vision/reject.py:18), [inference.py:109](/Users/nico/Documents/IA/src/vision/inference.py:109). |
| **4. Cero ACT considerado satisfactorio** | **Corregido** | Exige ≥50 ACT y error puntual ≤2%; desactiva si no cumple. B/C tienen **0 ACT y riesgo null**. [reject.py:125](/Users/nico/Documents/IA/src/vision/reject.py:125). |
| **5. Trazabilidad de código y pesos** | **Parcialmente corregido** | SHA real coincide; último run MLflow contiene pesos, metadata y `recipe.py` idénticos. Pero metadata referencia commit `3aa0bbb…`, **dirty=true**, que no contiene la receta nueva; no hay snapshot completo. Persisten entrenamientos que sobrescriben `vision-1.0.0`. [metadata.json:696](/Users/nico/Documents/IA/models/vision-1.0.0/metadata.json:696), [train_vision.py:133](/Users/nico/Documents/IA/scripts/train_vision.py:133). |
| **6. Media denominada p50** | **Corregido** | Se guarda y presenta como media por imagen en lote. [recipe.py:173](/Users/nico/Documents/IA/src/vision/recipe.py:173), [analysis.py:321](/Users/nico/Documents/IA/src/vision/analysis.py:321). |
| **7. Ángulo serializado como 360°** | **Corregido** | Normaliza después de redondear: `359.963 → 0.0`. [inference.py:99](/Users/nico/Documents/IA/src/vision/inference.py:99). |

Hallazgos adicionales:

- **Nuevo: SHA incorrecto en candidatos/promociones.** Staging copia `model_sha256` del padre; `save()` no lo recalcula. Reproducción sin escrituras: candidato serializado en memoria con **hash distinto del declarado**. Promoción conserva ese dato. [inference.py:175](/Users/nico/Documents/IA/src/vision/inference.py:175), [:64](/Users/nico/Documents/IA/src/vision/inference.py:64), [:203](/Users/nico/Documents/IA/src/vision/inference.py:203).
- **Nuevo: diagnóstico contradictorio al desactivar ACT.** Conserva conteos/riesgo del candidato descartado y pone solamente cobertura a cero. ResNet A publica **7 ACT, cobertura 0 y riesgo 0**, aunque el umbral efectivo es −1. [reject.py:141](/Users/nico/Documents/IA/src/vision/reject.py:141), [results.json:6094](/Users/nico/Documents/IA/docs/report/results.json:6094).
- **Adicional preexistente: `top_k=0` produce HTTP 500.** La API lo admite, pero inferencia accede a `similar[0]` cuando la lista está vacía. Reproduje **200 con k=5 y 500 con k=0**. [app.py:174](/Users/nico/Documents/IA/services/vision_api/app.py:174), [inference.py:104](/Users/nico/Documents/IA/src/vision/inference.py:104).
- **Formato preexistente, ampliado:** `results.json` contiene **62 valores `NaN`**, inválidos en JSON estándar. Un lector estricto falla; usar `null`. Ejemplo: [results.json:6119](/Users/nico/Documents/IA/docs/report/results.json:6119).

**5) Cambios priorizados**

1. Eliminar la contaminación de selección global y evaluar la receta con particiones independientes de todas las decisiones de selección.
2. Fijar un criterio explícito de falsa aceptación de desconocidos y validarlo antes de autorizar ACT.
3. Recalcular el SHA de pesos en cada guardado, incluido staging y promoción.
4. Registrar el código completo utilizado y evitar sobrescribir versiones publicadas.
5. Resolver `top_k=0` y cubrirlo con una prueba API.
6. Separar métricas del candidato descartado y de la política efectiva; serializar ausencias como `null`.
7. Sustituir afirmaciones generales de seguridad e independencia y regenerar entregables.
8. Completar las 14 pruebas API en un entorno con temporales escribibles.