# FinOps: modelo de costos y prácticas

Modelo con fórmulas vivas: [`finops_model.xlsx`](finops_model.xlsx) — se regenera con
`uv run python scripts/build_finops.py` (o `make finops`).

## 1. Cómo leer el modelo

| Hoja | Contenido |
|---|---|
| **Supuestos** | Todas las entradas (celdas amarillas) con unidad, tipo (*Supuesto* / *Placeholder* / *Referencia* / *Calculado*), fuente y fecha. Nombres definidos (p. ej. `pct_automatizado`) para que las fórmulas se lean solas |
| **Operación actual** | Costo del operador por estación: horas 24×5 × factor de cobertura × costo cargado/hora, Colombia y España |
| **Azure** / **AWS** | Una fila por servicio con **dos precios por región**: EE. UU. (East US 2 / us-east-1 → escenario Colombia) y UE-España (Spain Central / eu-south-2 → escenario España). Costo = precio × cantidad × incluir. Columna *Estado*: **API verificada** o **Estimado** (resaltado) |
| **Comparativo** | Actual vs Azure vs AWS en ambos países, cada país con los precios de su región: OPEX nuevo (nube + mantenimiento de borde + humano residual), ahorro, CAPEX, payback, ahorro neto a 3 años |
| **Sensibilidad** | % automatizado 80/90/95/99 (también escala el almacenamiento de imágenes), estaciones 1/5/20, conversaciones/día 20/60/200/1000 (la columna "todo al modelo grande" lee las celdas de precio gpt-4.1) |
| **Fuentes** | Una fila por consulta de precio: clave, región, precio, unidad, **medidor / usagetype**, id de medidor o SKU, producto, fecha y la **URL de consulta exacta**. Snapshot en `docs/cloud/precios_snapshot.json` |

`pct_automatizado` es un **placeholder (95%) sin evidencia**: debe reemplazarse por la cobertura ACT medida del modelo.
`fraccion_atencion` es la fracción de las horas de operación de la estación que todavía requiere a una persona
(10% → ≈ 60 h pagadas/mes con cobertura); no es un FTE.

**Trazabilidad de precios:** `uv run python scripts/build_finops.py --refresh-prices` vuelve a consultar la Azure Retail
Prices API y la AWS Price List Bulk API (públicas, sin credenciales) y regenera el snapshot y el libro. Precios sin medidor
localizado quedan como "Estimado": Bedrock (Claude), NAT Gateway, GuardDuty, SSM, Private Endpoints de PaaS en Azure,
Defender for Storage y transacciones de almacenamiento. Para la UE, si no hay precio verificado se aplica `uplift_ue` (×1,10).

## 2. Resultados con los supuestos base (1 planta, 1 estación)

**Qué es supuesto y qué no:** costos laborales, throughput, CAPEX, % automatizado y volúmenes del agente son
**supuestos** editables. Los precios de nube marcados "API verificada" son **precios de lista reales** consultados el
2026-10-24. Colombia usa jornada de 42 h/semana (vigente desde el 15-jul-2026).

| USD/mes salvo indicación | Colombia · Azure | Colombia · AWS | España · Azure | España · AWS |
|---|---|---|---|---|
| Región de precios | East US 2 | us-east-1 | Spain Central | eu-south-2 |
| Operación actual | 2.485 | 2.485 | 12.283 | 12.283 |
| Nube total | 637 | 410 | 741 | 464 |
| Humano residual (cola + atención itinerante) | 789 | 789 | 3.899 | 3.899 |
| Costo mensual nuevo | 1.472 | 1.245 | 4.685 | 4.408 |
| **Ahorro mensual** | **1.013 (41%)** | **1.241 (50%)** | **7.598 (62%)** | **7.875 (64%)** |
| CAPEX (1 estación + implementación) | 50.500 | 50.500 | 50.500 | 50.500 |
| **Payback** | **≈ 50 meses** | ≈ 41 meses | **≈ 6,6 meses** | ≈ 6,4 meses |

**Sensibilidad (Azure):**

| | Colombia | España |
|---|---|---|
| Payback con 5 estaciones | ≈ 18 meses | ≈ 3,2 meses |
| Payback con 20 estaciones | ≈ 14 meses | ≈ 2,7 meses |
| Ahorro mensual con 80% automatizado (1 estación) | **negativo** (−608) | **negativo** (−413) |
| Ahorro mensual con 99% automatizado (1 estación) | 1.446 | 9.735 |

**Mensajes para defender:**
1. La nube cuesta **~USD 0,002 por objeto**; el gasto LLM (~USD 20/mes) es irrelevante frente a la mano de obra.
2. El caso de negocio **depende de la tasa de automatización**: con 80% la cola de revisión consume casi un operador
   y el proyecto no se paga. Por eso el sistema optimiza ACT *seguro* y la cifra real debe salir del reporte del modelo.
3. En **Colombia** una estación sola no se justifica (≈ 50 meses) y la diferencia Azure-AWS (≈ 22% del ahorro) sí
   importa: hay que recortar el diseño del piloto. El caso existe **a escala** (≥ 5 estaciones). En **España** se paga en meses.
4. El humano residual es ahora **el mayor costo**; reducir minutos por caso vale más que optimizar la nube.

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
