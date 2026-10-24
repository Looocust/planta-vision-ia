# Guía de defensa técnica

Documento de estudio para exponer y defender el proyecto. Cada bloque responde tres preguntas: **qué problema resuelve, por qué se eligió y qué evidencia demuestra que funciona.**

> Las cifras marcadas con `⟨…⟩` se completan con los resultados reales de `docs/report/results.json` y `docs/report/agent_eval.md`.

---

## 1. El discurso de 60 segundos

> "La tarea manual tiene dos partes: reconocer el producto y saber cómo está girado. Las separé de la conversación.
> En la línea corre un **modelo clásico** (características de color, forma y textura + un clasificador) que responde en milisegundos, sin internet y sin LLM. Unas **reglas determinísticas** deciden si el robot actúa, si el caso va a revisión humana o si se rechaza. El robot **nunca recibe un ángulo dudoso**.
> Encima hay un **asistente agéntico** en LangGraph para supervisores, calidad, mantenimiento y ML: inspecciona imágenes, gestiona la cola de revisión, cruza métricas, diagnostica fallas con citas a los procedimientos y prepara operaciones de riesgo, que **solo se ejecutan con permiso y aprobación humana**, verificados en código.
> Lo medí con los 4 protocolos que pide la prueba, con 30+ casos automáticos para el agente, y diseñé el despliegue en Azure (y AWS) con su modelo de costos."

## 2. La idea central: qué es determinístico y qué es agéntico

| Parte | Tipo | Dónde corre | Por qué |
|---|---|---|---|
| Imagen → producto + ángulo → orden al robot | **Determinístico** (modelo ML clásico + reglas) | **En la planta (edge)**, junto a la línea | Latencia de milisegundos, sigue funcionando sin internet, costo casi cero por imagen, comportamiento auditable |
| Decisión ACT / REVIEW / REJECT | **Determinístico** (umbrales) | Edge | Un error mueve un robot: no puede depender de un modelo de lenguaje |
| Permisos, aprobaciones, límites de umbral, rango del ángulo manual | **Determinístico** (política en código) | Nube | Seguridad: el LLM puede ser manipulado (prompt injection) |
| Conversar, investigar, cruzar datos, redactar reportes, proponer acciones | **Agéntico** (LLM + tools) | Nube | Problemas abiertos donde un humano pregunta en lenguaje natural |

**Frase para defender:** *"El LLM propone; el código decide; el humano aprueba."*

## 3. Reto 2 – Modelo de visión (explicado fácil)

### El dataset (COIL-100)
- 100 objetos, cada uno fotografiado girando sobre un plato, una foto cada 5° → 72 fotos por objeto, 7.200 en total, 128×128 píxeles.
- **Propiedades que condicionan el diseño:**
  - **El ángulo es circular**: 355° y 0° están a 5° de distancia. Por eso el error se mide como `min(|a−b|, 360−|a−b|)` y el promedio de ángulos se hace con senos y cosenos (media circular), no con un promedio normal.
  - **Objetos simétricos** (latas, frascos, tazas vistas de lado): se ven casi iguales en cualquier ángulo. Su orientación es **imposible** de saber por la imagen. El sistema lo detecta y lo manda a revisión en vez de inventar.
  - **Fondo negro y luz perfecta**: en planta habrá sombras, suciedad, movimiento y otros fondos. Por eso existe un **filtro de calidad de imagen** y un plan de piloto con datos reales.
- **Hallazgos del EDA:**
  - **7 objetos** se tratan como rotacionalmente ambiguos (obj35, 47, 67, 84, 94, 95, 98) y siempre van a REVIEW.
  - En **75 de 100** objetos, la vista girada 180° se parece más que la de 90°: la confusión "de espaldas" es un riesgo real.
  - El par más confundible es **obj61 ↔ obj92**, casi gemelos.
- **El hallazgo más importante (prueba integral):**
  - Con ruido de cámara imperceptible (σ=1), la versión 1.0.0 rechazaba objetos conocidos como "desconocidos": el ruido sobre el fondo negro "inventaba" texturas.
  - La versión **1.0.1** agrega preprocesado (filtro de mediana, máscara del objeto, fondo a cero) y entrenamiento con imágenes perturbadas (ruido, brillo, desenfoque).
  - Esto responde directo a la pregunta de la prueba: *"en qué difiere de un entorno real de planta y cómo lo aborda"*.

### Los 4 protocolos (son 4 "exámenes" distintos)
| Protocolo | Pregunta que responde |
|---|---|
| A – Interpolación | ¿Reconoce ángulos intermedios que no vio? |
| B – Sector ciego | ¿Y si le faltan 60° seguidos de un objeto? |
| C – Escasez | ¿Funciona con solo 12 fotos por objeto (producto nuevo)? |
| D – Desconocido | ¿Sabe decir "esto no lo conozco"? |

**Regla de oro:** entrenamiento = estudiar, validación = practicar, prueba = examen final. Ningún ajuste se hizo mirando el examen final.

### Cómo funciona el modelo
1. **Características** (números que describen la imagen): histograma de color HSV (qué colores tiene), HOG (bordes y formas), LBP (texturas). Se reducen con PCA.
2. **Clasificador:** se compararon **kNN, SVM, Random Forest y Regresión Logística**.
   - En producción se usa **Regresión Logística (C=1)**: tuvo la mejor exactitud media en validación (95,5% frente a 92,6% del SVM).
   - Sobre todo, sus **probabilidades están calibradas**: cuando dice 99%, acierta ~99%.
   - Las "confianzas" del SVM y del Random Forest no servían para decidir (su error de calibración, ECE, era 0,35–0,74).
3. **Ángulo**: dentro del objeto identificado, busca las vistas de entrenamiento más parecidas y promedia sus ángulos de forma circular. **La incertidumbre** es qué tan de acuerdo están esos vecinos: si apuntan a ángulos muy distintos, el sistema no está seguro.
4. **Bonus**: esas mismas vistas parecidas son el "top-k de imágenes similares" que se muestran como evidencia.

### ¿Cuándo dice "no sé"? (rechazo)
- **REJECT**: imagen mala (oscura, borrosa, vacía) u objeto desconocido (está lejos de todo lo conocido).
- **REVIEW**: conoce el objeto pero la confianza de la clase es baja, la incertidumbre del ángulo supera el umbral derivado de los ±10°, o el objeto es simétrico.
- **ACT**: todo está bien → el robot recibe `PICK` + cuánto girar.
- En REVIEW o REJECT el robot recibe **HOLD** y el caso va a la cola humana.

### ¿Cómo sé que la confianza sirve para decidir?
- **Diagrama de calibración:** si el modelo dice 90%, ¿acierta el 90% de las veces? En el protocolo A el ECE es **0,001**, casi perfecto.
- **Curva riesgo-cobertura:** cuanto más se rechaza, menos errores hay.
  - El umbral se elige **solo en validación**: la máxima cobertura con error ≤2% entre los casos automatizados, exigiendo al menos 50 casos ACT.
  - Si no se cumple, **la automatización se desactiva sola**. Eso pasa en los protocolos B y C.
- **Objetos desconocidos:** como regla explícita, como máximo el 5% de los pseudo-desconocidos de validación puede llegar a ACT.
  - En validación de producción fue el 4,1%.
  - En el test D estricto, el **6,4%** llega a ACT: todas son fotos del obj61, el gemelo del obj92.
- **Ojo con la palabra "independiente":** las cifras independientes salen de **selección anidada** (cada protocolo elige modelo, parámetros y umbrales solo con su propio entrenamiento). La configuración desplegada se reporta aparte, con esa salvedad.

### Criterio de aceptación (±10°)
- Éxito de punta a punta = **clase correcta Y error de ángulo ≤ 10°**.
- La métrica de negocio no es la exactitud: es **cuántos casos automatiza el sistema** y **qué tan pocos errores comete en los que automatiza** (cada error = producto mal empacado o agarre fallido).
**Resultados en test, configuración desplegada (vision-1.0.1):**

| Protocolo | Acierta objeto | Error ángulo (MAE) | Automatiza (ACT) | Error cuando actúa (IC95) |
|---|---|---|---|---|
| A – intermedios | 100% | 2,9° | **90,3%** | **0,09%** (0,03–0,27%) |
| B – sector ciego | 96,2% | 65,5° | 0% (se desactiva) | N/A: no arriesga |
| C – escasez | 98,8% | 13,0° | 0% (se desactiva) | N/A: no arriesga |
| D – variante mixta | — | — | 73,7% | 0,93% (0,64–1,34%) |
| D – estricto | — | — | 6,4% de desconocidos llega a ACT | (todo obj61) |

**Robustez (protocolo A con perturbaciones):**

| Condición | Error cuando actúa |
|---|---|
| Ruido σ=2 | 0,15% |
| Ruido σ=4 | 1,1% |
| Desenfoque | 0,49% |
| Brillo ±30% | 1,5–1,7% |
| Ruido σ=8 | 2,4% |

Las condiciones más fuertes que las del entrenamiento están reportadas tal cual.

### Comparación obligatoria
| Modelo | Qué es | Error cuando actúa (A / C / D mixto) | Latencia por imagen | Tamaño |
|---|---|---|---|---|
| Baseline | Comparar píxeles crudos (1-NN), siempre actúa | 2,4% / **25,7%** / **20,0%** | 0,3 ms (en lote) | — |
| **Clásico (producción)** | HSV + HOG + LBP + Hu → PCA → Regresión Logística | **0,09%** / no actúa / 0,93% | p50 **18 ms** en CPU | 17,6 MB |
| Referencia moderna | ResNet18 preentrenada (congelada) + misma receta | No automatiza nada: no cumple el riesgo en validación | ~27 ms en CPU, solo el embedding | 44,7 MB de pesos + PyTorch |

**Conclusión:** la red moderna **clasifica** un poco mejor (por ejemplo, 99,7% en B), pero sus confianzas no permitieron cumplir la regla de riesgo y **no automatiza nada**. Además es más pesada y necesita PyTorch. La brecha **no justifica** su costo y complejidad para producción. Sí vale la pena explorarla en el futuro con calibración propia.

## 4. Reto 3 – Asistente agéntico

### Qué es un "agente" (explicado fácil)
Un chatbot normal solo contesta. Un **agente** recibe una meta, **decide qué herramientas usar** (consultar métricas, inspeccionar una foto, buscar un manual, crear un ticket), mira el resultado y decide el siguiente paso, hasta terminar.

### Patrón elegido
⟨Se completa con docs/agent_design.md: agente con tools en LangGraph + nodo de política determinístico + pausa para aprobación humana⟩

**Alternativas descartadas** (prepárate para esta pregunta):
| Alternativa | Por qué no |
|---|---|
| Multi-agente (un agente por área) | Más llamadas al LLM = más costo y latencia, más puntos de falla; no mejoró la calidad en este alcance |
| Flujo fijo / formulario | No resuelve preguntas abiertas como "¿por qué subieron los rechazos?" |
| ⟨…⟩ | ⟨…⟩ |

### Lo que el LLM NUNCA decide
- Si el robot actúa (lo decide el servicio de visión).
- Si un usuario tiene permiso (tabla de roles en `configs/roles.yaml`).
- Si una operación de riesgo se ejecuta (requiere aprobación de otra persona con rol suficiente).
- A qué planta o línea tiene acceso (sale de la sesión, no de lo que escriba el LLM).

### Límites para que no se quede en un loop
Máximo de pasos por turno, máximo de llamadas a tools, detección de la misma llamada repetida, timeouts y presupuesto de tokens.

### Evaluación
⟨Resultados de docs/report/agent_eval.md: % de éxito, % de tools correctas, tokens y costo promedio, fallos principales, 3 mejoras⟩

## 5. Reto 4 – Despliegue
- Dos servicios separados: **API de visión** (puerto 8001) y **agente** (puerto 8002).
- Un solo comando: `make up` (o `./scripts/run_local.sh`). También hay `docker-compose.yml`.
- Configuración en `.env` (nunca en el código), modelo versionado en `models/vision-1.0.0/`.

## 6. Reto 6 – Azure y AWS (explicado fácil)

**La nube** = alquilar computadores y servicios administrados en vez de comprarlos.

| Pieza | Azure | AWS | Para qué |
|---|---|---|---|
| Modelo en la planta | IoT Edge / AKS Edge | IoT Greengrass | Corre la visión al lado del robot sin depender de internet |
| Contenedores | Container Apps | ECS Fargate | Corre el agente y la API |
| LLM | Azure OpenAI | Bedrock | El "cerebro" del agente |
| Imágenes | Blob Storage | S3 | Guardar fotos con reglas de borrado automático |
| Manuales (búsqueda) | AI Search | OpenSearch / Knowledge Bases | Buscar procedimientos con citas |
| Modelos | Azure ML | SageMaker | Registro y versiones de modelos |
| Secretos | Key Vault | Secrets Manager | Guardar claves |
| Usuarios | Entra ID | IAM Identity Center | Permisos por rol |
| Monitoreo | Azure Monitor | CloudWatch | Logs y alertas |

**Costos** (1 planta, 1 estación, 90,3% automatizado medido; USD/mes):

| | Colombia · Azure | Colombia · AWS | España · Azure | España · AWS |
|---|---|---|---|---|
| Operación actual | 2.485 | 2.485 | 12.283 | 12.283 |
| Nube | 638 | 410 | 741 | 464 |
| Humano residual (cola + persona itinerante) | 1.297 | 1.297 | 6.409 | 6.409 |
| **Ahorro** | 505 (20%) | 733 (29%) | 5.088 (41%) | 5.365 (44%) |
| Recuperar la inversión, 1 estación | ~100 meses | ~69 meses | **~10 meses** | ~9 meses |
| Recuperar la inversión, 5 estaciones | ~27 meses | ~25 meses | **~4,7 meses** | ~4,6 meses |
| % de automatización mínimo para ahorrar | 85,6% | 83,5% | 80,8% | 80,3% |

**Cómo leerlo:**
1. **La nube no es lo caro.** Lo caro es la **cola de revisión humana**, ~42% del costo actual. Cada punto de automatización vale más que elegir Azure o AWS.
2. **Con desconocidos mezclados** (73,7% de automatización), **el proyecto no se paga.** Por eso el alta controlada de SKU nuevos y el rechazo de desconocidos son críticos para el negocio.
3. **España se paga en menos de un año.** En Colombia hay que escalar (5 o más estaciones) y arrancar con un piloto austero.
4. **Recomendación: Azure**, por el ecosistema Microsoft (Entra ID, Teams), la gestión del edge con Arc y la residencia de datos del LLM. Se reconoce que AWS sale más barato y que en Colombia esa diferencia pesa.

Las cifras salen de [`docs/cloud/finops_model.xlsx`](cloud/finops_model.xlsx), que tiene fórmulas vivas. Los supuestos (salarios, throughput, CAPEX) están marcados y son editables.

## 7. Preguntas difíciles que te pueden hacer

| Pregunta | Idea para responder |
|---|---|
| ¿Por qué ML clásico y no deep learning? | La prueba lo exige, y además lo medí: ResNet18 clasifica un poco mejor, pero con la misma regla de riesgo no automatiza nada, pesa más y necesita PyTorch. El clásico corre en CPU en ~18 ms y está calibrado. |
| ¿Cómo validaste que no te estabas engañando? | Un segundo modelo de otro proveedor (Codex) auditó cada reto contra el texto de la prueba. Encontró un baseline mal contado, un bug con umbrales NaN y una suplantación de aprobador, y todo se corrigió. Los informes están en `docs/validacion/`. |
| ¿Qué pasa con objetos casi idénticos (obj61 vs obj92)? | Es la debilidad principal: el 6,4% de desconocidos que llega a ACT es obj61. En planta se resuelve con un dato complementario (código de barras, peso) para esos SKU. |
| ¿Funciona con ruido real de cámara? | La versión 1.0.0 no funcionaba; lo detecté en la prueba integral. La 1.0.1 tiene preprocesado y aumentación, y la robustez está medida: error de 1,1% con ruido σ=4. |
| ¿Cómo evitaste usar el test para ajustar? | Validación solo con vistas de entrenamiento; los umbrales salen de validación; el test se evalúa una sola vez. |
| ¿Qué pasa con una lata (objeto simétrico)? | El ángulo no es identificable; el sistema lo detecta y manda REVIEW. Para el robot, si es simétrico, el agarre no depende del ángulo → mejora futura: marcar el SKU como "agarre libre". |
| ¿Qué pasa si se cae internet? | La línea sigue: la visión corre en el edge. Solo el asistente queda sin servicio. |
| ¿Qué pasa si el servicio de visión falla? | Reintentos, circuit breaker y el robot queda en HOLD. Nunca se inventa un resultado. |
| ¿Y si alguien le pide al agente "mueve el robot a 90°"? | Verifica el rol, crea una solicitud de aprobación y queda pausado hasta que otra persona autorizada apruebe. |
| ¿Cómo agregan un producto nuevo? | Se suben vistas, se valida que sean suficientes (cobertura del círculo), se crea un modelo candidato en staging, se mide y se pide aprobación para promoverlo. |
| ¿Qué cambia en una fábrica real? | Datos reales, calibración cámara-robot, iluminación controlada, piloto en sombra (el sistema predice pero el operario sigue) antes de quitar al operario. |
| ¿Cuánto cuesta? | La nube cuesta ~USD 640/mes en Azure por estación (EE. UU.). Ahorra 20% en Colombia y 41% en España; en España se recupera la inversión en ~10 meses. El factor que más pesa es el % de automatización: por debajo de ~81–86% no hay ahorro. |

## 8. Vocabulario mínimo

- **API**: puerta para que dos programas hablen.
- **Clasificador**: modelo que elige una categoría.
- **Características (features)**: números que describen una imagen.
- **PCA**: técnica que resume muchos números en pocos sin perder lo importante.
- **kNN**: "dime a quién te pareces": busca los ejemplos más parecidos.
- **SVM**: traza fronteras entre clases.
- **Calibración**: que el 90% de confianza signifique 90% de aciertos.
- **AUROC**: qué tan bien separa dos grupos (1.0 = perfecto, 0.5 = azar).
- **Embedding**: lista de números que representa el contenido (imagen o texto).
- **LangGraph**: librería para construir agentes como un grafo de pasos con estado y pausas.
- **Human-in-the-loop**: el flujo se pausa y espera que un humano apruebe.
- **Prompt injection**: texto malicioso que intenta darle órdenes al LLM.
- **Idempotencia**: repetir la misma acción no la duplica (por ejemplo, no crea dos tickets iguales).
- **Edge**: computador en la planta, al lado de la máquina.
- **MLflow**: herramienta para registrar experimentos (parámetros, métricas, artefactos).
- **Model card**: ficha del modelo con su uso previsto, métricas y limitaciones.
- **FinOps**: gestionar y optimizar el costo de la nube.
