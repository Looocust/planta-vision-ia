# FinOps: modelo de costos y prácticas

Modelo con fórmulas vivas: [`finops_model.xlsx`](finops_model.xlsx) — se regenera con
`uv run python scripts/build_finops.py` (o `make finops`).

## 1. Cómo leer el modelo

| Hoja | Contenido |
|---|---|
| **Supuestos** | Todas las entradas (celdas amarillas) con unidad, tipo (*Supuesto* / *Medido* / *Referencia* / *Calculado*), fuente y fecha. Nombres definidos (p. ej. `pct_automatizado`) para que las fórmulas se lean solas |
| **Operación actual** | Costo del operador por estación: horas 24×5 × factor de cobertura × costo cargado/hora, Colombia y España |
| **Azure** / **AWS** | Una fila por servicio con **dos precios por región**: EE. UU. (East US 2 / us-east-1 → escenario Colombia) y UE-España (Spain Central / eu-south-2 → escenario España). Costo = precio × cantidad × incluir. Columna *Estado*: **API verificada** o **Estimado** (resaltado) |
| **Comparativo** | Actual vs Azure vs AWS en ambos países, cada país con los precios de su región: OPEX nuevo (nube + mantenimiento de borde + humano residual), ahorro, CAPEX, payback, ahorro neto a 3 años |
| **Sensibilidad** | % automatizado 73,7/74,9/80/90/90,3/95/99 + punto de equilibrio (también escala el almacenamiento de imágenes), estaciones 1/5/20, conversaciones/día 20/60/200/1000 (la columna "todo al modelo grande" lee las celdas de precio gpt-4.1) |
| **Fuentes** | Una fila por consulta de precio: clave, región, precio, unidad, **medidor / usagetype**, id de medidor o SKU, producto, fecha y la **URL de consulta exacta**. Snapshot en `docs/cloud/precios_snapshot.json` |

`pct_automatizado` = **90,3% (medido)**: cobertura ACT en test de `vision-1.0.1`; el 73,7-74,9% con desconocidos está en *Sensibilidad*.
`fraccion_atencion` es la fracción de las horas de operación de la estación que todavía requiere a una persona
(10% → ≈ 60 h pagadas/mes con cobertura); no es un FTE.

**Trazabilidad de precios:** `uv run python scripts/build_finops.py --refresh-prices` vuelve a consultar la Azure Retail
Prices API y la AWS Price List Bulk API (públicas, sin credenciales) y regenera el snapshot y el libro. Precios sin medidor
localizado quedan como "Estimado": Bedrock (Claude), NAT Gateway, GuardDuty, SSM, Private Endpoints de PaaS en Azure,
Defender for Storage y transacciones de almacenamiento. Para la UE, si no hay precio verificado se aplica `uplift_ue` (×1,10).

## 2. Resultados con los supuestos base (1 planta, 1 estación)

**Qué es supuesto y qué no:** `pct_automatizado` = **90,3% medido** (cobertura ACT en test, protocolo A, modelo
`vision-1.0.1`, 0,09% de error entre los ACT). Con objetos desconocidos mezclados (variante D) la cobertura medida baja a
**73,7-74,9%** (filas propias en *Sensibilidad*). Costos laborales, throughput, CAPEX y volúmenes del agente son
**supuestos** editables. Los precios de nube marcados "API verificada" son **precios de lista reales** consultados el
2026-10-24. Colombia usa jornada de 42 h/semana (vigente desde el 15-jul-2026).

**Tabla principal (1 planta, 90,3% automatizado):**

| USD/mes salvo indicación | Colombia · Azure | Colombia · AWS | España · Azure | España · AWS |
|---|---|---|---|---|
| Región de precios | East US 2 | us-east-1 | Spain Central | eu-south-2 |
| Operación actual (1 estación) | 2.485 | 2.485 | 12.283 | 12.283 |
| Nube total (1 estación) | 638 | 410 | 741 | 464 |
| Humano residual (cola + atención itinerante) | 1.297 | 1.297 | 6.409 | 6.409 |
| Costo mensual nuevo | 1.980 | 1.753 | 7.195 | 6.919 |
| **Ahorro mensual (1 estación)** | **505 (20%)** | **733 (29%)** | **5.088 (41%)** | **5.365 (44%)** |
| CAPEX (1 estación + implementación) | 50.500 | 50.500 | 50.500 | 50.500 |
| **Payback 1 estación** | **≈ 100 meses** | ≈ 69 meses | **≈ 9,9 meses** | ≈ 9,4 meses |
| Nube total (5 estaciones) | ≈ 745 | ≈ 502 | ≈ 850 | ≈ 565 |
| **Payback 5 estaciones** | **≈ 27 meses** | ≈ 25 meses | **≈ 4,7 meses** | ≈ 4,6 meses |
| **% automatizado de equilibrio** (ahorro = 0, 1 estación) | **85,6%** | 83,5% | **80,8%** | 80,3% |

**Sensibilidad al % automatizado (Azure, 1 estación):**

| % automatizado | Ahorro CO (USD/mes) | Payback CO | Ahorro ES (USD/mes) | Payback ES |
|---|---|---|---|---|
| 73,7% (medido, variante D con desconocidos) | **−1.290** | No recupera | **−3.778** | No recupera |
| 74,9% (medido, variante D) | −1.160 | No recupera | −3.137 | No recupera |
| 80% | −608 | No recupera | −413 | No recupera |
| 90% | 473 | ≈ 107 meses | 4.928 | ≈ 10,2 meses |
| **90,3% (medido, protocolo A)** | **505** | **≈ 100 meses** | **5.088** | **≈ 9,9 meses** |
| 95% | 1.013 | ≈ 50 meses | 7.598 | ≈ 6,6 meses |
| 99% | 1.446 | ≈ 35 meses | 9.735 | ≈ 5,2 meses |

Con 20 estaciones (Azure, 90,3%): payback ≈ 20 meses en Colombia y ≈ 3,8 en España.

**Mensajes para defender:**
1. La nube cuesta **~USD 0,002 por objeto**; el gasto LLM (~USD 20/mes) es irrelevante frente a la mano de obra.
2. El caso de negocio **depende de la tasa de automatización**. El equilibrio está en **~81-86%**: con el 90,3% medido
   hay ahorro en ambos países, con poco margen en Colombia; con el 74% medido cuando aparecen objetos desconocidos,
   **no hay ahorro**. Por eso es crítico mantener el catálogo de SKUs al día (alta de SKU) y reducir los desconocidos en la línea.
3. En **Colombia** una estación sola no se justifica (≈ 100 meses) y la diferencia Azure-AWS (≈ 45% del ahorro) sí
   importa: hay que recortar el diseño del piloto. El caso existe **a escala** (≈ 27 meses con 5 estaciones, ≈ 20 con 20).
   En **España** se paga en menos de un año incluso con una estación.
4. El humano residual es ahora **el mayor costo** (cola de revisión ≈ 42% del costo actual con 90,3%); reducir minutos
   por caso o subir la cobertura segura vale mucho más que optimizar la nube.

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
