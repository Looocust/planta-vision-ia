"""Genera docs/cloud/finops_model.xlsx con fórmulas vivas.

    uv run python scripts/build_finops.py

Todas las celdas de entrada (azul) viven en la hoja "Supuestos" y tienen un
nombre definido (p. ej. `estaciones`, `pct_automatizado`). Las demás hojas
solo contienen fórmulas que referencian esos nombres, así que cambiar un
supuesto recalcula todo el libro al abrirlo en Excel / LibreOffice / Sheets.

Precios: los marcados "API verificada" se consultaron el 2026-10-24 en la API
pública de precios minoristas de Azure (https://prices.azure.com/api/retail/prices,
región eastus2). Los marcados "Estimado" provienen de las páginas públicas de
precios o de referencias de mercado y deben verificarse antes de presupuestar.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

OUT = Path(__file__).resolve().parents[1] / "docs" / "cloud" / "finops_model.xlsx"
FECHA = "2026-10-24"
AZ_API = "https://prices.azure.com/api/retail/prices (eastus2)"

# ---------------------------------------------------------------- estilos
BLUE = Font(color="1F4E9E", bold=True)
BOLD = Font(bold=True)
H1 = Font(bold=True, size=14, color="FFFFFF")
HDR = Font(bold=True, color="FFFFFF")
FILL_H1 = PatternFill("solid", fgColor="1F3864")
FILL_HDR = PatternFill("solid", fgColor="2F5597")
FILL_IN = PatternFill("solid", fgColor="FFF2CC")
FILL_SEC = PatternFill("solid", fgColor="D9E1F2")
FILL_TOT = PatternFill("solid", fgColor="E2EFDA")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")

USD = '"$"#,##0.00'
USD0 = '"$"#,##0'
PCT = "0.0%"
NUM = "#,##0.00"
INT = "#,##0"

wb = Workbook()


def name(ws, ref: str, nm: str) -> None:
    col, row = "".join(c for c in ref if c.isalpha()), "".join(c for c in ref if c.isdigit())
    wb.defined_names[nm] = DefinedName(nm, attr_text=f"'{ws.title}'!${col}${row}")


def title(ws, text: str, sub: str, width: int) -> None:
    ws["A1"] = text
    ws["A1"].font = H1
    for c in range(1, width + 1):
        ws.cell(row=1, column=c).fill = FILL_H1
    ws["A2"] = sub
    ws["A2"].alignment = Alignment(wrap_text=True)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=width)
    ws.row_dimensions[2].height = 45


def header(ws, row: int, cols: list[str]) -> None:
    for i, h in enumerate(cols, start=1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = HDR
        c.fill = FILL_HDR
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = BOX


def widths(ws, ws_widths: list[int]) -> None:
    for i, w in enumerate(ws_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


# =================================================================== SUPUESTOS
sup = wb.active
sup.title = "Supuestos"
title(
    sup,
    "Supuestos del modelo FinOps (todo lo editable está aquí)",
    "Celdas amarillas con texto azul = entradas editables. Las demás hojas solo tienen fórmulas. "
    "Tipo: 'Supuesto' = valor elegido por el candidato y defendible pero no verificado; "
    "'Referencia' = dato público con fuente; 'Calculado' = fórmula. Moneda de salida: USD.",
    7,
)
header(sup, 4, ["Nombre (rango)", "Descripción", "Valor", "Unidad", "Tipo", "Fuente / justificación", "Fecha"])

# (nombre, descripción, valor o fórmula, unidad, tipo, fuente, formato)
SUPUESTOS: list[tuple | str] = [
    "Operación de la planta",
    ("plantas", "Plantas en el alcance", 1, "plantas", "Supuesto", "Piloto en una planta; el modelo escala por estaciones", INT),
    ("estaciones", "Estaciones (líneas de empaque) por planta", 1, "estaciones", "Supuesto", "Caso base del enunciado: 1 estación. Ver hoja Sensibilidad para 5 y 20", INT),
    ("horas_semana", "Horas de operación por semana", 120, "h/semana", "Referencia", "Enunciado: 24 h/día × 5 días/semana (3 turnos de 8 h)", INT),
    ("semanas_mes", "Semanas promedio por mes", "=52/12", "semanas", "Calculado", "52 semanas / 12 meses", NUM),
    ("horas_mes", "Horas de operación por mes", "=horas_semana*semanas_mes", "h/mes", "Calculado", "120 × 4,33 ≈ 520 h", NUM),
    ("dias_mes", "Días de operación por mes", "=5*semanas_mes", "días/mes", "Calculado", "5 días × 4,33", NUM),
    ("operadores_turno", "Operadores por turno por estación", 1, "personas", "Referencia", "Enunciado: 1 operador por turno por estación", INT),
    ("factor_cobertura", "Factor de cobertura (vacaciones, ausentismo, relevos)", 1.15, "×", "Supuesto", "Cubrir 1 puesto 24×5 requiere ~15% más horas pagadas que horas de puesto", NUM),
    ("throughput_obj_h", "Objetos procesados por hora por estación", 600, "obj/h", "Supuesto", "1 objeto cada 6 s. Ajustar con el dato real de la línea", INT),
    "Desempeño del sistema automático",
    ("pct_automatizado", "% de objetos resueltos sin humano (decisión ACT correcta)", 0.95, "%", "Supuesto", "ACTUALIZAR con la cobertura ACT del modelo (docs/report). Placeholder conservador", PCT),
    ("min_caso_revision", "Minutos humanos por caso en cola (REVIEW/REJECT)", 0.5, "min/caso", "Supuesto", "Mirar evidencia en UI + reubicar físicamente el objeto desviado (~30 s)", NUM),
    ("fte_residual", "FTE residual por estación (supervisión, atascos, reabastecer)", 0.10, "FTE", "Supuesto", "Persona itinerante que atiende varias estaciones", NUM),
    "Costo laboral — escenario Colombia",
    ("salario_co_cop", "Salario base mensual operador", 1750000, "COP/mes", "Supuesto", "≈1,2 SMMLV 2025 (SMMLV 2025 = 1.423.500 COP, Decreto 1572/2024). Actualizar a 2026", INT),
    ("factor_prest_co", "Factor prestacional (prestaciones, seguridad social, parafiscales, aux. transporte)", 1.55, "×", "Supuesto", "Rango típico 1,50–1,60 para salarios bajos en Colombia", NUM),
    ("horas_lab_mes_co", "Horas laborales contratadas por mes", 190, "h/mes", "Supuesto", "Jornada de 44 h/semana (Ley 2101/2021, tramo 2025-2026) × 4,33", INT),
    ("pct_nocturno", "% de horas en franja nocturna", 0.33, "%", "Supuesto", "1 de 3 turnos nocturno", PCT),
    ("recargo_noct_co", "Recargo nocturno", 0.35, "%", "Referencia", "Código Sustantivo del Trabajo art. 168: 35%", PCT),
    ("trm_cop_usd", "Tasa de cambio COP por USD", 4000, "COP/USD", "Supuesto", "Verificar TRM del día (banrep.gov.co)", INT),
    ("costo_hora_co", "Costo cargado por hora-persona (Colombia)", "=salario_co_cop*factor_prest_co/horas_lab_mes_co/trm_cop_usd*(1+recargo_noct_co*pct_nocturno)", "USD/h", "Calculado", "Salario × factor / horas / TRM × recargo nocturno ponderado", USD),
    "Costo laboral — escenario España",
    ("salario_es_eur", "Salario bruto anual operador", 22000, "EUR/año", "Supuesto", "Rango de convenios de industria/logística para mozo/operador de línea", INT),
    ("ss_empresa_es", "Seguridad Social a cargo de la empresa", 0.32, "%", "Supuesto", "≈31-33% (contingencias comunes, desempleo, FOGASA, FP, AT/EP)", PCT),
    ("horas_anuales_es", "Horas efectivas anuales por convenio", 1760, "h/año", "Supuesto", "Rango 1.750–1.800 h según convenio", INT),
    ("plus_noct_es", "Plus de nocturnidad", 0.25, "%", "Supuesto", "Estatuto de los Trabajadores art. 36 remite a convenio; 25% es habitual", PCT),
    ("usd_por_eur", "USD por EUR", 1.15, "USD/EUR", "Supuesto", "Verificar tipo de cambio del día (BCE)", NUM),
    ("costo_hora_es", "Costo cargado por hora-persona (España)", "=salario_es_eur*(1+ss_empresa_es)/horas_anuales_es*usd_por_eur*(1+plus_noct_es*pct_nocturno)", "USD/h", "Calculado", "Bruto × (1+SS) / horas × USD/EUR × plus nocturno ponderado", USD),
    "Imágenes y telemetría",
    ("muestreo_act", "% de imágenes ACT que se suben a la nube (auditoría / reentrenamiento)", 0.02, "%", "Supuesto", "Todas las REVIEW/REJECT se suben; de las ACT solo una muestra", PCT),
    ("pct_subidas", "% de imágenes subidas a la nube", "=(1-pct_automatizado)+pct_automatizado*muestreo_act", "%", "Calculado", "No-ACT + muestra de ACT", PCT),
    ("kb_imagen", "Tamaño medio por imagen subida (JPEG, rostros difuminados)", 150, "KB", "Supuesto", "Cámara ~1,3 MP comprimida", INT),
    ("gb_imagenes_mes_est", "GB de imágenes nuevas por mes por estación", "=throughput_obj_h*horas_mes*pct_subidas*kb_imagen/1024/1024", "GB/mes", "Calculado", "", NUM),
    ("dias_hot", "Días en tier caliente (Hot / S3 Standard)", 30, "días", "Supuesto", "Revisión y reentrenamiento recientes", INT),
    ("dias_cool", "Días en tier frío (Cool / S3 Standard-IA)", 150, "días", "Supuesto", "Del día 31 al 180", INT),
    ("dias_archivo", "Días en archivo (Archive / Glacier Deep Archive)", 915, "días", "Supuesto", "Hasta completar 3 años de retención (auditoría de calidad)", INT),
    ("msgs_dia_estacion", "Mensajes de telemetría por día por estación", 3000, "msgs/día", "Supuesto", "Métricas agregadas cada 30 s + eventos de decisión agregados", INT),
    ("gb_logs_mes", "GB de logs / trazas por mes (plataforma)", 10, "GB/mes", "Supuesto", "Logs de apps + trazas OpenTelemetry del agente, con muestreo", INT),
    "Asistente agéntico (LLM)",
    ("conv_dia_base", "Conversaciones por día (base por planta)", 40, "conv/día", "Supuesto", "Supervisores, calidad, mantenimiento, entrega de turno ×3", INT),
    ("conv_dia_estacion", "Conversaciones adicionales por día por estación", 20, "conv/día", "Supuesto", "Consultas de cola, diagnósticos, imágenes", INT),
    ("conv_mes", "Conversaciones por mes", "=(conv_dia_base+conv_dia_estacion*estaciones)*dias_mes", "conv/mes", "Calculado", "", INT),
    ("tok_in_conv", "Tokens de entrada por conversación (multi-turno, con tools)", 25000, "tokens", "Supuesto", "Prompt de sistema + 13 tools + resultados de tools; medir con usage del agente", INT),
    ("tok_out_conv", "Tokens de salida por conversación", 1500, "tokens", "Supuesto", "", INT),
    ("pct_cache", "% de tokens de entrada servidos desde caché de prompt", 0.50, "%", "Supuesto", "Prompt de sistema y definiciones de tools son estables → cacheables", PCT),
    ("pct_modelo_grande", "% de conversaciones enrutadas al modelo grande", 0.15, "%", "Supuesto", "Solo diagnósticos complejos; el resto va al modelo pequeño", PCT),
    ("tok_embed_mes", "Tokens de embeddings por mes (reindexado de SOP)", 2000000, "tokens", "Supuesto", "Corpus SOP pequeño, reindexado semanal", INT),
    "CAPEX e implementación (pago único)",
    ("capex_pc", "PC industrial fanless por estación (CPU, 16-32 GB, IP65 opcional)", 3000, "USD", "Supuesto", "Rango de mercado USD 2.000–4.000 (Advantech, Siemens IPC, OnLogic)", USD0),
    ("capex_camara", "Cámara industrial + óptica + iluminación + soporte", 2500, "USD", "Supuesto", "Cámara GigE/USB3 + lente + domo/anillo LED", USD0),
    ("capex_integracion", "Integración con PLC / robot y puesta en marcha por estación", 15000, "USD", "Supuesto", "Ingeniería de automatización, calibración cámara-robot, FAT/SAT", USD0),
    ("capex_planta", "Implementación por planta (piloto, validación, capacitación, nube)", 30000, "USD", "Supuesto", "Equipo de proyecto ~2-3 meses (no incluye el desarrollo ya hecho en la demo)", USD0),
    ("pct_mant_hw", "Mantenimiento anual del hardware de borde (% del hardware)", 0.10, "%/año", "Supuesto", "Repuestos, reemplazo de cámara, soporte", PCT),
    "Producción en la nube",
    ("replicas_bd", "Réplicas de base de datos (1 = sin HA, 2 = HA de zona)", 2, "réplicas", "Supuesto", "Producción: HA. Piloto: poner 1", INT),
    ("horas_activas_vision_cloud", "Horas activas al mes del servicio de visión en nube (no tiempo real)", 60, "h/mes", "Supuesto", "Inspección de imágenes desde el chat y staging de SKU; escala a cero", INT),
    ("horas_entrenamiento", "Horas de cómputo de reentrenamiento al mes", 10, "h/mes", "Supuesto", "Modelo clásico en CPU: minutos por corrida; holgura para varias corridas", INT),
]

row = 5
for item in SUPUESTOS:
    if isinstance(item, str):
        sup.cell(row=row, column=1, value=item).font = BOLD
        for c in range(1, 8):
            sup.cell(row=row, column=c).fill = FILL_SEC
        row += 1
        continue
    nm, desc, val, unit, tipo, src, fmt = item
    sup.cell(row=row, column=1, value=nm)
    sup.cell(row=row, column=2, value=desc).alignment = WRAP
    c = sup.cell(row=row, column=3, value=val)
    c.number_format = fmt
    c.border = BOX
    if tipo != "Calculado":
        c.font = BLUE
        c.fill = FILL_IN
    sup.cell(row=row, column=4, value=unit)
    sup.cell(row=row, column=5, value=tipo)
    sup.cell(row=row, column=6, value=src).alignment = WRAP
    sup.cell(row=row, column=7, value=FECHA)
    name(sup, f"C{row}", nm)
    if nm == "pct_automatizado":
        c.comment = Comment("Coordinador: reemplazar por la cobertura ACT medida en test (reporte del modelo).", "FinOps")
    row += 1
widths(sup, [24, 58, 16, 11, 12, 70, 12])
sup.freeze_panes = "A5"

# ============================================================ OPERACIÓN ACTUAL
op = wb.create_sheet("Operación actual")
title(op, "Costo de la operación manual actual", "Un operador por turno por estación, 24×5. Costos en USD por mes.", 4)
header(op, 4, ["Concepto", "Colombia", "España", "Cómo se calcula"])
OP_ROWS = [
    ("Horas de puesto por mes por estación", "=horas_mes*operadores_turno", "=horas_mes*operadores_turno", "horas_mes × operadores por turno", NUM),
    ("Horas pagadas por mes por estación (con cobertura)", "=B5*factor_cobertura", "=C5*factor_cobertura", "× factor de cobertura", NUM),
    ("Costo cargado por hora", "=costo_hora_co", "=costo_hora_es", "Ver Supuestos", USD),
    ("Costo mensual por estación", "=B6*B7", "=C6*C7", "Horas pagadas × costo hora", USD0),
    ("Estaciones", "=estaciones", "=estaciones", "", INT),
    ("Costo mensual de la operación actual", "=B8*B9*plantas", "=C8*C9*plantas", "Por estación × estaciones × plantas", USD0),
    ("Costo anual de la operación actual", "=B10*12", "=C10*12", "", USD0),
    ("Personas en nómina equivalentes (FTE)", "=B6*B9*plantas/horas_lab_mes_co", "=C6*C9*plantas/(horas_anuales_es/12)", "Horas pagadas / horas contratadas por persona", NUM),
]
for i, (lbl, fco, fes, how, fmt) in enumerate(OP_ROWS, start=5):
    op.cell(row=i, column=1, value=lbl)
    for col, f in ((2, fco), (3, fes)):
        c = op.cell(row=i, column=col, value=f)
        c.number_format = fmt
        c.border = BOX
    op.cell(row=i, column=4, value=how)
for col in (1, 2, 3):
    op.cell(row=10, column=col).fill = FILL_TOT
    op.cell(row=10, column=col).font = BOLD
name(op, "B10", "actual_mes_co")
name(op, "C10", "actual_mes_es")
name(op, "B8", "actual_estacion_co")
name(op, "C8", "actual_estacion_es")
widths(op, [52, 16, 16, 50])

# ============================================================ HOJAS DE NUBE
CLOUD_HDR = [
    "Servicio", "SKU / medidor", "Precio unitario (USD)", "Unidad de precio", "Cantidad mensual",
    "Incluir (1/0)", "Costo mensual (USD)", "Tipo", "Estado del precio", "Fuente", "Fecha consulta", "Cómo se calcula la cantidad",
]


def cloud_sheet(title_txt: str, sub: str, rows: list, prefix: str):
    ws = wb.create_sheet(prefix)
    title(ws, title_txt, sub, len(CLOUD_HDR))
    header(ws, 4, CLOUD_HDR)
    r = 5
    for item in rows:
        if isinstance(item, str):
            ws.cell(row=r, column=1, value=item).font = BOLD
            for c in range(1, len(CLOUD_HDR) + 1):
                ws.cell(row=r, column=c).fill = FILL_SEC
            r += 1
            continue
        svc, sku, price, unit, qty, incl, tipo, estado, src, how = item
        vals = [svc, sku, price, unit, qty, incl, f"=C{r}*E{r}*F{r}", tipo, estado, src, FECHA, how]
        for ci, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=ci, value=v)
            c.border = BOX
            if ci in (2, 10, 12):
                c.alignment = WRAP
        ws.cell(row=r, column=3).number_format = "0.000000"
        ws.cell(row=r, column=3).font = BLUE
        ws.cell(row=r, column=3).fill = FILL_IN
        ws.cell(row=r, column=6).font = BLUE
        ws.cell(row=r, column=6).fill = FILL_IN
        ws.cell(row=r, column=5).number_format = NUM
        ws.cell(row=r, column=7).number_format = USD
        r += 1
    last = r - 1
    r += 1
    tot = {}
    for lbl, crit in (("Plataforma fija", "Plataforma fija"), ("Por estación", "Por estación"), ("LLM", "LLM")):
        ws.cell(row=r, column=6, value=f"Subtotal {lbl}").font = BOLD
        c = ws.cell(row=r, column=7, value=f'=SUMIF($H$5:$H${last},"{crit}",$G$5:$G${last})')
        c.number_format = USD
        c.font = BOLD
        tot[lbl] = r
        r += 1
    ws.cell(row=r, column=6, value="TOTAL MENSUAL").font = BOLD
    c = ws.cell(row=r, column=7, value=f"=SUM(G{tot['Plataforma fija']}:G{tot['LLM']})")
    c.number_format = USD
    c.font = BOLD
    for col in (6, 7):
        ws.cell(row=r, column=col).fill = FILL_TOT
    p = prefix.lower()
    name(ws, f"G{r}", f"{p}_total_mes")
    name(ws, f"G{tot['Plataforma fija']}", f"{p}_fijo_mes")
    name(ws, f"G{tot['Por estación']}", f"{p}_estacion_total_mes")
    name(ws, f"G{tot['LLM']}", f"{p}_llm_mes")
    r += 2
    ws.cell(row=r, column=6, value="Costo por estación (marginal)")
    c = ws.cell(row=r, column=7, value=f"={p}_estacion_total_mes/estaciones")
    c.number_format = USD
    name(ws, f"G{r}", f"{p}_por_estacion_mes")
    r += 1
    ws.cell(row=r, column=6, value="Costo LLM por conversación")
    c = ws.cell(row=r, column=7, value=f"={p}_llm_mes/conv_mes")
    c.number_format = "0.0000"
    name(ws, f"G{r}", f"{p}_llm_por_conv")
    r += 1
    ws.cell(row=r, column=6, value="Costo por objeto procesado")
    c = ws.cell(row=r, column=7, value=f"={p}_total_mes/(throughput_obj_h*horas_mes*estaciones)")
    c.number_format = "0.00000"
    widths(ws, [30, 34, 13, 14, 14, 9, 15, 15, 15, 46, 12, 52])
    ws.freeze_panes = "C5"
    ws.conditional_formatting.add(
        f"I5:I{last}", CellIsRule(operator="equal", formula=['"Estimado"'], fill=PatternFill("solid", fgColor="FCE4D6"))
    )
    return ws


# Cantidades reutilizables
IMG_HOT = "=gb_imagenes_mes_est*estaciones*dias_hot/30"
IMG_COOL = "=gb_imagenes_mes_est*estaciones*dias_cool/30"
IMG_ARCH = "=gb_imagenes_mes_est*estaciones*dias_archivo/30"
SMALL_IN_FRESH = "=conv_mes*(1-pct_modelo_grande)*tok_in_conv*(1-pct_cache)/1000"
SMALL_IN_CACHE = "=conv_mes*(1-pct_modelo_grande)*tok_in_conv*pct_cache/1000"
SMALL_OUT = "=conv_mes*(1-pct_modelo_grande)*tok_out_conv/1000"
BIG_IN_FRESH = "=conv_mes*pct_modelo_grande*tok_in_conv*(1-pct_cache)/1000"
BIG_IN_CACHE = "=conv_mes*pct_modelo_grande*tok_in_conv*pct_cache/1000"
BIG_OUT = "=conv_mes*pct_modelo_grande*tok_out_conv/1000"

V = "API verificada"
E = "Estimado"
AOAI = "https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/"

AZURE = [
    "Plano de nube — cómputo",
    ("Azure Container Apps (agente)", "Consumo · vCPU activa (0,5 vCPU)", 0.000024, "vCPU-segundo", "=0.5*3600*horas_mes", 1, "Plataforma fija", V, AZ_API,
     "0,5 vCPU × horas de operación (min. 1 réplica en turno; escala a 0 fines de semana). No descuenta la cuota gratuita mensual (conservador)"),
    ("Azure Container Apps (agente)", "Consumo · memoria activa (1 GiB)", 0.000003, "GiB-segundo", "=1*3600*horas_mes", 1, "Plataforma fija", V, AZ_API, "1 GiB × horas de operación"),
    ("Azure Container Apps (visión no tiempo real)", "Consumo · vCPU + memoria (1 vCPU, 2 GiB)", 0.000030, "segundo", "=3600*horas_activas_vision_cloud", 1, "Plataforma fija", V, AZ_API,
     "1×0,000024 + 2×0,000003 por segundo activo; escala a cero"),
    ("Azure Container Registry", "Basic", 0.1666, "día", "=30.4", 1, "Plataforma fija", V, AZ_API, "Imágenes de contenedor para nube y borde"),
    ("Azure Machine Learning", "Compute cluster CPU (D4s_v3 aprox.)", 0.20, "hora", "=horas_entrenamiento", 1, "Plataforma fija", E,
     "https://azure.microsoft.com/pricing/details/machine-learning/", "Workspace sin cargo; solo se paga cómputo. Cluster escala a 0"),
    "Plano de nube — datos",
    ("Azure Database for PostgreSQL Flexible", "General Purpose D2ds_v5 (2 vCore)", 0.178, "hora", "=730*replicas_bd", 1, "Plataforma fija", V, AZ_API,
     "0,089 USD/vCore-h × 2 vCore. Réplica HA de zona duplica cómputo"),
    ("Azure Database for PostgreSQL Flexible", "Almacenamiento SSD", 0.115, "GB-mes", "=64*replicas_bd", 1, "Plataforma fija", V, AZ_API, "64 GB por réplica"),
    ("Azure AI Search", "Basic (1 unidad, 15 GB, semántico incluido en cuota gratuita)", 0.101, "hora", "=730", 1, "Plataforma fija", V, AZ_API,
     "Índice de SOP/manuales con búsqueda híbrida. Alternativa más barata: pgvector en la misma PostgreSQL"),
    ("Azure OpenAI", "text-embedding-3-small", 0.00002, "1K tokens", "=tok_embed_mes/1000", 1, "Plataforma fija", E, AOAI, "Reindexado de SOP"),
    ("Azure Blob Storage", "Hot LRS (imágenes recientes)", 0.0184, "GB-mes", IMG_HOT, 1, "Por estación", V, AZ_API, "Stock en Hot = GB/mes × días en Hot / 30"),
    ("Azure Blob Storage", "Cool LRS", 0.01, "GB-mes", IMG_COOL, 1, "Por estación", V, AZ_API, "Política de ciclo de vida: Hot→Cool día 30"),
    ("Azure Blob Storage", "Archive LRS", 0.002, "GB-mes", IMG_ARCH, 1, "Por estación", V, AZ_API, "Cool→Archive día 180; borrar a 3 años"),
    ("Azure Blob Storage", "Transacciones y recuperaciones (aprox.)", 5, "mes", "=estaciones", 1, "Por estación", E, "https://azure.microsoft.com/pricing/details/storage/blobs/", "Escrituras + lecturas desde la cola de revisión"),
    "Borde y dispositivos",
    ("Azure IoT Hub", "S1 (400.000 msgs/día por unidad)", 25, "unidad-mes", "=MAX(1,ROUNDUP(msgs_dia_estacion*estaciones/400000,0))", 1, "Plataforma fija", V, AZ_API,
     "Unidades = msgs/día / 400k. Incluye device twins y despliegue de módulos IoT Edge"),
    ("Azure IoT Edge runtime", "Runtime open source en el PC industrial", 0, "dispositivo-mes", "=estaciones", 1, "Por estación", V,
     "https://azure.microsoft.com/products/iot-edge/", "Sin cargo de licencia; se paga IoT Hub"),
    ("AKS Edge Essentials (alternativa)", "Licencia por dispositivo", 2.5, "dispositivo-mes", "=estaciones", 0, "Por estación", V, AZ_API,
     "Solo si se elige Kubernetes en el borde (Incluir=1)"),
    ("Defender for Servers P2 (vía Azure Arc)", "Por PC industrial", 0.02, "nodo-hora", "=730*estaciones", 1, "Por estación", V, AZ_API, "EDR y gestión de vulnerabilidades del PC de borde"),
    "Seguridad, red y observabilidad",
    ("Azure Key Vault", "Standard · operaciones", 0.03, "10K ops", "=20", 1, "Plataforma fija", V, AZ_API, "~200K operaciones/mes (con caché de secretos)"),
    ("Private Endpoints", "Por endpoint (Storage, PostgreSQL, Key Vault, OpenAI, Search)", 0.01, "hora", "=5*730", 1, "Plataforma fija", E,
     "https://azure.microsoft.com/pricing/details/private-link/", "5 endpoints × 730 h (+ 0,01 USD/GB procesado, despreciable)"),
    ("Log Analytics + Application Insights", "Analytics Logs · ingesta", 2.76, "GB", "=MAX(0,gb_logs_mes-5)", 1, "Plataforma fija", V, AZ_API, "Primeros 5 GB/mes gratis por cuenta de facturación"),
    ("Defender for Cloud", "Containers · escaneo de imágenes", 0.29, "imagen", "=10", 1, "Plataforma fija", V, AZ_API, "~10 imágenes nuevas/mes"),
    ("Defender for Cloud", "Storage (por cuenta, aprox.)", 10, "cuenta-mes", "=1", 1, "Plataforma fija", E, "https://azure.microsoft.com/pricing/details/defender-for-cloud/", "Detección de malware/anomalías en Blob"),
    ("Azure Front Door Standard + WAF (opcional)", "Tarifa base", 35, "mes", "=1", 0, "Plataforma fija", E, "https://azure.microsoft.com/pricing/details/frontdoor/",
     "Solo si el chat se expone fuera de la red corporativa"),
    "Asistente agéntico (LLM)",
    ("Azure OpenAI", "gpt-4.1-mini · Data Zone · entrada", 0.00044, "1K tokens", SMALL_IN_FRESH, 1, "LLM", V, AZ_API, "Modelo por defecto (85%). Data Zone = datos dentro de EE.UU./UE"),
    ("Azure OpenAI", "gpt-4.1-mini · Data Zone · entrada en caché", 0.00011, "1K tokens", SMALL_IN_CACHE, 1, "LLM", V, AZ_API, "Prompt caching automático (prefijos ≥1.024 tokens)"),
    ("Azure OpenAI", "gpt-4.1-mini · Data Zone · salida", 0.00176, "1K tokens", SMALL_OUT, 1, "LLM", V, AZ_API, ""),
    ("Azure OpenAI", "gpt-4.1 · Data Zone · entrada", 0.0022, "1K tokens", BIG_IN_FRESH, 1, "LLM", E, AOAI, "Modelo grande para diagnósticos (15%)"),
    ("Azure OpenAI", "gpt-4.1 · Data Zone · entrada en caché", 0.00055, "1K tokens", BIG_IN_CACHE, 1, "LLM", E, AOAI, ""),
    ("Azure OpenAI", "gpt-4.1 · Data Zone · salida", 0.0088, "1K tokens", BIG_OUT, 1, "LLM", E, AOAI, ""),
]

AWS_P = "https://aws.amazon.com/{}/pricing/"
AWS = [
    "Plano de nube — cómputo",
    ("Amazon ECS on Fargate (agente)", "vCPU (0,5 vCPU, Linux x86)", 0.04048, "vCPU-hora", "=0.5*horas_mes", 1, "Plataforma fija", E, AWS_P.format("fargate"),
     "Igual perfil que en Azure. Fargate tras ALB no escala a cero de forma nativa"),
    ("Amazon ECS on Fargate (agente)", "Memoria (1 GB)", 0.004445, "GB-hora", "=1*horas_mes", 1, "Plataforma fija", E, AWS_P.format("fargate"), ""),
    ("Amazon ECS on Fargate (visión no tiempo real)", "1 vCPU + 2 GB", 0.04937, "hora", "=horas_activas_vision_cloud", 1, "Plataforma fija", E, AWS_P.format("fargate"),
     "0,04048 + 2×0,004445; tarea bajo demanda"),
    ("Application Load Balancer", "Hora + ~1 LCU", 0.0305, "hora", "=730", 1, "Plataforma fija", E, AWS_P.format("elasticloadbalancing"), "0,0225 h + 0,008 LCU-h"),
    ("NAT Gateway", "Hora (1 AZ)", 0.045, "hora", "=730", 1, "Plataforma fija", E, "https://aws.amazon.com/vpc/pricing/", "Salida de subredes privadas; datos aparte"),
    ("Amazon ECR", "Almacenamiento", 0.10, "GB-mes", "=3", 1, "Plataforma fija", E, AWS_P.format("ecr"), ""),
    ("Amazon SageMaker AI", "Training ml.m5.large", 0.115, "hora", "=horas_entrenamiento", 1, "Plataforma fija", E, AWS_P.format("sagemaker-ai"),
     "Model Registry y Pipelines sin cargo propio"),
    "Plano de nube — datos",
    ("Amazon Aurora PostgreSQL Serverless v2", "ACU-hora (promedio 1 ACU)", 0.12, "ACU-hora", "=1*730*replicas_bd", 1, "Plataforma fija", E, AWS_P.format("rds/aurora"),
     "Estado, auditoría y vector store (pgvector) de Bedrock Knowledge Bases. Réplica lectora en otra AZ = HA"),
    ("Amazon Aurora PostgreSQL", "Almacenamiento", 0.10, "GB-mes", "=64", 1, "Plataforma fija", E, AWS_P.format("rds/aurora"), "Almacenamiento compartido entre réplicas"),
    ("Amazon Bedrock Knowledge Bases", "Embeddings Titan Text v2", 0.00002, "1K tokens", "=tok_embed_mes/1000", 1, "Plataforma fija", E, AWS_P.format("bedrock"),
     "KB sobre Aurora pgvector (evita mínimo de OpenSearch Serverless)"),
    ("Amazon OpenSearch Serverless (alternativa)", "OCU-hora (mín. 1 OCU sin redundancia)", 0.24, "OCU-hora", "=2*730", 0, "Plataforma fija", E, AWS_P.format("opensearch-service"),
     "No incluido: mínimo ~350 USD/mes desproporcionado para pocos SOP"),
    ("Amazon S3", "Standard", 0.023, "GB-mes", IMG_HOT, 1, "Por estación", E, AWS_P.format("s3"), "Lifecycle: Standard→IA día 30"),
    ("Amazon S3", "Standard-IA", 0.0125, "GB-mes", IMG_COOL, 1, "Por estación", E, AWS_P.format("s3"), "IA→Deep Archive día 180"),
    ("Amazon S3", "Glacier Deep Archive", 0.00099, "GB-mes", IMG_ARCH, 1, "Por estación", E, AWS_P.format("s3"), "Expira a 3 años"),
    ("Amazon S3", "Solicitudes y recuperaciones (aprox.)", 5, "mes", "=estaciones", 1, "Por estación", E, AWS_P.format("s3"), ""),
    "Borde y dispositivos",
    ("AWS IoT Core", "Mensajería", 0.000001, "mensaje", "=msgs_dia_estacion*estaciones*dias_mes", 1, "Por estación", E, AWS_P.format("iot-core"), "1 USD por millón de mensajes"),
    ("AWS IoT Core", "Conectividad", 0.00000008, "minuto-conexión", "=43800*estaciones", 1, "Por estación", E, AWS_P.format("iot-core"), "0,08 USD por millón de minutos"),
    ("AWS IoT Greengrass V2", "Dispositivo core activo", 0.16, "dispositivo-mes", "=estaciones", 1, "Por estación", E, AWS_P.format("greengrass"), ""),
    ("AWS Systems Manager (instancia híbrida avanzada)", "Por PC industrial", 0.00695, "hora", "=730*estaciones", 1, "Por estación", E, AWS_P.format("systems-manager"),
     "Parches/inventario/sesiones del PC de borde"),
    "Seguridad, red y observabilidad",
    ("AWS Secrets Manager", "Secreto", 0.40, "secreto-mes", "=8", 1, "Plataforma fija", E, AWS_P.format("secrets-manager"), "8 secretos (BD, tokens, certificados)"),
    ("VPC Interface Endpoints (PrivateLink)", "Endpoint-AZ", 0.01, "hora", "=5*2*730", 1, "Plataforma fija", E, "https://aws.amazon.com/privatelink/pricing/",
     "5 endpoints (Bedrock, S3 gateway gratis aparte, Secrets, ECR, Logs) × 2 AZ"),
    ("Amazon CloudWatch Logs", "Ingesta", 0.50, "GB", "=gb_logs_mes", 1, "Plataforma fija", E, AWS_P.format("cloudwatch"), ""),
    ("Amazon GuardDuty", "Cuenta pequeña (CloudTrail, S3, runtime ECS)", 15, "mes", "=1", 1, "Plataforma fija", E, AWS_P.format("guardduty"), "Estimación según volumen de eventos"),
    ("AWS WAF (opcional)", "Web ACL + reglas", 10, "mes", "=1", 0, "Plataforma fija", E, AWS_P.format("waf"), "Solo si el chat se expone fuera de la red corporativa"),
    "Asistente agéntico (LLM)",
    ("Amazon Bedrock", "Claude Haiku 4.5 · entrada", 0.001, "1K tokens", SMALL_IN_FRESH, 1, "LLM", E, AWS_P.format("bedrock"), "Modelo por defecto (85%)"),
    ("Amazon Bedrock", "Claude Haiku 4.5 · lectura de caché", 0.0001, "1K tokens", SMALL_IN_CACHE, 1, "LLM", E, AWS_P.format("bedrock"),
     "Prompt caching explícito (escritura en caché ~1,25× no modelada)"),
    ("Amazon Bedrock", "Claude Haiku 4.5 · salida", 0.005, "1K tokens", SMALL_OUT, 1, "LLM", E, AWS_P.format("bedrock"), ""),
    ("Amazon Bedrock", "Claude Sonnet 4.5 · entrada", 0.003, "1K tokens", BIG_IN_FRESH, 1, "LLM", E, AWS_P.format("bedrock"), "Modelo grande (15%)"),
    ("Amazon Bedrock", "Claude Sonnet 4.5 · lectura de caché", 0.0003, "1K tokens", BIG_IN_CACHE, 1, "LLM", E, AWS_P.format("bedrock"), ""),
    ("Amazon Bedrock", "Claude Sonnet 4.5 · salida", 0.015, "1K tokens", BIG_OUT, 1, "LLM", E, AWS_P.format("bedrock"), ""),
]

cloud_sheet(
    "Azure — costo mensual por servicio",
    "Región de referencia: East US 2 (precios de la API pública de Azure, " + FECHA + "). Para plantas en España usar Spain Central / West Europe: "
    "precios ~10-40% mayores según servicio (p. ej. Container Apps vCPU 0,000034 vs 0,000024 USD/s). Precio × cantidad × incluir = costo. "
    "Filas 'Estimado' (naranja) deben verificarse.",
    AZURE,
    "Azure",
)
cloud_sheet(
    "AWS — costo mensual por servicio",
    "Región de referencia: us-east-1. Precios de lista públicos tomados de las páginas de precios de AWS conocidas a " + FECHA + "; "
    "NO verificados contra la API de precios en esta entrega → todos marcados 'Estimado'. Para España usar eu-south-2 (Spain).",
    AWS,
    "AWS",
)

# ============================================================ COMPARATIVO
cmp_ = wb.create_sheet("Comparativo")
title(
    cmp_,
    "Comparativo: operación actual vs Azure vs AWS",
    "USD. El costo 'nuevo' = nube + mantenimiento de borde + trabajo humano residual (cola de revisión + supervisión itinerante). "
    "El robot ya existe en ambos escenarios y no se incluye.",
    5,
)
header(cmp_, 4, ["Concepto", "Colombia · Azure", "Colombia · AWS", "España · Azure", "España · AWS"])
COLS = {"B": ("co", "azure"), "C": ("co", "aws"), "D": ("es", "azure"), "E": ("es", "aws")}
CMP = [
    ("Costo mensual operación actual", lambda s, c: f"=actual_mes_{s}", USD0),
    ("Nube: plataforma fija", lambda s, c: f"={c}_fijo_mes*plantas", USD0),
    ("Nube: por estaciones", lambda s, c: f"={c}_estacion_total_mes*plantas", USD0),
    ("Nube: LLM del asistente", lambda s, c: f"={c}_llm_mes*plantas", USD0),
    ("Mantenimiento hardware de borde", lambda s, c: "=estaciones*plantas*(capex_pc+capex_camara)*pct_mant_hw/12", USD0),
    ("Humano residual: cola de revisión", lambda s, c: f"=estaciones*plantas*throughput_obj_h*horas_mes*(1-pct_automatizado)*min_caso_revision/60*costo_hora_{s}", USD0),
    ("Humano residual: supervisión itinerante", lambda s, c: f"=estaciones*plantas*fte_residual*horas_mes*factor_cobertura*costo_hora_{s}", USD0),
    ("COSTO MENSUAL NUEVO", lambda s, c: "=SUM({col}6:{col}11)", USD0),
    ("AHORRO MENSUAL", lambda s, c: "={col}5-{col}12", USD0),
    ("Ahorro mensual (%)", lambda s, c: "=IF({col}5>0,{col}13/{col}5,0)", PCT),
    ("Costo anual actual", lambda s, c: "={col}5*12", USD0),
    ("Costo anual nuevo (OPEX)", lambda s, c: "={col}12*12", USD0),
    ("CAPEX único (borde + integración + implementación)", lambda s, c: "=estaciones*plantas*(capex_pc+capex_camara+capex_integracion)+capex_planta*plantas", USD0),
    ("Payback (meses)", lambda s, c: '=IF({col}13>0,{col}17/{col}13,"No recupera")', NUM),
    ("Ahorro neto a 3 años (36 × ahorro − CAPEX)", lambda s, c: "=36*{col}13-{col}17", USD0),
    ("Costo nube por objeto procesado (USD)", lambda s, c: "=SUM({col}6:{col}8)/(throughput_obj_h*horas_mes*estaciones*plantas)", "0.00000"),
    ("Humano residual / costo actual (%)", lambda s, c: "=({col}10+{col}11)/{col}5", PCT),
]
for i, (lbl, fn, fmt) in enumerate(CMP, start=5):
    cmp_.cell(row=i, column=1, value=lbl)
    for col, (s, c) in COLS.items():
        f = fn(s, c).replace("{col}", col)
        cell = cmp_[f"{col}{i}"]
        cell.value = f
        cell.number_format = fmt
        cell.border = BOX
    if lbl.isupper():
        for col in "ABCDE":
            cmp_[f"{col}{i}"].font = BOLD
            cmp_[f"{col}{i}"].fill = FILL_TOT
widths(cmp_, [50, 18, 18, 18, 18])
r = 5 + len(CMP) + 1
notes = [
    "Lectura rápida:",
    "• El ahorro viene de retirar 3 turnos de operador por estación; la nube es una fracción pequeña del costo laboral evitado.",
    "• El factor que más mueve el resultado es % automatizado (cola de revisión) y el costo laboral local, no el proveedor de nube.",
    "• Con 1 estación en Colombia el payback es largo porque el CAPEX de implementación por planta pesa mucho; ver Sensibilidad (5 y 20 estaciones).",
    "• Ningún número de esta hoja está hardcodeado: todo sale de Supuestos, Operación actual, Azure y AWS.",
]
for n in notes:
    cmp_.cell(row=r, column=1, value=n).alignment = Alignment(wrap_text=False)
    r += 1

# ============================================================ SENSIBILIDAD
sen = wb.create_sheet("Sensibilidad")
title(sen, "Sensibilidad", "Celdas azules de la primera columna son editables. Todo lo demás se recalcula desde Supuestos y las hojas de nube (Azure como referencia salvo que se indique).", 7)

# Tabla 1: % automatizado
sen["A4"] = "1) % automatizado (con las estaciones de Supuestos) — Azure"
sen["A4"].font = BOLD
header(sen, 5, ["% automatizado", "Residual cola CO (USD/mes)", "Ahorro CO (USD/mes)", "Payback CO (meses)", "Residual cola ES (USD/mes)", "Ahorro ES (USD/mes)", "Payback ES (meses)"])
NUBE_AZ = "(azure_fijo_mes+azure_estacion_total_mes+azure_llm_mes)*plantas"
MANT = "estaciones*plantas*(capex_pc+capex_camara)*pct_mant_hw/12"
SUPV = "estaciones*plantas*fte_residual*horas_mes*factor_cobertura*costo_hora_{s}"
CAPEX = "(estaciones*plantas*(capex_pc+capex_camara+capex_integracion)+capex_planta*plantas)"
for i, p in enumerate([0.80, 0.90, 0.95, 0.99], start=6):
    c = sen.cell(row=i, column=1, value=p)
    c.number_format = PCT
    c.font = BLUE
    c.fill = FILL_IN
    for base, s in ((2, "co"), (5, "es")):
        L = get_column_letter(base)
        A = get_column_letter(base + 1)
        sen.cell(row=i, column=base, value=f"=estaciones*plantas*throughput_obj_h*horas_mes*(1-$A{i})*min_caso_revision/60*costo_hora_{s}").number_format = USD0
        sen.cell(row=i, column=base + 1, value=f"=actual_mes_{s}-({NUBE_AZ}+{MANT}+{L}{i}+{SUPV.format(s=s)})").number_format = USD0
        sen.cell(row=i, column=base + 2, value=f'=IF({A}{i}>0,{CAPEX}/{A}{i},"No recupera")').number_format = NUM

# Tabla 2: estaciones
sen["A12"] = "2) Número de estaciones por planta (con % automatizado de Supuestos) — Azure"
sen["A12"].font = BOLD
header(sen, 13, ["Estaciones", "Costo actual CO", "Costo nuevo CO", "Payback CO (meses)", "Costo actual ES", "Costo nuevo ES", "Payback ES (meses)"])
for i, n in enumerate([1, 5, 20], start=14):
    c = sen.cell(row=i, column=1, value=n)
    c.font = BLUE
    c.fill = FILL_IN
    for base, s in ((2, "co"), (5, "es")):
        A = get_column_letter(base)
        N = get_column_letter(base + 1)
        sen.cell(row=i, column=base, value=f"=actual_estacion_{s}*$A{i}*plantas").number_format = USD0
        nube = f"(azure_fijo_mes+azure_por_estacion_mes*$A{i}+azure_llm_por_conv*(conv_dia_base+conv_dia_estacion*$A{i})*dias_mes)*plantas"
        resid = (
            f"$A{i}*plantas*(throughput_obj_h*horas_mes*(1-pct_automatizado)*min_caso_revision/60*costo_hora_{s}"
            f"+fte_residual*horas_mes*factor_cobertura*costo_hora_{s}+(capex_pc+capex_camara)*pct_mant_hw/12)"
        )
        sen.cell(row=i, column=base + 1, value=f"={nube}+{resid}").number_format = USD0
        capex = f"($A{i}*(capex_pc+capex_camara+capex_integracion)+capex_planta)*plantas"
        sen.cell(row=i, column=base + 2, value=f'=IF({A}{i}>{N}{i},{capex}/({A}{i}-{N}{i}),"No recupera")').number_format = NUM

# Tabla 3: conversaciones LLM
sen["A19"] = "3) Volumen del asistente: costo LLM mensual por planta"
sen["A19"].font = BOLD
header(sen, 20, ["Conversaciones/día", "Azure LLM (USD/mes)", "AWS LLM (USD/mes)", "Azure LLM si todo va al modelo grande", "Peso LLM Azure en nube total", "", ""])
for i, n in enumerate([20, 60, 200, 1000], start=21):
    c = sen.cell(row=i, column=1, value=n)
    c.font = BLUE
    c.fill = FILL_IN
    sen.cell(row=i, column=2, value=f"=azure_llm_por_conv*$A{i}*dias_mes").number_format = USD0
    sen.cell(row=i, column=3, value=f"=aws_llm_por_conv*$A{i}*dias_mes").number_format = USD0
    big_conv = "(tok_in_conv*((1-pct_cache)*0.0022+pct_cache*0.00055)+tok_out_conv*0.0088)/1000"
    sen.cell(row=i, column=4, value=f"={big_conv}*$A{i}*dias_mes").number_format = USD0
    sen.cell(row=i, column=5, value=f"=B{i}/(azure_fijo_mes+azure_estacion_total_mes+B{i})").number_format = PCT
sen["A26"] = "Nota tabla 3: la columna 'todo al modelo grande' usa los precios gpt-4.1 Data Zone de la hoja Azure (estimados) y muestra el valor del enrutamiento a modelo pequeño."
widths(sen, [20, 22, 20, 22, 22, 20, 20])

# ============================================================ FUENTES
src = wb.create_sheet("Fuentes")
title(src, "Fuentes y método", "", 2)
FUENTES = [
    ("API de precios minoristas de Azure", "https://prices.azure.com/api/retail/prices — consultada " + FECHA + " con filtros por serviceName/meterName, región eastus2, priceType=Consumption"),
    ("Calculadora Azure", "https://azure.microsoft.com/pricing/calculator/"),
    ("Precios Azure OpenAI", AOAI),
    ("Calculadora AWS", "https://calculator.aws/"),
    ("Precios Bedrock", "https://aws.amazon.com/bedrock/pricing/"),
    ("Precios Fargate", "https://aws.amazon.com/fargate/pricing/"),
    ("Precios S3", "https://aws.amazon.com/s3/pricing/"),
    ("SMMLV Colombia 2025", "Decreto 1572 de 2024 (1.423.500 COP)"),
    ("Recargo nocturno Colombia", "Código Sustantivo del Trabajo, art. 168"),
    ("Método", "Precio unitario × cantidad mensual × incluir. Cantidades derivadas de Supuestos. Precios de lista sin descuentos EA/MACC/EDP, sin reservas ni impuestos."),
    ("Limitaciones", "Precios AWS y algunos de Azure son estimados (columna 'Estado'). No incluye egress de red, soporte (Developer/Business) ni licencias corporativas existentes."),
]
for i, (k, v) in enumerate(FUENTES, start=4):
    src.cell(row=i, column=1, value=k).font = BOLD
    src.cell(row=i, column=2, value=v).alignment = WRAP
widths(src, [34, 120])

# Orden de hojas pedido
wb._sheets = [wb[n] for n in ["Supuestos", "Operación actual", "Azure", "AWS", "Comparativo", "Sensibilidad", "Fuentes"]]
OUT.parent.mkdir(parents=True, exist_ok=True)
wb.save(OUT)
print(f"OK → {OUT}")
