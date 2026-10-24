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
- ⟨Hallazgos del EDA: cuántos objetos simétricos, qué clases se confunden⟩

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
2. **Clasificador**: se compararon ⟨kNN, SVM, Random Forest, Regresión Logística⟩. Ganó ⟨…⟩ porque ⟨…⟩.
3. **Ángulo**: dentro del objeto identificado, busca las vistas de entrenamiento más parecidas y promedia sus ángulos de forma circular. **La incertidumbre** es qué tan de acuerdo están esos vecinos: si apuntan a ángulos muy distintos, el sistema no está seguro.
4. **Bonus**: esas mismas vistas parecidas son el "top-k de imágenes similares" que se muestran como evidencia.

### ¿Cuándo dice "no sé"? (rechazo)
- **REJECT**: imagen mala (oscura, borrosa, vacía) u objeto desconocido (está lejos de todo lo conocido).
- **REVIEW**: conoce el objeto pero la confianza de la clase es baja, la incertidumbre del ángulo supera el umbral derivado de los ±10°, o el objeto es simétrico.
- **ACT**: todo está bien → el robot recibe `PICK` + cuánto girar.
- En REVIEW o REJECT el robot recibe **HOLD** y el caso va a la cola humana.

### ¿Cómo sé que la confianza sirve para decidir?
- **Diagrama de calibración**: si el modelo dice 90%, ¿acierta el 90% de las veces? ⟨ECE = …⟩
- **Curva riesgo-cobertura**: cuanto más rechazo, menos errores. Elegimos el punto de operación ⟨cobertura …%, error entre los automatizados …%⟩.
- **Protocolo D**: AUROC ⟨…⟩ para separar conocidos de desconocidos.

### Criterio de aceptación (±10°)
- Éxito de punta a punta = **clase correcta Y error de ángulo ≤ 10°**.
- La métrica de negocio no es la exactitud: es **cuántos casos automatiza el sistema** y **qué tan pocos errores comete en los que automatiza** (cada error = producto mal empacado o agarre fallido).
- ⟨Tabla de resultados por protocolo⟩

### Comparación obligatoria
| Modelo | Qué es | Resultado | Latencia | Tamaño |
|---|---|---|---|---|
| Baseline | Comparar píxeles crudos (1-NN) | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ |
| **Clásico (producción)** | Características + ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ |
| Referencia moderna | Red neuronal preentrenada + kNN | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ |

**Conclusión:** ⟨¿la brecha justifica el costo/latencia/complejidad?⟩

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

⟨Costos: operación actual vs Azure vs AWS, payback⟩

## 7. Preguntas difíciles que te pueden hacer

| Pregunta | Idea para responder |
|---|---|
| ¿Por qué ML clásico y no deep learning? | La prueba lo exige, y además lo medí: ⟨brecha⟩. El clásico es más rápido, más pequeño y corre en CPU en la planta. |
| ¿Cómo evitaste usar el test para ajustar? | Validación solo con vistas de entrenamiento; los umbrales salen de validación; el test se evalúa una sola vez. |
| ¿Qué pasa con una lata (objeto simétrico)? | El ángulo no es identificable; el sistema lo detecta y manda REVIEW. Para el robot, si es simétrico, el agarre no depende del ángulo → mejora futura: marcar el SKU como "agarre libre". |
| ¿Qué pasa si se cae internet? | La línea sigue: la visión corre en el edge. Solo el asistente queda sin servicio. |
| ¿Qué pasa si el servicio de visión falla? | Reintentos, circuit breaker y el robot queda en HOLD. Nunca se inventa un resultado. |
| ¿Y si alguien le pide al agente "mueve el robot a 90°"? | Verifica el rol, crea una solicitud de aprobación y queda pausado hasta que otra persona autorizada apruebe. |
| ¿Cómo agregan un producto nuevo? | Se suben vistas, se valida que sean suficientes (cobertura del círculo), se crea un modelo candidato en staging, se mide y se pide aprobación para promoverlo. |
| ¿Qué cambia en una fábrica real? | Datos reales, calibración cámara-robot, iluminación controlada, piloto en sombra (el sistema predice pero el operario sigue) antes de quitar al operario. |
| ¿Cuánto cuesta? | ⟨Costo mensual y payback⟩ |

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
