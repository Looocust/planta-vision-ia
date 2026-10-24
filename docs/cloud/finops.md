# FinOps: modelo de costos y prácticas

Modelo con fórmulas vivas: [`finops_model.xlsx`](finops_model.xlsx) — se regenera con
`uv run python scripts/build_finops.py` (o `make finops`).

## 1. Cómo leer el modelo

| Hoja | Contenido |
|---|---|
| **Supuestos** | Todas las entradas (celdas amarillas). Cada una con unidad, tipo (*Supuesto* / *Referencia* / *Calculado*), fuente y fecha. Tienen nombre definido (p. ej. `pct_automatizado`) para que las fórmulas se lean solas |
| **Operación actual** | Costo del operador por estación: horas 24×5 × factor de cobertura × costo cargado/hora, en Colombia y España |
| **Azure** / **AWS** | Una fila por servicio: precio unitario × cantidad mensual (fórmula) × incluir (1/0). Columna *Estado*: **API verificada** (Azure Retail Prices API, 2026-10-24) o **Estimado** (resaltado) |
| **Comparativo** | Actual vs Azure vs AWS en ambos países: OPEX nuevo (nube + mantenimiento de borde + humano residual), ahorro, CAPEX, payback, ahorro neto a 3 años |
| **Sensibilidad** | % automatizado 80/90/95/99, estaciones 1/5/20, conversaciones/día 20/60/200/1000 |
| **Fuentes** | URLs, método y limitaciones |

`pct_automatizado` es un **placeholder (95%)** que debe reemplazarse por la cobertura ACT medida del modelo (reporte).

## 2. Resultados con los supuestos base (1 planta)

**Qué es supuesto y qué no:** los costos laborales, throughput, CAPEX, % automatizado y volúmenes del agente son
**supuestos** del candidato (editables). Los precios de Azure marcados "API verificada" son **precios de lista
reales** de East US 2; los de AWS y algunos de Azure son **estimados**.

| (USD/mes salvo indicación) | Colombia · Azure | Colombia · AWS | España · Azure | España · AWS |
|---|---|---|---|---|
| Operación actual (1 estación) | 2.381 | 2.381 | 12.283 | 12.283 |
| Nube total | 519 | 397 | 519 | 397 |
| Humano residual (cola + supervisión) | 756 | 756 | 3.899 | 3.899 |
| Costo mensual nuevo | 1.320 | 1.198 | 4.463 | 4.341 |
| **Ahorro mensual** | **1.061 (45%)** | **1.183 (50%)** | **7.820 (64%)** | **7.942 (65%)** |
| CAPEX (1 estación + implementación) | 50.500 | 50.500 | 50.500 | 50.500 |
| **Payback** | **≈ 48 meses** | ≈ 43 meses | **≈ 6,5 meses** | ≈ 6,4 meses |

**Sensibilidad (Azure):**

| | Colombia | España |
|---|---|---|
| Payback con 5 estaciones | ≈ 18 meses | ≈ 3,2 meses |
| Payback con 20 estaciones | ≈ 14 meses | ≈ 2,7 meses |
| Ahorro mensual con 80% automatizado (1 estación) | **negativo** (−492) | **negativo** (−191) |
| Ahorro mensual con 99% automatizado (1 estación) | 1.475 | 9.956 |

**Mensajes para defender:**
1. La nube cuesta **~USD 0,0017 por objeto**; el gasto LLM (~USD 20/mes) es irrelevante frente a la mano de obra.
2. El caso de negocio **depende de la tasa de automatización**: con 80% la cola de revisión consume casi un operador
   y el proyecto no se paga. Por eso el sistema optimiza ACT *seguro* y mide la cobertura con rigor.
3. En **Colombia** una estación sola no se justifica (48 meses); el caso existe **a escala** (≥ 5 estaciones) porque
   el costo de implementación por planta se reparte. En **España** se paga en meses incluso con una estación.
4. El humano residual (supervisión itinerante 0,1 FTE + cola) es ahora **el mayor costo**; reducir minutos por caso
   (buena UI de revisión, evidencia de vecinos similares) vale más que optimizar la nube.

## 3. Prácticas FinOps

### Visibilidad y asignación
- **Etiquetado obligatorio** (Azure Policy *deny* / AWS Tag Policies + SCP): `planta`, `linea`, `entorno`,
  `componente` (`edge|agent|vision|data|ml`), `centro_costo`, `owner`.
- **Showback por planta**: la estampa por RG/cuenta hace el costo por planta directo. El costo LLM compartido se
  reparte por `plant_id`, que el agente ya registra con tokens y `cost_usd` en cada respuesta (`usage`).
- **KPI unitarios** en el tablero: costo de nube por objeto procesado, costo LLM por conversación, % del costo
  laboral evitado.

### Presupuestos y alertas
- **Budgets** por RG/cuenta con alertas a 50/80/100% y **anomalías de costo** (Cost Management / Cost Anomaly Detection).
- **Presupuesto diario de tokens** por planta y por usuario en el agente: al superarlo, degrada a modelo pequeño y luego
  bloquea conversaciones no críticas (el ciclo del robot no se ve afectado).

### Optimización de cómputo
- **Agente escala a cero** fuera de turnos (fines de semana) y visión cloud escala a cero siempre.
- **Reservas / Savings Plans** solo para lo constante: PostgreSQL/Aurora y, con varias plantas, el cómputo base
  (reserva de 1 año en PostgreSQL ≈ 30–40% menos; *estimado*). El PC de borde es CAPEX (no aplica).
- **Pilotos sin HA** (`replicas_bd = 1`) y B-series; HA solo en producción.
- Cluster de entrenamiento con **mínimo 0 nodos** y spot/low-priority para reentrenamientos no urgentes.

### Control de costo del LLM
- **Enrutamiento a modelo pequeño** por defecto (gpt-4.1-mini / Haiku); el grande solo para diagnósticos (15%).
  La hoja *Sensibilidad* muestra cuánto costaría mandar todo al grande.
- **Prompt caching**: prompt de sistema y definiciones de tools estables al inicio del prompt → caché (≈ 75% de
  descuento en tokens cacheados en Azure OpenAI; ~90% en lectura de caché en Bedrock).
- **Presupuesto de tokens por conversación** (máx. iteraciones del grafo, recorte de resultados de tools,
  resumen de historial largo).
- **Las tareas deterministas no usan LLM**: métricas, cola y estado se consultan con tools; el LLM solo redacta.
- **Batch** (50% de descuento) para tareas no interactivas: resumen nocturno de turno, reetiquetado sugerido.
- PTU solo si el gasto sostenido supera el costo mínimo de capacidad dedicada.

### Datos
- Subir solo REVIEW/REJECT + muestra de ACT; **ciclo de vida** Hot→Cool→Archive y borrado a 3 años.
- Muestreo de trazas y retención corta de logs detallados (30 días) con archivo barato para auditoría.

### Gobierno
- Revisión mensual de costos con el gerente de planta (costo por objeto y % automatizado juntos).
- Toda la infraestructura en IaC: no hay recursos "huérfanos" fuera de las estampas.
