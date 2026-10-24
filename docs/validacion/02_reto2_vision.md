**1) Veredicto: CAMBIOS REQUERIDOS**

La implementación cubre buena parte del Reto 2, pero contiene un error material en el baseline D, atribuye métricas de distintos modelos al artefacto productivo y presenta afirmaciones de seguridad superiores a la evidencia disponible.

No modifiqué archivos. Extraje el texto completo del PDF de 9 páginas.

**Tests:** `uv` falló por permisos de caché; el comando alternativo falló por falta de directorio temporal escribible. Ejecutando pytest sin captura ni caché: **14 passed, 8 errors**. Los ocho errores corresponden a la preparación del modelo temporal de los tests API; sus aserciones quedaron sin ejecutar.

**2) Tabla de requisitos**

| Requisito | Estado | Evidencia |
|---|---|---|
| Modelo productivo clásico; identificación y top-k | ✅ | Características clásicas, clasificadores y recuperación en [models.py:42](/Users/nico/Documents/IA/src/vision/models.py:42) y [:91](/Users/nico/Documents/IA/src/vision/models.py:91). ResNet queda como referencia. |
| Protocolos A, B y C exactos | ✅ | A: 36/36 vistas; B: 60/12, arco circular contiguo; C: 12/60. [data.py:132](/Users/nico/Documents/IA/src/vision/data.py:132). Intersecciones train/test vacías. |
| Protocolo D obligatorio | ⚠️ | Variante declarada: usa solamente 36 vistas de cada conocido y añade conocidos al test. Falta la ejecución estricta con todas las vistas de los 90 objetos. [data.py:126](/Users/nico/Documents/IA/src/vision/data.py:126). |
| Sin fuga ni selección usando test | ⚠️ | Cada experimento selecciona con validación dentro de train antes del test: [experiments.py:139](/Users/nico/Documents/IA/src/vision/experiments.py:139). La selección y calibración final reutilizan datos que fueron test de otros protocolos; falta evaluación independiente final. |
| ≥3 clasificadores con validación | ✅ | kNN, logística, SVM y Random Forest; rejillas y selección explícitas. [experiments.py:24](/Users/nico/Documents/IA/src/vision/experiments.py:24). |
| Error angular circular e incertidumbre | ⚠️ | Error circular correcto. La incertidumbre es una distancia convertida a grados mediante un perfil medio, sin cobertura probabilística calibrada. [angle.py:7](/Users/nico/Documents/IA/src/vision/angle.py:7), [models.py:89](/Users/nico/Documents/IA/src/vision/models.py:89). |
| Criterio objetivo de casos dudosos y salida al robot | ✅ | Calidad, novedad, confianza, incertidumbre y ambigüedad. Toda decisión distinta de ACT devuelve HOLD. [reject.py:22](/Users/nico/Documents/IA/src/vision/reject.py:22), [inference.py:94](/Users/nico/Documents/IA/src/vision/inference.py:94). |
| Desconocidos y validez de confianza | ⚠️ | Hay ECE, riesgo-cobertura y AUROC. En D, AUROC combinado **0,991**, pero **37/720 desconocidos llegan a ACT**. [results.json:4486](/Users/nico/Documents/IA/docs/report/results.json:4486). |
| Baseline y referencia moderna; costo/latencia | ⚠️ | Comparación implementada y medida: clásico **14,84 ms**, embedding ResNet CPU **29,91 ms**. Baseline D tiene denominador incorrecto; su latencia tampoco es un p50. [analysis.py:280](/Users/nico/Documents/IA/src/vision/analysis.py:280). |
| Traducción de ±10° a aceptación | ⚠️ | Éxito e2e correctamente definido como clase correcta y error ≤10°. El umbral angular de 30° necesita validación independiente del riesgo de ACT. |
| Dataset y diferencia con planta | ✅ | EDA, simetrías, pares confundibles, limitaciones y mitigaciones documentados. [build_vision_notebook.py:80](/Users/nico/Documents/IA/scripts/build_vision_notebook.py:80). |
| Notebook ejecutado de punta a punta | ✅ | 16 celdas de código ejecutadas consecutivamente, sin errores; incluye adquisición, experimentación, errores y exportación. |
| Trazabilidad MLflow | ⚠️ | **31 runs FINISHED**, parámetros, métricas, hashes y artefactos presentes. Faltan vinculación del binario y código reproducible; detalle abajo. |
| Model card completa | ✅ | Incluye uso, datos, métricas, limitaciones y condiciones de exclusión. [model_card.md:5](/Users/nico/Documents/IA/docs/model_card.md:5). La atribución de métricas requiere corrección. |
| Coherencia numérica entre entregables | ⚠️ | Las tablas principales del PDF, model card y notebook coinciden con JSON, con redondeo. También reproducen sus errores metodológicos. |
| Contrato API y pruebas | ⚠️ | Respuesta principal y HOLD implementados. Existe un bypass por NaN y un posible ángulo serializado como 360°. Tests API bloqueados por el entorno. |

**3) Alucinaciones o afirmaciones sin respaldo**

Las cifras principales tienen respaldo en resultados guardados. Estas afirmaciones sí contradicen o exceden la evidencia:

- **«Garantiza…solo ángulos confiables»**: D registra **44 fallos entre 3.021 ACT**. El código garantiza HOLD cuando *decide* rechazar, pero puede aceptar predicciones erróneas. [build_vision_report.py:215](/Users/nico/Documents/IA/scripts/build_vision_report.py:215).
- **Riesgo-cobertura “monótono” y existencia de un umbral operable:** las curvas recalculadas presentan subidas y bajadas; B no automatiza ningún caso. [build_vision_report.py:287](/Users/nico/Documents/IA/scripts/build_vision_report.py:287).
- **«Test una sola vez»**: posteriormente se vuelven a evaluar los tests A–C con `k=1,2,3`. No encontré un selector que elija `k` mediante esos resultados; la afirmación de evaluación única es falsa. [build_vision_notebook.py:273](/Users/nico/Documents/IA/scripts/build_vision_notebook.py:273).
- **Logística y SVM “prácticamente empatadas” en exactitud:** medias de validación **95,52% frente a 92,56%**. [results.json:4295](/Users/nico/Documents/IA/docs/report/results.json:4295).
- **Dos vecinos reducen el MAE**, sin matices: en B empeora de **66,47° a 67,47°**. [build_vision_notebook.py:284](/Users/nico/Documents/IA/scripts/build_vision_notebook.py:284).

**4) Errores técnicos**

1. **Error operativo del baseline D subestimado.** Se evalúan únicamente los 3.240 conocidos y después se declara ACT=100%. Los 720 desconocidos también serían aceptados. El error comparable es **(71+720)/3960 = 19,97%**, frente al **2,19% publicado**. [experiments.py:215](/Users/nico/Documents/IA/src/vision/experiments.py:215).

2. **Evaluación del artefacto final insuficiente y métricas mal atribuidas.** El resumen usa `C=0.1` en A y `C=10` en D; producción usa `C=1` y otros umbrales. Además, la validación productiva contiene las **3.600 imágenes del test A** y después se reajusta el modelo con todos los datos. Las métricas individuales por protocolo conservan su separación; **no constituyen un test independiente de la política final**. [train_vision.py:66](/Users/nico/Documents/IA/scripts/train_vision.py:66), [metadata.json:39](/Users/nico/Documents/IA/models/vision-1.0.0/metadata.json:39).

   **Sobre 30° frente a ±10°:** 30° puede funcionar como corte de un *score* heurístico; no representa una cota del error real. Su coherencia exige demostrar el riesgo `P(clase incorrecta o error>10° | ACT)` con datos independientes. El **0,79%** disponible pertenece a la validación usada para seleccionar el umbral.

3. **NaN desactiva la compuerta angular.** Reproducción en memoria: enviar `{"max_angle_uncertainty_deg":"NaN"}` devuelve **HTTP 500**, pero deja aplicado NaN. Después, `decide(... angle_unc=100 ...)` devuelve **ACT** porque `100 > NaN` es falso. [app.py:207](/Users/nico/Documents/IA/services/vision_api/app.py:207), [reject.py:32](/Users/nico/Documents/IA/src/vision/reject.py:32).

4. **Cero acciones se interpreta como validación satisfactoria.** `select_thresholds` considera factible un umbral sin ningún ACT y publica `automation_enabled=true`. B/C tienen cobertura de validación cero; C produce apenas **2 ACT en test**. Eso no demuestra riesgo ≤2%. [reject.py:119](/Users/nico/Documents/IA/src/vision/reject.py:119).

5. **Trazabilidad incompleta del código y pesos.** Los hashes reales de datos coinciden y los últimos resultados MLflow corresponden al JSON actual. Sin embargo, el entrenamiento registra solamente metadata, sin `model.joblib` ni su hash. El commit registrado `97862ca…` carece del código de entrenamiento, según `git ls-tree`; siete entrenamientos reutilizan `vision-1.0.0`. [train_vision.py:119](/Users/nico/Documents/IA/scripts/train_vision.py:119).

6. **Latencia baseline mal denominada.** Un promedio por imagen de una búsqueda en lote se guarda como `p50_ms`. No se calcularon percentiles para ese valor. [build_vision_notebook.py:390](/Users/nico/Documents/IA/scripts/build_vision_notebook.py:390).

7. **Ángulo fuera del rango contractual.** Redondear antes de normalizar puede devolver **360,0°**. Existe una predicción guardada de **359,963°** en C que se serializaría así. [inference.py:99](/Users/nico/Documents/IA/src/vision/inference.py:99).

**5) Cambios priorizados**

1. Evaluar una política final congelada con un test independiente; identificar exactamente modelo y umbrales en cada tabla.
2. Corregir el baseline D incluyendo desconocidos en el error entre ACT.
3. Rechazar NaN/infinito antes de modificar umbrales; añadir pruebas de atomicidad.
4. Exigir suficientes ACT de validación y reportar riesgo con incertidumbre; usar N/A cuando no haya acciones.
5. Validar explícitamente el corte angular de 30° frente al objetivo ±10°.
6. Ejecutar D estricto y conservar la variante actual como experimento adicional.
7. Comparar alternativas de `k` en validación y corregir la afirmación de evaluación única.
8. Registrar código reproducible, pesos, SHA del artefacto y versiones inmutables en MLflow.
9. Corregir afirmaciones, denominadores y etiquetas de latencia; regenerar los entregables.
10. Normalizar el ángulo después del redondeo y completar los tests API en un entorno con temporales escribibles.