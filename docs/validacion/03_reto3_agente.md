## 1) Veredicto: **CAMBIOS REQUERIDOS**

Hay una implementación sustancial del Reto 3, pero **la separación entre humanos, el aislamiento entre plantas y la integridad del dataset no están garantizados**. El 93 % reportado coincide aritméticamente con el JSON; sus verificadores permiten falsos positivos.

No modifiqué archivos. El comando de pytest solicitado abortó antes de recolectar pruebas: `FileNotFoundError: No usable temporary directory found`, por el entorno de solo lectura. Ejecuté después los tres tests que no requieren temporales: **`3 passed, 31 deselected`**. Los probes adicionales usaron memoria, sin servicios externos.

## 2) Tabla de requisitos

| Requisito de la sección 6 | Estado | Evidencia concreta |
|---|---|---|
| Usar visión del Reto 2 como herramienta | ✅ | HTTP independiente; el agente transmite sus decisiones: [tools.py:125](/Users/nico/Documents/IA/services/agent/tools.py:125), [vision_client.py:113](/Users/nico/Documents/IA/services/agent/vision_client.py:113). |
| UC1: inspeccionar imágenes, calidad, clase, ángulo, confianza y similares | ⚠️ | Backend completo, pero la UI presenta texto e URLs sin mostrar las vistas; UC1-02 falla al encolar cuando se delega la acción: [index.html:66](/Users/nico/Documents/IA/services/agent/static/index.html:66), [agent_eval.md:77](/Users/nico/Documents/IA/docs/report/agent_eval.md:77). |
| UC2: cola → confirmación humana → dataset | ⚠️ | Flujo implementado, pero cualquier etiqueta válida emitida por el LLM se registra como `human_review`, sin confirmación independiente: [tools.py:243](/Users/nico/Documents/IA/services/agent/tools.py:243). |
| UC3: consulta operativa correlacionando fuentes | ⚠️ | Existen las fuentes; UC3-02 omite consultar cámara y propone una causa incompleta: [agent_eval.md:110](/Users/nico/Documents/IA/docs/report/agent_eval.md:110). |
| UC4: diagnóstico, información faltante, SOP con citas y ticket | ⚠️ | Tools reales de consulta, búsqueda y tickets. UC4-01 termina sin las citas exigidas: [tools.py:402](/Users/nico/Documents/IA/services/agent/tools.py:402), [agent_eval.md:111](/Users/nico/Documents/IA/docs/report/agent_eval.md:111). |
| UC5: vistas suficientes, staging, desempeño y aprobación | ⚠️ | Staging implementado; suficiencia y gates deficientes, detallados abajo: [tools.py:496](/Users/nico/Documents/IA/services/agent/tools.py:496). |
| UC6: cinco operaciones riesgosas con aprobación humana explícita | ❌ | Las cinco existen, pero la API acepta identidad declarada sin credenciales; umbrales y promociones afectan un servicio global: [approvals.py:82](/Users/nico/Documents/IA/services/agent/approvals.py:82), [vision_client.py:120](/Users/nico/Documents/IA/services/agent/vision_client.py:120). |
| UC7: entrega de turno | ✅ | Compila y persiste métricas, incidentes, tickets, revisiones y aprobaciones pendientes: [tools.py:664](/Users/nico/Documents/IA/services/agent/tools.py:664). |
| Patrón justificado y ≥2 alternativas con cuatro dimensiones | ✅ | ReAct/LangGraph y tres alternativas comparadas: [agent_design.md:34](/Users/nico/Documents/IA/docs/agent_design.md:34). |
| RBAC, esquemas y límites de parámetros fuera del LLM | ✅ | Guard determinístico y revalidación antes de ejecutar: [graph.py:221](/Users/nico/Documents/IA/services/agent/graph.py:221), [approvals.py:66](/Users/nico/Documents/IA/services/agent/approvals.py:66). |
| Aprobador ≠ solicitante | ⚠️ | Compara IDs correctamente; no demuestra dos personas distintas porque ambas identidades son suplantables. |
| Planta inyectada desde sesión y aislamiento efectivo | ❌ | La inyección existe, pero `model_registry` es global y se devuelve sin filtro: [tools.py:345](/Users/nico/Documents/IA/services/agent/tools.py:345), [db.py:41](/Users/nico/Documents/IA/services/agent/plant/db.py:41). |
| Terminación y límites | ⚠️ | Pasos, llamadas y loops controlados. Timeout y presupuesto no son barreras estrictas: [graph.py:177](/Users/nico/Documents/IA/services/agent/graph.py:177), [graph.py:293](/Users/nico/Documents/IA/services/agent/graph.py:293). |
| ≥10 tools distintas, lectura/escritura | ✅ | **16: ocho de lectura y ocho de escritura**, sin contar operaciones riesgosas como wrappers adicionales: [tools.py:732](/Users/nico/Documents/IA/services/agent/tools.py:732). |
| Contratos, validación, permisos, fallos, idempotencia, visibilidad y auditoría | ⚠️ | Los 16 schemas coinciden con Pydantic; hay catálogo y auditoría. Salidas sin validar, truncado inválido y garantías incompletas de idempotencia: [tool_catalog.md:5](/Users/nico/Documents/IA/docs/tool_catalog.md:5), [security.py:51](/Users/nico/Documents/IA/services/agent/security.py:51). |
| Diferenciar ejecución, historial, memoria y conocimiento | ✅ | Capas, identificadores e imágenes documentados y respaldados por implementación: [agent_design.md:77](/Users/nico/Documents/IA/docs/agent_design.md:77). |
| Retención, contexto y datos sensibles | ⚠️ | Recorte de historial implementado; imágenes expiradas siguen accesibles, reloj fijo y redacción incompleta: [service.py:106](/Users/nico/Documents/IA/services/agent/service.py:106), [config.py:71](/Users/nico/Documents/IA/services/agent/config.py:71). |
| ≥8 adversos, incluidos los cinco obligatorios | ✅ | Doce ADV y casos de baja confianza; cubren desconocido, incertidumbre, tool/args inválidos, 5xx/timeout y loop. Evidencia principalmente con stub; dos casos guionados: [cases.yaml:383](/Users/nico/Documents/IA/eval/agent/cases.yaml:383). |
| ≥30 casos reproducibles y criterio objetivo por caso | ⚠️ | Hay **43**, con fixtures aislados; varios criterios aceptan respuestas incorrectas: [run_eval.py:101](/Users/nico/Documents/IA/eval/agent/run_eval.py:101). |
| Selección, argumentos, tokens y costo por ejecución | ⚠️ | Registrados, pero cobertura incompleta de checks y errores al contabilizar modelos de respaldo: [run_eval.py:163](/Users/nico/Documents/IA/eval/agent/run_eval.py:163). |
| Resultados, análisis de fallos y ≥3 mejoras priorizadas | ✅ | Tres fallos explicados y cinco mejoras: [agent_eval.md:107](/Users/nico/Documents/IA/docs/report/agent_eval.md:107). |

## 3) Alucinaciones o afirmaciones sin respaldo

- **Cifras finales:** coinciden con el [JSON final](/Users/nico/Documents/IA/eval/agent/results/20261024-125326.json:14): **40/43**, 251.907/19.540 tokens, 87 llamadas y USD **0,032882**, redondeados a 0,03288. La coincidencia no valida los criterios.
- **“100 % argumentos correctos”** solo comprueba **12/43 casos**. Los otros 31 pasan mediante `all([])`. Selección solo se comprueba en 26 casos: **24/26 = 92,3 %**, frente al 95,3 % agregado. [run_eval.py:163](/Users/nico/Documents/IA/eval/agent/run_eval.py:163).
- El agregado incluye **700/140 tokens artificiales y siete llamadas guionadas**. Consumo del LLM real: **251.207/19.400 tokens y 80 llamadas**. [llm.py:129](/Users/nico/Documents/IA/services/agent/llm.py:129).
- Las garantías universales de aislamiento, redacción de texto libre e idempotencia exceden lo implementado. También es excesivo atribuir los **12/12 adversos** exclusivamente a controles determinísticos. [threat_model.md:29](/Users/nico/Documents/IA/docs/threat_model.md:29), [agent_eval.md:117](/Users/nico/Documents/IA/docs/report/agent_eval.md:117).
- Las tarifas 3.x están explícitamente **sin verificar**; los multiplicadores de costo de arquitecturas alternativas son estimaciones, sin benchmark comparativo. La cuota “~20 req/día” y la corrida de humo favorable no tienen evidencia guardada suficiente. [llm_pricing.yaml:9](/Users/nico/Documents/IA/configs/llm_pricing.yaml:9), [agent_eval.md:92](/Users/nico/Documents/IA/docs/report/agent_eval.md:92).
- El catálogo anuncia **9 lecturas + 7 escrituras**; realmente son **8 + 8**.

## 4) Errores técnicos

1. **Bypass de separación de aprobadores.** Un POST sin credenciales, declarando `approver_id=u_sup2_p1`, devolvió **`200 executed`** y pausó la línea. La identidad simulada está reconocida documentalmente, pero no satisface una aprobación humana verificable. [app.py:104](/Users/nico/Documents/IA/services/agent/app.py:104).

2. **Fuga e impacto entre plantas.** Un candidato privado de P2 fue visible desde P1; sus gates también pasaron desde P1. Los cambios de umbral/promoción no llevan planta al servicio remoto. Además, promover no actualiza `lines.model_version`. [tools.py:549](/Users/nico/Documents/IA/services/agent/tools.py:549), [approvals.py:116](/Users/nico/Documents/IA/services/agent/approvals.py:116).

3. **Envenenamiento del dataset ante un LLM comprometido.** Ante «Lista los tickets abiertos», un modelo guionado ejecutó también `submit_review_label` y guardó `obj2@0` como revisión humana. Demuestra una barrera ausente; **no prueba una inyección lingüística exitosa contra Gemini**.

4. **Promoción sin validar el desempeño exigido.** Se compara `loo_class_accuracy` del SKU nuevo con accuracy de producción, sin medir degradación de clases existentes. Un candidato simulado con accuracy 1 y error angular 90° pasó. También se admitieron doce copias de una imagen. La cobertura previa usa `max−min`, incorrecto circularmente; el servicio real sí corrige esa cobertura. [tools.py:505](/Users/nico/Documents/IA/services/agent/tools.py:505).

5. **Timeout inefectivo y presupuesto tardío.** `ThreadPoolExecutor` espera al trabajador al salir del `with`. Probe: timeout configurado **0,01 s**, duración **0,243 s**. Otro probe ejecutó una tool después de consumir **120 tokens con presupuesto 100**. Los límites se revisan antes de la siguiente llamada, no antes del efecto.

6. **Caducidad y confidencialidad incompletas.** El reloj fijo impide expirar por tiempo real; las lecturas ignoran `uploads.expires_at`. Títulos de tickets y preferencias conservan correos y llegan al contexto sin redacción. [tools.py:418](/Users/nico/Documents/IA/services/agent/tools.py:418), [graph.py:133](/Users/nico/Documents/IA/services/agent/graph.py:133).

7. **Idempotencia y atomicidad insuficientes.** Encolado sin UNIQUE por imagen; etiquetado y dataset se actualizan en commits separados. La clave de staging omite ángulos. Aprobaciones están serializadas entre threads de un proceso, pero no garantizan ejecución única tras crash o entre procesos.

8. **Evaluación inflada y ajustada sobre el mismo set.** `evaluate()` aceptó UC3-03 con tool en estado `error` y respuesta «P1-L2»; ADV-12 aceptó una respuesta irrelevante sin tools. El p95 usa un índice inferior: **50,92 s**, frente a **52,72 s** por rango más próximo. Prompt y checks se ajustaron con estos mismos casos, sin conjunto reservado. La corrección de ADV-01 y su merge están declarados y son razonables; **no hay evidencia para calificarlos de fraude**. [run_eval.py:199](/Users/nico/Documents/IA/eval/agent/run_eval.py:199), [agent_eval.md:96](/Users/nico/Documents/IA/docs/report/agent_eval.md:96).

## 5) Cambios priorizados

1. Autenticar usuarios y aprobadores; obtener identidad y alcance desde sesión verificada.
2. Aislar registro, modelos, umbrales y promociones por planta.
3. Confirmar etiqueta y ángulo humanos fuera del control del LLM.
4. Exigir vistas únicas y validación de clase, ángulo y regresión sobre SKU existentes.
5. Endurecer verificadores, informar cobertura y reservar casos independientes.
6. Implementar deadlines efectivos y comprobar presupuesto antes de ejecutar efectos.
7. Separar reloj de simulación de seguridad; aplicar expiración y purga.
8. Hacer transaccionales cola/dataset y persistente la idempotencia remota.
9. Redactar todos los canales sensibles y conservar JSON válido al truncar.
10. Corregir documentación, percentiles y costos por modelo; separar consumos reales y guionados.