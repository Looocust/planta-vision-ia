"""Genera docs/cloud/finops_model.xlsx con fórmulas vivas.

    uv run python scripts/build_finops.py                    # usa el snapshot de precios guardado
    uv run python scripts/build_finops.py --refresh-prices   # vuelve a consultar precios públicos

Todas las entradas (celdas amarillas) viven en la hoja "Supuestos" con nombre definido
(`estaciones`, `pct_automatizado`, …). Las demás hojas solo contienen fórmulas, así que
cambiar un supuesto recalcula el libro al abrirlo en Excel / LibreOffice / Sheets.

Precios y trazabilidad
----------------------
Con `--refresh-prices` el script consulta, para dos regiones (EE. UU. y UE/España):
  * Azure Retail Prices API (pública, sin autenticación): https://prices.azure.com/api/retail/prices
    con el filtro OData exacto de cada medidor (regiones eastus2 y spaincentral).
  * AWS Price List Bulk API (pública): https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<oferta>/current/<región>/index.json
    buscando el `usagetype` exacto (regiones us-east-1 y eu-south-2).
El resultado (precio, medidor/usagetype, descripción, consulta y fecha) se guarda en
docs/cloud/precios_snapshot.json y se vuelca en la hoja "Fuentes". Las filas sin precio
encontrado usan un valor de referencia marcado "Estimado".
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "cloud" / "finops_model.xlsx"
SNAPSHOT = ROOT / "docs" / "cloud" / "precios_snapshot.json"

AZ_API = "https://prices.azure.com/api/retail/prices"
AWS_API = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{offer}/current/{region}/index.json"
REGIONS = {
    "us": {"az": ["eastus2", "eastus"], "aws": "us-east-1", "label": "EE. UU. (East US 2 / us-east-1)"},
    "eu": {"az": ["spaincentral", "westeurope"], "aws": "eu-south-2", "label": "UE-España (Spain Central / eu-south-2)"},
}

# ------------------------------------------------------------------ catálogo de precios
# Azure: filtro OData (sin región). AWS: oferta + usagetype exacto (sin prefijo regional) + filtros de descripción.
PRICE_SPECS: dict[str, dict] = {
    # Azure
    "az_aca_vcpu": {"az": "serviceName eq 'Azure Container Apps' and meterName eq 'Standard vCPU Active Usage'"},
    "az_aca_mem": {"az": "serviceName eq 'Azure Container Apps' and meterName eq 'Standard Memory Active Usage'"},
    "az_aca_pe": {"az": "serviceName eq 'Azure Container Apps' and meterName eq 'Environment Private Endpoint'"},
    "az_acr_basic": {"az": "serviceName eq 'Container Registry' and meterName eq 'Basic Registry Unit'"},
    "az_vm_d4sv3": {"az": "serviceName eq 'Virtual Machines' and armSkuName eq 'Standard_D4s_v3' and meterName eq 'D4s v3'", "exclude": "Windows"},
    "az_pg_d2ds": {"az": "productName eq 'Azure Database for PostgreSQL Flexible Server General Purpose Ddsv5 Series Compute' and skuName eq '2 vCore'"},
    "az_pg_storage": {"az": "productName eq 'Azure Database for PostgreSQL Flex Server Storage' and meterName eq 'Storage Data Stored'"},
    "az_search_basic": {"az": "serviceName eq 'Azure Cognitive Search' and meterName eq 'Basic Unit'"},
    "az_blob_hot_lrs": {"az": "productName eq 'General Block Blob v2' and meterName eq 'Hot LRS Data Stored'"},
    "az_blob_cool_lrs": {"az": "productName eq 'General Block Blob v2' and meterName eq 'Cool LRS Data Stored'"},
    "az_blob_archive_lrs": {"az": "productName eq 'General Block Blob v2' and meterName eq 'Archive LRS Data Stored'"},
    "az_blob_hot_zrs": {"az": "productName eq 'General Block Blob v2' and meterName eq 'Hot ZRS Data Stored'"},
    "az_iothub_s1": {"az": "serviceName eq 'IoT Hub' and meterName eq 'S1 Unit'"},
        "az_def_servers_p2": {"az": "productName eq 'Microsoft Defender for Servers' and meterName eq 'Standard P2 Node'"},
    "az_kv_ops": {"az": "serviceName eq 'Key Vault' and skuName eq 'Standard' and meterName eq 'Operations'"},
    "az_la_ingest": {"az": "serviceName eq 'Log Analytics' and meterName eq 'Analytics Logs Data Ingestion'"},
    "az_def_cont_img": {"az": "productName eq 'Microsoft Defender for Containers' and meterName eq 'Standard Images'"},
    "az_aoai_emb": {"az": "productName eq 'Azure OpenAI' and meterName eq 'text-embedding-3-small-glbl Tokens'"},
    "az_aoai_mini_in": {"az": "productName eq 'Azure OpenAI' and meterName eq 'gpt 4.1 mini Inp Data Zone Tokens'"},
    "az_aoai_mini_cache": {"az": "productName eq 'Azure OpenAI' and meterName eq 'gpt 4.1 mini cached Inp DZone Tokens'"},
    "az_aoai_mini_out": {"az": "productName eq 'Azure OpenAI' and meterName eq 'gpt 4.1 mini Outp Data Zone Tokens'"},
    "az_aoai_big_in": {"az": "productName eq 'Azure OpenAI' and meterName eq 'gpt 4.1 Inp Data Zone Tokens'"},
    "az_aoai_big_cache": {"az": "productName eq 'Azure OpenAI' and meterName eq 'gpt 4.1 cached Inp Data Zone Tokens'"},
    "az_aoai_big_out": {"az": "productName eq 'Azure OpenAI' and meterName eq 'gpt 4.1 Outp Data Zone Tokens'"},
    # AWS
    "aws_fargate_vcpu": {"aws": ("AmazonECS", "Fargate-vCPU-Hours:perCPU", [])},
    "aws_fargate_gb": {"aws": ("AmazonECS", "Fargate-GB-Hours", [])},
    "aws_alb_hr": {"aws": ("AWSELB", "LoadBalancerUsage", ["Application"])},
    "aws_alb_lcu": {"aws": ("AWSELB", "LCUUsage", ["Application"])},
    "aws_vpce_hr": {"aws": ("AmazonVPC", "VpcEndpoint-Hours", [])},
    "aws_ecr": {"aws": ("AmazonECR", "TimedStorage-ByteHrs", [])},
    "aws_sm_train": {"aws": ("AmazonSageMaker", "Train:ml.m5.large", [])},
    "aws_aurora_acu": {"aws": ("AmazonRDS", "Aurora:ServerlessV2Usage", ["PostgreSQL"])},
    "aws_aurora_storage": {"aws": ("AmazonRDS", "Aurora:StorageUsage", ["Aurora PostgreSQL"])},
    "aws_s3_std": {"aws": ("AmazonS3", "TimedStorage-ByteHrs", [])},
    "aws_s3_ia": {"aws": ("AmazonS3", "TimedStorage-SIA-ByteHrs", [])},
    "aws_s3_gda": {"aws": ("AmazonS3GlacierDeepArchive", "TimedStorage-GDA-ByteHrs", [])},
    "aws_iot_msg": {"aws": ("AWSIoT", "Messages", [])},
    "aws_iot_conn": {"aws": ("AWSIoT", "ConnectionMinutes", [])},
    "aws_greengrass": {"aws": ("AWSGreengrass", "ActiveGGC-Devices", [])},
    "aws_secrets": {"aws": ("AWSSecretsManager", "AWSSecretsManager-Secrets", [])},
    "aws_cw_logs": {"aws": ("AmazonCloudWatch", "DataProcessing-Bytes", [])},
}


def _az_lookup(flt: str, region: str, exclude: str | None = None) -> dict | None:
    full = f"armRegionName eq '{region}' and priceType eq 'Consumption' and {flt}"
    url = AZ_API + "?" + urllib.parse.urlencode({"$filter": full})
    with urllib.request.urlopen(url, timeout=60) as r:
        items = json.load(r).get("Items", [])
    # Primer tramo con precio > 0 (p. ej. Log Analytics cobra desde el GB 5), sin Spot/Low Priority.
    items = [i for i in items if i.get("retailPrice", 0) > 0
             and "Spot" not in i.get("skuName", "") and "Low Priority" not in i.get("skuName", "")
             and not (exclude and exclude in i.get("productName", ""))]
    items.sort(key=lambda i: float(i.get("tierMinimumUnits", 0)))
    if not items:
        return None
    i = items[0]
    return {"price": i["retailPrice"], "unit": i["unitOfMeasure"], "meter": i["meterName"], "sku": i["skuName"],
            "product": i["productName"], "meter_id": i.get("meterId"), "region": region,
            "query": f"{AZ_API}?$filter={full}"}


_AWS_CACHE: dict[tuple[str, str], dict] = {}


def _aws_lookup(offer: str, usagetype: str, desc_filters: list[str], region: str) -> dict | None:
    k = (offer, region)
    if k not in _AWS_CACHE:
        with urllib.request.urlopen(AWS_API.format(offer=offer, region=region), timeout=300) as r:
            _AWS_CACHE[k] = json.load(r)
    data = _AWS_CACHE[k]
    pat = re.compile(r"([A-Z0-9]+-)?" + re.escape(usagetype))
    od = data.get("terms", {}).get("OnDemand", {})
    for sku, prod in data["products"].items():
        ut = prod["attributes"].get("usagetype", "")
        if not pat.fullmatch(ut):
            continue
        for term in od.get(sku, {}).values():
            for pdim in term["priceDimensions"].values():
                desc = pdim.get("description", "")
                if any(f not in desc for f in desc_filters) or "Outposts" in desc or "Trust Store" in desc:
                    continue
                if str(pdim.get("beginRange", "0")) not in ("0", "0.0"):
                    continue
                price = float(pdim["pricePerUnit"].get("USD", 0))
                if price <= 0:
                    continue
                return {"price": price, "unit": pdim["unit"], "meter": ut, "sku": sku, "product": offer,
                        "description": desc, "region": region, "query": AWS_API.format(offer=offer, region=region)}
    return None


def refresh_prices() -> dict:
    today = dt.date.today().isoformat()
    snap: dict = {"_fecha": today, "_nota": "Generado por scripts/build_finops.py --refresh-prices", "precios": {}}
    for key, spec in PRICE_SPECS.items():
        snap["precios"][key] = {}
        for reg, cfg in REGIONS.items():
            res = None
            try:
                if "az" in spec:
                    for azr in cfg["az"]:
                        res = _az_lookup(spec["az"], azr, spec.get("exclude"))
                        if res:
                            break
                else:
                    offer, ut, filt = spec["aws"]
                    res = _aws_lookup(offer, ut, filt, cfg["aws"])
            except Exception as exc:  # red caída, cambio de API…
                print(f"  ! {key} [{reg}]: {exc}", file=sys.stderr)
            if res:
                res["fecha"] = today
            snap["precios"][key][reg] = res
            print(f"  {key:22s} {reg}: {res['price'] if res else '—'}")
    SNAPSHOT.write_text(json.dumps(snap, indent=1, ensure_ascii=False))
    return snap


if "--refresh-prices" in sys.argv:
    print("Consultando precios públicos…")
    SNAP = refresh_prices()
else:
    SNAP = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {"_fecha": None, "precios": {}}
FECHA = SNAP.get("_fecha") or dt.date.today().isoformat()


def price(key: str | None, reg: str) -> dict | None:
    if not key:
        return None
    return (SNAP["precios"].get(key) or {}).get(reg)


# ------------------------------------------------------------------ estilos
BLUE = Font(color="1F4E9E", bold=True)
BOLD = Font(bold=True)
H1 = Font(bold=True, size=14, color="FFFFFF")
HDR = Font(bold=True, color="FFFFFF")
FILL_H1 = PatternFill("solid", fgColor="1F3864")
FILL_HDR = PatternFill("solid", fgColor="2F5597")
FILL_IN = PatternFill("solid", fgColor="FFF2CC")
FILL_SEC = PatternFill("solid", fgColor="D9E1F2")
FILL_TOT = PatternFill("solid", fgColor="E2EFDA")
FILL_EST = PatternFill("solid", fgColor="FCE4D6")
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
    col = "".join(c for c in ref if c.isalpha())
    row = "".join(c for c in ref if c.isdigit())
    wb.defined_names[nm] = DefinedName(nm, attr_text=f"'{ws.title}'!${col}${row}")


def title(ws, text: str, sub: str, width: int) -> None:
    ws["A1"] = text
    ws["A1"].font = H1
    for c in range(1, width + 1):
        ws.cell(row=1, column=c).fill = FILL_H1
    ws["A2"] = sub
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=width)
    ws.row_dimensions[2].height = 60


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
    "Tipo: 'Supuesto' = valor elegido por el candidato, defendible pero no verificado; 'Placeholder' = debe "
    "reemplazarse por un resultado medido; 'Referencia' = dato público con fuente; 'Calculado' = fórmula. "
    "Moneda: USD. Región de precios: Colombia → EE. UU. (East US 2 / us-east-1); España → UE (Spain Central / eu-south-2).",
    7,
)
header(sup, 4, ["Nombre (rango)", "Descripción", "Valor", "Unidad", "Tipo", "Fuente / justificación", "Fecha"])

SUPUESTOS: list = [
    "Operación de la planta",
    ("plantas", "Plantas en el alcance", 1, "plantas", "Supuesto", "Piloto en una planta", INT),
    ("estaciones", "Estaciones (líneas de empaque) por planta", 1, "estaciones", "Supuesto", "Caso base del enunciado. Ver Sensibilidad para 5 y 20", INT),
    ("horas_semana", "Horas de operación por semana", 120, "h/semana", "Referencia", "Enunciado: 24 h/día × 5 días/semana (3 turnos de 8 h)", INT),
    ("semanas_mes", "Semanas promedio por mes", "=52/12", "semanas", "Calculado", "52 / 12", NUM),
    ("horas_mes", "Horas de operación por mes", "=horas_semana*semanas_mes", "h/mes", "Calculado", "≈ 520 h", NUM),
    ("dias_mes", "Días de operación por mes", "=5*semanas_mes", "días/mes", "Calculado", "5 días × 4,33", NUM),
    ("operadores_turno", "Operadores por turno por estación", 1, "personas", "Referencia", "Enunciado", INT),
    ("factor_cobertura", "Factor de cobertura (vacaciones, ausentismo, relevos)", 1.15, "×", "Supuesto", "Cubrir 1 puesto requiere ~15% más horas pagadas que horas de puesto", NUM),
    ("throughput_obj_h", "Objetos procesados por hora por estación", 600, "obj/h", "Supuesto", "1 objeto cada 6 s. Ajustar con el dato real de la línea", INT),
    "Desempeño del sistema automático",
    ("pct_automatizado", "% de objetos resueltos sin humano (decisión ACT correcta)", 0.903, "%", "Medido",
     "Cobertura ACT medida en test, protocolo A, vision-1.0.1 (docs/report/results.json): 90,3 % con 0,09 % de error entre ACT (IC95 0,03-0,27 %). Con desconocidos mezclados (D variante) baja a 73,7-74,9 %. Planta real: re-medir en piloto. Ver Sensibilidad", PCT),
    ("min_caso_revision", "Minutos humanos por caso en cola (REVIEW/REJECT)", 0.5, "min/caso", "Supuesto", "Mirar evidencia + reubicar el objeto desviado (~30 s)", NUM),
    ("fraccion_atencion", "Fracción de las horas de operación de la estación que requiere una persona (supervisión itinerante, atascos, reabastecer)", 0.10, "% horas puesto", "Supuesto",
     "Horas residuales = fracción × horas_mes × factor_cobertura (≈ 60 h pagadas/mes). NO es FTE", PCT),
    "Costo laboral — escenario Colombia",
    ("salario_co_cop", "Salario base mensual operador", 1750000, "COP/mes", "Supuesto", "≈1,2 SMMLV 2025 (1.423.500 COP, Decreto 1572/2024). Actualizar con SMMLV 2026", INT),
    ("factor_prest_co", "Factor prestacional (prestaciones, seguridad social, parafiscales, aux. transporte)", 1.55, "×", "Supuesto", "Rango típico 1,50-1,60", NUM),
    ("jornada_semanal_co", "Jornada máxima semanal", 42, "h/semana", "Referencia",
     "Ley 2101 de 2021: 42 h/semana desde el 15-jul-2026 (Circular MinTrabajo 0101 de 2025)", INT),
    ("horas_lab_mes_co", "Horas laborales contratadas por mes", "=jornada_semanal_co*semanas_mes", "h/mes", "Calculado", "42 × 4,33 ≈ 182 h", NUM),
    ("pct_nocturno", "% de horas en franja nocturna", 0.33, "%", "Supuesto", "1 de 3 turnos nocturno", PCT),
    ("recargo_noct_co", "Recargo nocturno", 0.35, "%", "Referencia", "Código Sustantivo del Trabajo art. 168", PCT),
    ("trm_cop_usd", "Tasa de cambio COP por USD", 4000, "COP/USD", "Supuesto", "Verificar TRM del día (banrep.gov.co)", INT),
    ("costo_hora_co", "Costo cargado por hora-persona (Colombia)", "=salario_co_cop*factor_prest_co/horas_lab_mes_co/trm_cop_usd*(1+recargo_noct_co*pct_nocturno)", "USD/h", "Calculado", "", USD),
    "Costo laboral — escenario España",
    ("salario_es_eur", "Salario bruto anual operador", 22000, "EUR/año", "Supuesto", "Rango de convenios de industria/logística", INT),
    ("ss_empresa_es", "Seguridad Social a cargo de la empresa", 0.32, "%", "Supuesto", "≈31-33%", PCT),
    ("horas_anuales_es", "Horas efectivas anuales por convenio", 1760, "h/año", "Supuesto", "Rango 1.750-1.800 h", INT),
    ("plus_noct_es", "Plus de nocturnidad", 0.25, "%", "Supuesto", "ET art. 36 remite a convenio; 25% habitual", PCT),
    ("usd_por_eur", "USD por EUR", 1.15, "USD/EUR", "Supuesto", "Verificar tipo de cambio del día (BCE)", NUM),
    ("costo_hora_es", "Costo cargado por hora-persona (España)", "=salario_es_eur*(1+ss_empresa_es)/horas_anuales_es*usd_por_eur*(1+plus_noct_es*pct_nocturno)", "USD/h", "Calculado", "", USD),
    "Imágenes y telemetría",
    ("muestreo_act", "% de imágenes ACT que se suben a la nube", 0.02, "%", "Supuesto", "Todas las REVIEW/REJECT + muestra de ACT", PCT),
    ("pct_subidas", "% de imágenes subidas a la nube", "=(1-pct_automatizado)+pct_automatizado*muestreo_act", "%", "Calculado", "", PCT),
    ("kb_imagen", "Tamaño medio por imagen subida (JPEG, anonimizada)", 150, "KB", "Supuesto", "Cámara ~1,3 MP comprimida", INT),
    ("img_dia_estacion", "Imágenes subidas por día por estación", "=throughput_obj_h*24*pct_subidas", "img/día", "Calculado",
     "Se suben directo a Blob/S3 desde el borde (no por IoT Hub/IoT Core)", INT),
    ("gb_imagenes_mes_est", "GB de imágenes nuevas por mes por estación", "=throughput_obj_h*horas_mes*pct_subidas*kb_imagen/1024/1024", "GB/mes", "Calculado", "", NUM),
    ("dias_hot", "Días en tier caliente (Hot / S3 Standard)", 30, "días", "Supuesto", "", INT),
    ("dias_cool", "Días en tier frío (Cool / S3 Standard-IA)", 150, "días", "Supuesto", "Del día 31 al 180", INT),
    ("dias_archivo", "Días en archivo (Archive / Glacier Deep Archive)", 915, "días", "Supuesto", "Hasta 3 años de retención", INT),
    ("gb_auditoria", "GB acumulados en la cuenta de auditoría (ZRS, inmutable)", 20, "GB", "Supuesto", "Exportación diaria de la tabla de auditoría", INT),
    ("msgs_telemetria_dia", "Mensajes de telemetría por día por estación (≤ 4 KB)", 3000, "msgs/día", "Supuesto", "Métricas agregadas cada 30 s + eventos", INT),
    ("msgs_dia_estacion", "Mensajes IoT por día por estación (telemetría + 1 aviso de metadatos por imagen subida)", "=msgs_telemetria_dia+img_dia_estacion", "msgs/día", "Calculado",
     "Los bytes de la imagen no pasan por IoT Hub; solo un mensaje pequeño con su referencia", INT),
    ("gb_logs_mes", "GB de logs / trazas por mes", 10, "GB/mes", "Supuesto", "Con muestreo de trazas", INT),
    "Asistente agéntico (LLM)",
    ("conv_dia_base", "Conversaciones por día (base por planta)", 40, "conv/día", "Supuesto", "", INT),
    ("conv_dia_estacion", "Conversaciones adicionales por día por estación", 20, "conv/día", "Supuesto", "", INT),
    ("conv_mes", "Conversaciones por mes", "=(conv_dia_base+conv_dia_estacion*estaciones)*dias_mes", "conv/mes", "Calculado", "", INT),
    ("tok_in_conv", "Tokens de entrada por conversación (multi-turno, con tools)", 25000, "tokens", "Supuesto", "Medir con `usage` del agente", INT),
    ("tok_out_conv", "Tokens de salida por conversación", 1500, "tokens", "Supuesto", "", INT),
    ("pct_cache", "% de tokens de entrada servidos desde caché", 0.50, "%", "Supuesto", "Prompt de sistema y tools estables", PCT),
    ("pct_modelo_grande", "% de conversaciones enrutadas al modelo grande", 0.15, "%", "Supuesto", "Solo diagnósticos complejos", PCT),
    ("tok_embed_mes", "Tokens de embeddings por mes (reindexado SOP)", 2000000, "tokens", "Supuesto", "", INT),
    "CAPEX e implementación (pago único)",
    ("capex_pc", "PC industrial fanless por estación", 3000, "USD", "Supuesto", "Rango de mercado USD 2.000-4.000", USD0),
    ("capex_camara", "Cámara industrial + óptica + iluminación + soporte", 2500, "USD", "Supuesto", "", USD0),
    ("capex_integracion", "Integración PLC/robot y puesta en marcha por estación", 15000, "USD", "Supuesto", "Ingeniería de automatización, calibración, FAT/SAT", USD0),
    ("capex_planta", "Implementación por planta (piloto, validación, capacitación, nube)", 30000, "USD", "Supuesto", "", USD0),
    ("pct_mant_hw", "Mantenimiento anual del hardware de borde", 0.10, "%/año", "Supuesto", "", PCT),
    "Producción en la nube",
    ("replicas_agente", "Réplicas mínimas del agente en horario de turnos", 2, "réplicas", "Supuesto",
     "≥ 2 para que la redundancia de zona de Container Apps / ECS en 2 AZ sea real", INT),
    ("replicas_bd", "Réplicas de base de datos (1 = sin HA, 2 = HA de zona)", 2, "réplicas", "Supuesto", "Producción: HA. Piloto: 1", INT),
    ("horas_activas_vision_cloud", "Horas activas/mes del servicio de visión en nube (no tiempo real)", 60, "h/mes", "Supuesto", "Escala a cero", INT),
    ("horas_entrenamiento", "Horas de cómputo de reentrenamiento al mes", 10, "h/mes", "Supuesto", "", INT),
    ("uplift_ue", "Factor de precio UE vs EE. UU. cuando no hay precio UE verificado", 1.10, "×", "Supuesto",
     "Solo se usa en filas UE marcadas 'Estimado (US × factor UE)'", NUM),
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
        c.comment = Comment("Medido en COIL-100 (protocolo A). En planta real debe re-medirse en el piloto.", "FinOps")
    row += 1
widths(sup, [24, 58, 16, 13, 12, 70, 12])
sup.freeze_panes = "A5"

# ============================================================ OPERACIÓN ACTUAL
op = wb.create_sheet("Operación actual")
title(op, "Costo de la operación manual actual", "Un operador por turno por estación, 24×5. USD por mes.", 4)
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
    "Servicio", "SKU / medidor", "Precio EE. UU. (USD)", "Precio UE-España (USD)", "Unidad de precio", "Cantidad mensual",
    "Incluir (1/0)", "Costo EE. UU. (USD/mes)", "Costo UE (USD/mes)", "Tipo", "Escala con imágenes (1/0)",
    "Estado precio EE. UU.", "Estado precio UE", "Clave de precio (ver Fuentes)", "Referencia", "Cómo se calcula la cantidad",
]
V = "API verificada"


def cloud_sheet(sheet: str, sub: str, rows: list) -> None:
    ws = wb.create_sheet(sheet)
    title(ws, f"{sheet} — costo mensual por servicio y región", sub, len(CLOUD_HDR))
    header(ws, 4, CLOUD_HDR)
    r = 5
    for item in rows:
        if isinstance(item, str):
            ws.cell(row=r, column=1, value=item).font = BOLD
            for c in range(1, len(CLOUD_HDR) + 1):
                ws.cell(row=r, column=c).fill = FILL_SEC
            r += 1
            continue
        svc, sku, key, est_us, est_eu, unit, qty, incl, tipo, img, ref, how, *opt = item
        nm = opt[0] if opt else None
        pu, pe = price(key, "us"), price(key, "eu")
        if pu:
            p_us, st_us = pu["price"], V
        else:
            p_us, st_us = est_us, "Estimado"
        if pe:
            p_eu, st_eu = pe["price"], V
        elif est_eu is not None:
            p_eu, st_eu = est_eu, "Estimado"
        else:
            p_eu, st_eu = f"=C{r}*uplift_ue", "Estimado (US × factor UE)"
        vals = [svc, sku, p_us, p_eu, unit, qty, incl, f"=C{r}*F{r}*G{r}", f"=D{r}*F{r}*G{r}", tipo, img,
                st_us, st_eu, key or "", ref, how]
        for ci, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=ci, value=v)
            c.border = BOX
            if ci in (2, 15, 16):
                c.alignment = WRAP
        for ci in (3, 4):
            ws.cell(row=r, column=ci).number_format = "0.0000000"
            ws.cell(row=r, column=ci).font = BLUE
            ws.cell(row=r, column=ci).fill = FILL_IN
        for ci in (7, 11):
            ws.cell(row=r, column=ci).font = BLUE
            ws.cell(row=r, column=ci).fill = FILL_IN
        ws.cell(row=r, column=6).number_format = NUM
        ws.cell(row=r, column=8).number_format = USD
        ws.cell(row=r, column=9).number_format = USD
        if nm:
            name(ws, f"C{r}", nm)
        r += 1
    last = r - 1
    p = sheet.lower()
    ws.conditional_formatting.add(f"L5:M{last}", FormulaRule(formula=['LEFT(L5,8)="Estimado"'], fill=FILL_EST))
    r += 1
    ws.cell(row=r, column=6, value="Resumen").font = BOLD
    ws.cell(row=r, column=8, value="EE. UU.").font = BOLD
    ws.cell(row=r, column=9, value="UE-España").font = BOLD
    r += 1
    first_sub = r
    for lbl, crit, key in (("Plataforma fija", "Plataforma fija", "fijo_mes"), ("Por estación", "Por estación", "estacion_total_mes"), ("LLM", "LLM", "llm_mes")):
        ws.cell(row=r, column=6, value=f"Subtotal {lbl}").font = BOLD
        for col, suf in (("H", ""), ("I", "_ue")):
            c = ws[f"{col}{r}"]
            c.value = f'=SUMIF($J$5:$J${last},"{crit}",{col}$5:{col}${last})'
            c.number_format = USD
            c.font = BOLD
            name(ws, f"{col}{r}", f"{p}_{key}{suf}")
        r += 1
    ws.cell(row=r, column=6, value="TOTAL MENSUAL").font = BOLD
    for col, suf in (("H", ""), ("I", "_ue")):
        c = ws[f"{col}{r}"]
        c.value = f"=SUM({col}{first_sub}:{col}{r - 1})"
        c.number_format = USD
        c.font = BOLD
        c.fill = FILL_TOT
        name(ws, f"{col}{r}", f"{p}_total_mes{suf}")
    r += 2
    extra = [
        ("Almacenamiento de imágenes (escala con % subido)", "=SUMPRODUCT({c}$5:{c}$%d,$K$5:$K$%d)" % (last, last), "img_mes", USD),
        ("Costo por estación (marginal)", f"={p}_estacion_total_mes{{s}}/estaciones", "por_estacion_mes", USD),
        ("Costo LLM por conversación", f"={p}_llm_mes{{s}}/conv_mes", "llm_por_conv", "0.0000"),
        ("Costo nube por objeto procesado", f"={p}_total_mes{{s}}/(throughput_obj_h*horas_mes*estaciones)", "por_objeto", "0.00000"),
    ]
    for lbl, f, key, fmt in extra:
        ws.cell(row=r, column=6, value=lbl)
        for col, suf in (("H", ""), ("I", "_ue")):
            c = ws[f"{col}{r}"]
            c.value = f.replace("{c}", col).replace("{s}", suf)
            c.number_format = fmt
            name(ws, f"{col}{r}", f"{p}_{key}{suf}")
        r += 1
    widths(ws, [30, 34, 12, 12, 13, 13, 8, 13, 13, 14, 10, 14, 16, 18, 40, 50])
    ws.freeze_panes = "C5"


IMG_HOT = "=gb_imagenes_mes_est*estaciones*dias_hot/30"
IMG_COOL = "=gb_imagenes_mes_est*estaciones*dias_cool/30"
IMG_ARCH = "=gb_imagenes_mes_est*estaciones*dias_archivo/30"
SMALL_IN = "=conv_mes*(1-pct_modelo_grande)*tok_in_conv*(1-pct_cache)/1000"
SMALL_CA = "=conv_mes*(1-pct_modelo_grande)*tok_in_conv*pct_cache/1000"
SMALL_OUT = "=conv_mes*(1-pct_modelo_grande)*tok_out_conv/1000"
BIG_IN = "=conv_mes*pct_modelo_grande*tok_in_conv*(1-pct_cache)/1000"
BIG_CA = "=conv_mes*pct_modelo_grande*tok_in_conv*pct_cache/1000"
BIG_OUT = "=conv_mes*pct_modelo_grande*tok_out_conv/1000"
AOAI = "https://azure.microsoft.com/pricing/details/azure-openai/"
F, E_, L = "Plataforma fija", "Por estación", "LLM"

# (servicio, sku, clave, est_us, est_eu, unidad, cantidad, incluir, tipo, imágenes, referencia, cómo[, nombre_precio])
AZURE = [
    "Plano de nube — cómputo",
    ("Container Apps (agente)", "Consumo · vCPU activa (0,5 vCPU por réplica)", "az_aca_vcpu", 0.000024, None, "vCPU-segundo",
     "=replicas_agente*0.5*3600*horas_mes", 1, F, 0, "", "réplicas × 0,5 vCPU × horas de operación (2 réplicas en zonas distintas). Sin descontar la cuota gratuita"),
    ("Container Apps (agente)", "Consumo · memoria activa (1 GiB por réplica)", "az_aca_mem", 0.000003, None, "GiB-segundo",
     "=replicas_agente*1*3600*horas_mes", 1, F, 0, "", "réplicas × 1 GiB × horas"),
    ("Container Apps (visión no tiempo real)", "Consumo · vCPU activa (1 vCPU)", "az_aca_vcpu", 0.000024, None, "vCPU-segundo",
     "=3600*horas_activas_vision_cloud", 1, F, 0, "", "Escala a cero"),
    ("Container Apps (visión no tiempo real)", "Consumo · memoria activa (2 GiB)", "az_aca_mem", 0.000003, None, "GiB-segundo",
     "=2*3600*horas_activas_vision_cloud", 1, F, 0, "", ""),
    ("Container Apps — Private Endpoint del entorno", "Environment Private Endpoint", "az_aca_pe", 0.1, None, "hora", "=730", 1, F, 0,
     "https://learn.microsoft.com/azure/container-apps/private-endpoints-with-dns", "Chat accesible solo desde la red corporativa"),
    ("Container Registry", "Basic", "az_acr_basic", 0.1666, None, "día", "=30.4", 1, F, 0, "", ""),
    ("Azure Machine Learning (compute cluster)", "D4s v3 Linux", "az_vm_d4sv3", 0.192, None, "hora", "=horas_entrenamiento", 1, F, 0,
     "", "Workspace sin cargo; cluster escala a 0"),
    "Plano de nube — datos",
    ("PostgreSQL Flexible", "General Purpose D2ds_v5 (2 vCore)", "az_pg_d2ds", 0.178, None, "hora", "=730*replicas_bd", 1, F, 0, "",
     "Réplica HA de zona duplica cómputo"),
    ("PostgreSQL Flexible", "Almacenamiento SSD", "az_pg_storage", 0.115, None, "GB-mes", "=64*replicas_bd", 1, F, 0, "", "64 GB por réplica"),
    ("Azure AI Search", "Basic (1 réplica, sin SLA de consulta)", "az_search_basic", 0.101, None, "hora", "=730", 1, F, 0, "",
     "Alternativa más barata: pgvector en la PostgreSQL"),
    ("Azure OpenAI", "text-embedding-3-small (global)", "az_aoai_emb", 0.00002, None, "1K tokens", "=tok_embed_mes/1000", 1, F, 0, AOAI, ""),
    ("Storage — cuenta de imágenes (LRS)", "Hot LRS", "az_blob_hot_lrs", 0.0208, None, "GB-mes", IMG_HOT, 1, E_, 1, "", "Hot 30 días"),
    ("Storage — cuenta de imágenes (LRS)", "Cool LRS", "az_blob_cool_lrs", 0.0152, None, "GB-mes", IMG_COOL, 1, E_, 1, "", "Cool hasta el día 180"),
    ("Storage — cuenta de imágenes (LRS)", "Archive LRS", "az_blob_archive_lrs", 0.002, None, "GB-mes", IMG_ARCH, 1, E_, 1, "", "Archive hasta 3 años (Archive no admite ZRS)"),
    ("Storage — cuenta de imágenes (LRS)", "Transacciones y recuperaciones (aprox.)", None, 5, None, "mes", "=estaciones", 1, E_, 0,
     "https://azure.microsoft.com/pricing/details/storage/blobs/", "Escrituras desde el borde + lecturas de revisión"),
    ("Storage — cuenta de auditoría (ZRS, inmutable)", "Hot ZRS", "az_blob_hot_zrs", 0.0228, None, "GB-mes", "=gb_auditoria", 1, F, 0, "",
     "Cuenta separada: la redundancia es por cuenta"),
    "Borde y dispositivos",
    ("IoT Hub", "S1 (400.000 msgs/día por unidad, 4 KB)", "az_iothub_s1", 25, None, "unidad-mes",
     "=MAX(1,ROUNDUP(msgs_dia_estacion*estaciones/400000,0))", 1, F, 0, "",
     "Solo telemetría y avisos ≤ 4 KB; las imágenes van directo a Blob"),
    ("IoT Edge runtime", "Open source", None, 0, 0, "dispositivo-mes", "=estaciones", 1, E_, 0, "https://azure.microsoft.com/products/iot-edge/", "Sin licencia"),
    ("AKS Edge Essentials (alternativa)", "Licencia por dispositivo (API sin región, 2026-10-24)", None, 2.5, None, "dispositivo-mes", "=estaciones", 0, E_, 0, "",
     "Solo si se elige Kubernetes en el borde"),
    ("Defender for Servers P2 (vía Arc)", "Por PC industrial", "az_def_servers_p2", 0.02, None, "nodo-hora", "=730*estaciones", 1, E_, 0, "", ""),
    "Seguridad, red y observabilidad",
    ("Key Vault", "Standard · operaciones", "az_kv_ops", 0.03, None, "10K ops", "=20", 1, F, 0, "", "~200K operaciones/mes"),
    ("Private Endpoints de PaaS", "Storage ×2, PostgreSQL, Key Vault, OpenAI, Search", None, 0.01, 0.01, "hora", "=6*730", 1, F, 0,
     "https://azure.microsoft.com/pricing/details/private-link/", "6 endpoints (precio de lista no localizado en la API → estimado)"),
    ("Log Analytics + Application Insights", "Analytics Logs · ingesta", "az_la_ingest", 2.76, None, "GB", "=MAX(0,gb_logs_mes-5)", 1, F, 0, "",
     "Primeros 5 GB/mes gratis"),
    ("Defender for Containers", "Escaneo de imágenes", "az_def_cont_img", 0.29, None, "imagen", "=10", 1, F, 0, "", ""),
    ("Defender for Storage", "Por cuenta (aprox.)", None, 10, None, "cuenta-mes", "=2", 1, F, 0,
     "https://azure.microsoft.com/pricing/details/defender-for-cloud/", "2 cuentas (imágenes, auditoría)"),
    ("Front Door Standard + WAF (opcional)", "Tarifa base", None, 35, None, "mes", "=1", 0, F, 0,
     "https://azure.microsoft.com/pricing/details/frontdoor/", "Solo si el chat se expone fuera de la red corporativa"),
    "Asistente agéntico (LLM) — Data Zone (EE. UU. o UE según región)",
    ("Azure OpenAI", "gpt-4.1-mini · entrada", "az_aoai_mini_in", 0.00044, None, "1K tokens", SMALL_IN, 1, L, 0, AOAI, "85% de conversaciones"),
    ("Azure OpenAI", "gpt-4.1-mini · entrada en caché", "az_aoai_mini_cache", 0.00011, None, "1K tokens", SMALL_CA, 1, L, 0, AOAI, ""),
    ("Azure OpenAI", "gpt-4.1-mini · salida", "az_aoai_mini_out", 0.00176, None, "1K tokens", SMALL_OUT, 1, L, 0, AOAI, ""),
    ("Azure OpenAI", "gpt-4.1 · entrada", "az_aoai_big_in", 0.0022, None, "1K tokens", BIG_IN, 1, L, 0, AOAI, "15% de conversaciones", "azure_big_in"),
    ("Azure OpenAI", "gpt-4.1 · entrada en caché", "az_aoai_big_cache", 0.00055, None, "1K tokens", BIG_CA, 1, L, 0, AOAI, "", "azure_big_cache"),
    ("Azure OpenAI", "gpt-4.1 · salida", "az_aoai_big_out", 0.0088, None, "1K tokens", BIG_OUT, 1, L, 0, AOAI, "", "azure_big_out"),
]

AWSP = "https://aws.amazon.com/{}/pricing/"
AWS = [
    "Plano de nube — cómputo",
    ("ECS Fargate (agente)", "vCPU (0,5 por tarea)", "aws_fargate_vcpu", 0.04048, None, "vCPU-hora", "=replicas_agente*0.5*horas_mes", 1, F, 0, "", "2 tareas en 2 AZ"),
    ("ECS Fargate (agente)", "Memoria (1 GB por tarea)", "aws_fargate_gb", 0.004445, None, "GB-hora", "=replicas_agente*1*horas_mes", 1, F, 0, "", ""),
    ("ECS Fargate (visión no tiempo real)", "vCPU (1)", "aws_fargate_vcpu", 0.04048, None, "vCPU-hora", "=horas_activas_vision_cloud", 1, F, 0, "", "Tarea bajo demanda"),
    ("ECS Fargate (visión no tiempo real)", "Memoria (2 GB)", "aws_fargate_gb", 0.004445, None, "GB-hora", "=2*horas_activas_vision_cloud", 1, F, 0, "", ""),
    ("Application Load Balancer", "Hora", "aws_alb_hr", 0.0225, None, "hora", "=730", 1, F, 0, "", ""),
    ("Application Load Balancer", "LCU (~1)", "aws_alb_lcu", 0.008, None, "LCU-hora", "=730", 1, F, 0, "", ""),
    ("NAT Gateway", "Hora (1 AZ)", None, 0.045, None, "hora", "=730", 1, F, 0, "https://aws.amazon.com/vpc/pricing/",
     "Precio en la oferta EC2 (no consultada) → estimado"),
    ("Amazon ECR", "Almacenamiento", "aws_ecr", 0.10, None, "GB-mes", "=3", 1, F, 0, "", ""),
    ("SageMaker AI", "Training ml.m5.large", "aws_sm_train", 0.115, None, "hora", "=horas_entrenamiento", 1, F, 0, "",
     "Model Registry y Pipelines sin cargo propio"),
    "Plano de nube — datos",
    ("Aurora PostgreSQL Serverless v2", "ACU-hora (promedio 1 ACU por instancia)", "aws_aurora_acu", 0.12, None, "ACU-hora", "=1*730*replicas_bd", 1, F, 0, "",
     "Escritor + lector en otra AZ. Estado, auditoría y pgvector de Bedrock KB"),
    ("Aurora PostgreSQL", "Almacenamiento", "aws_aurora_storage", 0.10, None, "GB-mes", "=64", 1, F, 0, "", "Compartido entre instancias"),
    ("Bedrock Knowledge Bases", "Embeddings Titan Text v2", None, 0.00002, None, "1K tokens", "=tok_embed_mes/1000", 1, F, 0, AWSP.format("bedrock"), ""),
    ("OpenSearch Serverless (alternativa)", "OCU-hora (mín. 2 × 0,5 OCU)", None, 0.24, None, "OCU-hora", "=730", 0, F, 0,
     AWSP.format("opensearch-service"), "No incluido: mínimo desproporcionado para pocos SOP"),
    ("S3 — bucket de imágenes", "Standard", "aws_s3_std", 0.023, None, "GB-mes", IMG_HOT, 1, E_, 1, "", "Lifecycle: Standard→IA día 30"),
    ("S3 — bucket de imágenes", "Standard-IA", "aws_s3_ia", 0.0125, None, "GB-mes", IMG_COOL, 1, E_, 1, "", "IA→Deep Archive día 180"),
    ("S3 — bucket de imágenes", "Glacier Deep Archive", "aws_s3_gda", 0.00099, None, "GB-mes", IMG_ARCH, 1, E_, 1, "", "Expira a 3 años"),
    ("S3 — bucket de imágenes", "Solicitudes y recuperaciones (aprox.)", None, 5, None, "mes", "=estaciones", 1, E_, 0, AWSP.format("s3"), ""),
    ("S3 — bucket de auditoría (Object Lock)", "Standard", "aws_s3_std", 0.023, None, "GB-mes", "=gb_auditoria", 1, F, 0, "", "S3 Standard ya replica en ≥3 AZ"),
    "Borde y dispositivos",
    ("IoT Core", "Mensajería", "aws_iot_msg", 0.000001, None, "mensaje", "=msgs_dia_estacion*estaciones*dias_mes", 1, E_, 0, "",
     "Telemetría + avisos; las imágenes van a S3 por Stream Manager"),
    ("IoT Core", "Conectividad", "aws_iot_conn", 0.00000008, None, "minuto", "=43800*estaciones", 1, E_, 0, "", ""),
    ("IoT Greengrass V2", "Dispositivo core activo", "aws_greengrass", 0.16, None, "dispositivo-mes", "=estaciones", 1, E_, 0, "", ""),
    ("Systems Manager (instancia híbrida avanzada)", "Por PC industrial", None, 0.00695, None, "hora", "=730*estaciones", 1, E_, 0,
     AWSP.format("systems-manager"), ""),
    "Seguridad, red y observabilidad",
    ("Secrets Manager", "Secreto", "aws_secrets", 0.40, None, "secreto-mes", "=8", 1, F, 0, "", ""),
    ("VPC Interface Endpoints", "Endpoint-AZ", "aws_vpce_hr", 0.01, None, "hora", "=5*2*730", 1, F, 0, "", "5 endpoints × 2 AZ"),
    ("CloudWatch Logs", "Ingesta (Standard)", "aws_cw_logs", 0.50, None, "GB", "=gb_logs_mes", 1, F, 0, "", ""),
    ("GuardDuty", "Cuenta pequeña", None, 15, None, "mes", "=1", 1, F, 0, AWSP.format("guardduty"), "Depende del volumen de eventos"),
    ("AWS WAF (opcional)", "Web ACL + reglas", None, 10, None, "mes", "=1", 0, F, 0, AWSP.format("waf"), ""),
    "Asistente agéntico (LLM)",
    ("Bedrock", "Claude Haiku 4.5 · entrada", None, 0.001, None, "1K tokens", SMALL_IN, 1, L, 0, AWSP.format("bedrock"), "85%"),
    ("Bedrock", "Claude Haiku 4.5 · lectura de caché", None, 0.0001, None, "1K tokens", SMALL_CA, 1, L, 0, AWSP.format("bedrock"),
     "Escritura en caché (~1,25×) no modelada"),
    ("Bedrock", "Claude Haiku 4.5 · salida", None, 0.005, None, "1K tokens", SMALL_OUT, 1, L, 0, AWSP.format("bedrock"), ""),
    ("Bedrock", "Claude Sonnet 4.5 · entrada", None, 0.003, None, "1K tokens", BIG_IN, 1, L, 0, AWSP.format("bedrock"), "15%"),
    ("Bedrock", "Claude Sonnet 4.5 · lectura de caché", None, 0.0003, None, "1K tokens", BIG_CA, 1, L, 0, AWSP.format("bedrock"), ""),
    ("Bedrock", "Claude Sonnet 4.5 · salida", None, 0.015, None, "1K tokens", BIG_OUT, 1, L, 0, AWSP.format("bedrock"), ""),
]

cloud_sheet(
    "Azure",
    f"Precios de la Azure Retail Prices API consultados el {FECHA}: East US 2 (escenario Colombia) y Spain Central "
    "(escenario España). 'API verificada' = medidor exacto encontrado (ver hoja Fuentes); 'Estimado' (naranja) = "
    "referencia sin medidor localizado. Costo = precio × cantidad × incluir.",
    AZURE,
)
cloud_sheet(
    "AWS",
    f"Precios de la AWS Price List Bulk API consultados el {FECHA}: us-east-1 (Colombia) y eu-south-2 Spain (España). "
    "Bedrock (Claude), NAT Gateway, GuardDuty y SSM son 'Estimado' (no localizados por usagetype). Para la UE, Bedrock "
    "se asume igual a EE. UU. ajustado por el factor UE.",
    AWS,
)

# ------------------------------------------------------------ fórmulas reutilizables
MANT = "estaciones*plantas*(capex_pc+capex_camara)*pct_mant_hw/12"
CAPEX = "(estaciones*plantas*(capex_pc+capex_camara+capex_integracion)+capex_planta*plantas)"


def cola(p: str, s_: str) -> str:
    return f"estaciones*plantas*throughput_obj_h*horas_mes*(1-{p})*min_caso_revision/60*costo_hora_{s_}"


def ahorro(p: str, s_: str, u: str, cloud: str) -> str:
    """Ahorro mensual con % automatizado = p (la nube escala el almacenamiento de imágenes con p)."""
    nube = (f"({cloud}_total_mes{u}-{cloud}_img_mes{u}+{cloud}_img_mes{u}*((1-{p})+{p}*muestreo_act)/pct_subidas)*plantas")
    supv = f"estaciones*plantas*fraccion_atencion*horas_mes*factor_cobertura*costo_hora_{s_}"
    return f"actual_mes_{s_}-({nube}+{MANT}+{cola(p, s_)}+{supv})"


def equilibrio(s_: str, u: str, cloud: str) -> str:
    """% automatizado con ahorro = 0 (el ahorro es lineal en p)."""
    a0, a1 = ahorro("0", s_, u, cloud), ahorro("1", s_, u, cloud)
    return f"-({a0})/(({a1})-({a0}))"


# ============================================================ COMPARATIVO
cmp_ = wb.create_sheet("Comparativo")
title(
    cmp_,
    "Comparativo: operación actual vs Azure vs AWS",
    "USD/mes. Colombia usa precios de región EE. UU.; España usa precios de región UE-España. "
    "Costo nuevo = nube + mantenimiento de borde + humano residual. El robot existe en ambos escenarios.",
    5,
)
header(cmp_, 4, ["Concepto", "Colombia · Azure", "Colombia · AWS", "España · Azure", "España · AWS"])
COLS = {"B": ("co", "azure", ""), "C": ("co", "aws", ""), "D": ("es", "azure", "_ue"), "E": ("es", "aws", "_ue")}
CMP = [
    ("Costo mensual operación actual", lambda s, c, u: f"=actual_mes_{s}", USD0),
    ("Nube: plataforma fija", lambda s, c, u: f"={c}_fijo_mes{u}*plantas", USD0),
    ("Nube: por estaciones", lambda s, c, u: f"={c}_estacion_total_mes{u}*plantas", USD0),
    ("Nube: LLM del asistente", lambda s, c, u: f"={c}_llm_mes{u}*plantas", USD0),
    ("Mantenimiento hardware de borde", lambda s, c, u: "=estaciones*plantas*(capex_pc+capex_camara)*pct_mant_hw/12", USD0),
    ("Humano residual: cola de revisión", lambda s, c, u: f"=estaciones*plantas*throughput_obj_h*horas_mes*(1-pct_automatizado)*min_caso_revision/60*costo_hora_{s}", USD0),
    ("Humano residual: atención itinerante", lambda s, c, u: f"=estaciones*plantas*fraccion_atencion*horas_mes*factor_cobertura*costo_hora_{s}", USD0),
    ("COSTO MENSUAL NUEVO", lambda s, c, u: "=SUM({col}6:{col}11)", USD0),
    ("AHORRO MENSUAL", lambda s, c, u: "={col}5-{col}12", USD0),
    ("Ahorro mensual (%)", lambda s, c, u: "=IF({col}5>0,{col}13/{col}5,0)", PCT),
    ("Costo anual actual", lambda s, c, u: "={col}5*12", USD0),
    ("Costo anual nuevo (OPEX)", lambda s, c, u: "={col}12*12", USD0),
    ("CAPEX único (borde + integración + implementación)", lambda s, c, u: "=estaciones*plantas*(capex_pc+capex_camara+capex_integracion)+capex_planta*plantas", USD0),
    ("Payback (meses)", lambda s, c, u: '=IF({col}13>0,{col}17/{col}13,"No recupera")', NUM),
    ("Ahorro neto a 3 años (36 × ahorro − CAPEX)", lambda s, c, u: "=36*{col}13-{col}17", USD0),
    ("Costo nube por objeto procesado (USD)", lambda s, c, u: "=SUM({col}6:{col}8)/(throughput_obj_h*horas_mes*estaciones*plantas)", "0.00000"),
    ("Humano residual / costo actual (%)", lambda s, c, u: "=({col}10+{col}11)/{col}5", PCT),
]
for i, (lbl, fn, fmt) in enumerate(CMP, start=5):
    cmp_.cell(row=i, column=1, value=lbl)
    for col, (s, c, u) in COLS.items():
        cell = cmp_[f"{col}{i}"]
        cell.value = fn(s, c, u).replace("{col}", col)
        cell.number_format = fmt
        cell.border = BOX
    if lbl.isupper():
        for col in "ABCDE":
            cmp_[f"{col}{i}"].font = BOLD
            cmp_[f"{col}{i}"].fill = FILL_TOT
r = 5 + len(CMP) + 1
cmp_.cell(row=r, column=1, value="Diferencia de nube Azure − AWS / ahorro mensual Azure").font = BOLD
cmp_[f"B{r}"] = f"=(SUM(B6:B8)-SUM(C6:C8))/B13"
cmp_[f"D{r}"] = f"=(SUM(D6:D8)-SUM(E6:E8))/D13"
for col in "BD":
    cmp_[f"{col}{r}"].number_format = PCT
r += 1
cmp_.cell(row=r, column=1, value="% automatizado de equilibrio (ahorro = 0)").font = BOLD
for col, (s_, c_, u_) in COLS.items():
    cmp_[f"{col}{r}"] = f"={equilibrio(s_, u_, c_)}"
    cmp_[f"{col}{r}"].number_format = PCT
    cmp_[f"{col}{r}"].font = BOLD
r += 2
for n in [
    "Lectura rápida:",
    "• El ahorro viene de retirar 3 turnos de operador por estación; la nube es una fracción pequeña del costo laboral evitado.",
    "• El factor que más mueve el resultado es % automatizado (cola de revisión) y el costo laboral local, no el proveedor de nube.",
    "• Con 1 estación en Colombia el payback es largo: el CAPEX de implementación por planta pesa mucho (ver Sensibilidad).",
    "• Ningún número de esta hoja está hardcodeado: todo sale de Supuestos, Operación actual, Azure y AWS.",
]:
    cmp_.cell(row=r, column=1, value=n)
    r += 1
widths(cmp_, [56, 18, 18, 18, 18])

# ============================================================ SENSIBILIDAD
sen = wb.create_sheet("Sensibilidad")
title(sen, "Sensibilidad", "Celdas azules de la primera columna son editables. Todo lo demás se recalcula desde Supuestos y las "
      "hojas de nube (Azure; Colombia con precios EE. UU., España con precios UE).", 7)

sen["A4"] = ("1) % automatizado (estaciones de Supuestos) — Azure. 90,3% = medido (vision-1.0.1, protocolo A); 73,7-74,9% = medido con objetos "
             "desconocidos mezclados (variante D). El almacenamiento de imágenes también escala con el %.")
sen["A4"].font = BOLD
header(sen, 5, ["% automatizado", "Residual cola CO", "Ahorro CO", "Payback CO (meses)", "Residual cola ES", "Ahorro ES", "Payback ES (meses)"])
PCTS = [0.737, 0.749, 0.80, 0.90, 0.903, 0.95, 0.99]
for i, p in enumerate(PCTS, start=6):
    c = sen.cell(row=i, column=1, value=p)
    c.number_format = PCT
    c.font = BLUE
    c.fill = FILL_IN
    for base, s_, u in ((2, "co", ""), (5, "es", "_ue")):
        Lr = get_column_letter(base)
        A = get_column_letter(base + 1)
        sen.cell(row=i, column=base, value=f"={cola(f'$A{i}', s_)}").number_format = USD0
        sen.cell(row=i, column=base + 1, value=f"={ahorro(f'$A{i}', s_, u, 'azure')}").number_format = USD0
        sen.cell(row=i, column=base + 2, value=f'=IF({A}{i}>0,{CAPEX}/{A}{i},"No recupera")').number_format = NUM
r0 = 6 + len(PCTS)
sen.cell(row=r0, column=1, value="Equilibrio (ahorro = 0)").font = BOLD
for base, s_, u in ((3, "co", ""), (6, "es", "_ue")):
    c = sen.cell(row=r0, column=base, value=f"={equilibrio(s_, u, 'azure')}")
    c.number_format = PCT
    c.font = BOLD

T2 = r0 + 2
sen.cell(row=T2, column=1, value="2) Estaciones por planta (% automatizado de Supuestos) — Azure").font = BOLD
header(sen, T2 + 1, ["Estaciones", "Costo actual CO", "Costo nuevo CO", "Payback CO (meses)", "Costo actual ES", "Costo nuevo ES", "Payback ES (meses)"])
for i, n in enumerate([1, 5, 20], start=T2 + 2):
    c = sen.cell(row=i, column=1, value=n)
    c.font = BLUE
    c.fill = FILL_IN
    for base, s_, u in ((2, "co", ""), (5, "es", "_ue")):
        A = get_column_letter(base)
        N = get_column_letter(base + 1)
        sen.cell(row=i, column=base, value=f"=actual_estacion_{s_}*$A{i}*plantas").number_format = USD0
        nube = (f"(azure_fijo_mes{u}+azure_por_estacion_mes{u}*$A{i}"
                f"+azure_llm_por_conv{u}*(conv_dia_base+conv_dia_estacion*$A{i})*dias_mes)*plantas")
        resid = (f"$A{i}*plantas*(throughput_obj_h*horas_mes*(1-pct_automatizado)*min_caso_revision/60*costo_hora_{s_}"
                 f"+fraccion_atencion*horas_mes*factor_cobertura*costo_hora_{s_}+(capex_pc+capex_camara)*pct_mant_hw/12)")
        sen.cell(row=i, column=base + 1, value=f"={nube}+{resid}").number_format = USD0
        capex = f"($A{i}*(capex_pc+capex_camara+capex_integracion)+capex_planta)*plantas"
        sen.cell(row=i, column=base + 2, value=f'=IF({A}{i}>{N}{i},{capex}/({A}{i}-{N}{i}),"No recupera")').number_format = NUM

T3 = T2 + 7
sen.cell(row=T3, column=1, value="3) Volumen del asistente: costo LLM mensual por planta (precios EE. UU.)").font = BOLD
header(sen, T3 + 1, ["Conversaciones/día", "Azure LLM (USD/mes)", "AWS LLM (USD/mes)", "Azure si todo va a gpt-4.1", "Peso LLM en nube Azure", "", ""])
for i, n in enumerate([20, 60, 200, 1000], start=T3 + 2):
    c = sen.cell(row=i, column=1, value=n)
    c.font = BLUE
    c.fill = FILL_IN
    sen.cell(row=i, column=2, value=f"=azure_llm_por_conv*$A{i}*dias_mes").number_format = USD0
    sen.cell(row=i, column=3, value=f"=aws_llm_por_conv*$A{i}*dias_mes").number_format = USD0
    big = "(tok_in_conv*((1-pct_cache)*azure_big_in+pct_cache*azure_big_cache)+tok_out_conv*azure_big_out)/1000"
    sen.cell(row=i, column=4, value=f"={big}*$A{i}*dias_mes").number_format = USD0
    sen.cell(row=i, column=5, value=f"=B{i}/(azure_fijo_mes+azure_estacion_total_mes+B{i})").number_format = PCT
sen.cell(row=T3 + 7, column=1, value="La columna 'todo a gpt-4.1' usa las celdas de precio gpt-4.1 de la hoja Azure (rangos azure_big_*).")
widths(sen, [20, 22, 20, 24, 22, 20, 20])

# ============================================================ FUENTES
src = wb.create_sheet("Fuentes")
title(src, "Fuentes, trazas de consulta y método",
      f"Snapshot: docs/cloud/precios_snapshot.json (fecha {FECHA}). Regenerar con "
      "`uv run python scripts/build_finops.py --refresh-prices`. Cada fila es una consulta reproducible.", 9)
header(src, 4, ["Clave", "Región", "Precio USD", "Unidad", "Medidor / usagetype", "SKU / id", "Producto / oferta", "Fecha", "Consulta"])
r = 5
for key in PRICE_SPECS:
    for reg in REGIONS:
        p = price(key, reg)
        if p:
            vals = [key, p["region"], p["price"], p["unit"], p["meter"], p.get("meter_id") or p.get("sku"), p["product"], p.get("fecha"), p["query"]]
        else:
            vals = [key, REGIONS[reg]["label"], None, None, "NO ENCONTRADO → se usa valor 'Estimado'", None, None, FECHA, None]
        for ci, v in enumerate(vals, start=1):
            src.cell(row=r, column=ci, value=v)
        src.cell(row=r, column=9).alignment = Alignment(wrap_text=False)
        r += 1
r += 1
for k, v in [
    ("Método", "Precio unitario × cantidad mensual × incluir. Precios de lista sin descuentos (EA/MACC/EDP), sin reservas ni impuestos."),
    ("Estimados", "Bedrock (Claude Haiku 4.5 $1/$5 y Sonnet 4.5 $3/$15 por 1M tokens según https://aws.amazon.com/bedrock/pricing/), NAT Gateway, "
     "GuardDuty, SSM, Private Endpoints de PaaS en Azure, Defender for Storage, transacciones de almacenamiento."),
    ("Limitaciones", "No incluye egress de red, soporte (Developer/Business), ni licencias corporativas existentes."),
    ("SMMLV Colombia 2025", "Decreto 1572 de 2024 (1.423.500 COP)"),
    ("Jornada Colombia", "Ley 2101 de 2021; 42 h/semana desde el 15-jul-2026 (https://normativa.colpensiones.gov.co/compilacion/docs/circular_mintra_0101_2025.htm)"),
    ("Recargo nocturno Colombia", "Código Sustantivo del Trabajo, art. 168"),
]:
    src.cell(row=r, column=1, value=k).font = BOLD
    src.cell(row=r, column=2, value=v)
    r += 1
widths(src, [22, 14, 12, 12, 40, 38, 40, 12, 120])

wb._sheets = [wb[n] for n in ["Supuestos", "Operación actual", "Azure", "AWS", "Comparativo", "Sensibilidad", "Fuentes"]]
OUT.parent.mkdir(parents=True, exist_ok=True)
wb.save(OUT)
print(f"OK → {OUT}")
