"""Genera docs/report/vision_report.pdf y docs/model_card.md a partir de docs/report/results.json,
models/<versión>/metadata.json y las figuras de docs/report/figures/ (todas producidas por el notebook).

Las cifras de cabecera son las de la SELECCIÓN ANIDADA por protocolo (independientes); la configuración desplegada
(src/vision/recipe.py) se reporta aparte con su salvedad de selección.

Uso: uv run python scripts/build_vision_report.py
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "docs" / "report"
FIG = REP / "figures"
RES = json.loads((REP / "results.json").read_text())
import yaml  # noqa: E402

VERSION = yaml.safe_load((ROOT / "configs" / "vision.yaml").read_text())["version"]  # versión publicada que se documenta
META = json.loads((ROOT / "models" / VERSION / "metadata.json").read_text())
RS = RES["recipe_summary"]
RR = RES["recipe"]["results"]["classic"]
RN = RES["recipe"]["nested"]
RECIPE = RES["recipe"]["recipe"]
P = ["A", "B", "C", "D_strict", "D"]
PNAME = {"A": "A · Interpolación", "B": "B · Sector ciego 60°", "C": "C · Escasez (12 vistas)",
         "D_strict": "D estricto · desconocidos", "D": "D variante · mixto"}


def pct(x, d=1):
    return "N/A" if x is None else f"{100 * x:.{d}f}%"


def num(x, d=2):
    return "—" if x is None else f"{x:.{d}f}"


def ci(x, d=2):
    return "N/A" if not x or x[0] is None else f"[{100 * x[0]:.{d}f}, {100 * x[1]:.{d}f}]%"


def deg(x):
    return "—" if x is None else f"{x:.2f}°"


# ---------------------------------------------------------------- tablas

def headline_rows(key="nested_classic"):
    rows = [["Protocolo", "Modelo", "Exactitud", "MAE ángulo", "Éxito e2e", "% ACT", "n ACT", "Error|ACT", "IC95 Wilson"]]
    for p in P:
        c = RS[p][key]
        model = c.get("selected_candidate", "logreg C=1").replace("{", "").replace("}", "").replace("'", "")
        rows.append([PNAME[p], model, pct(c["class_accuracy"], 2), deg(c["angle_mae_deg"]),
                     pct(c["end_to_end_success"]), pct(c["coverage_act"]), c["n_act"], pct(c["error_among_act"], 2),
                     ci(c["error_among_act_wilson95"])])
    return rows


def robustness_rows(p):
    rob = RR[p]["robustness"]
    if p == "A":
        rows = [["Perturbación", "Exactitud", "MAE ángulo", "Éxito e2e", "% ACT", "n ACT", "Error|ACT", "IC95"]]
        for name, r in rob.items():
            e = r["error_among_act"]
            rows.append([name, pct(r["class_accuracy"], 2), deg(r["angle_mae_deg"]), pct(r["end_to_end_success"]),
                         pct(r["coverage_act"]), r["n_act"], pct(e["rate"], 2), ci(e["wilson95"])])
    else:
        rows = [["Perturbación", "Desconocidos rechazados", "Desconocidos a ACT", "IC95"]]
        for name, r in rob.items():
            rows.append([name, pct(r["unknown_rejected"]["rate"]), pct(r["unknown_reaches_act"]["rate"], 2),
                         ci(r["unknown_reaches_act"]["wilson95"])])
    return rows


def nested_val_rows(p):
    rows = [["Candidato", "val exactitud", "val ECE", "val % ACT", "val error|ACT", "k", "Autom."]]
    for r in RN[p]["selection_table_val"]:
        star = " *" if r["candidate"] == RN[p]["selected_candidate"] else ""
        rows.append([r["candidate"].replace("{", "").replace("}", "").replace("'", "") + star, pct(r["val_class_accuracy"], 2),
                     num(r["val_ece"], 3), pct(r["val_coverage"]), pct(r["val_error_among_act"], 2), r["k"],
                     "sí" if r["automation_enabled"] else "no"])
    return rows


def thresholds_rows():
    rows = [["Protocolo", "k (val)", "Conf. mín.", "Incert. máx.", "Dist. desc.", "Val: n ACT", "Val: error|ACT",
             "Val: desc.→ACT", "Autom."]]
    for p in P:
        r = RR[p]
        t, v = r["thresholds"], r["threshold_validation"]
        rows.append([PNAME[p], r["k_selected_on_val"], num(t["min_class_confidence"]),
                     "desactivada" if t["max_angle_uncertainty_deg"] < 0 else f"{t['max_angle_uncertainty_deg']:.1f}°",
                     num(t["unknown_distance"]), v["val_n_act"], pct(v["val_error_among_act"], 2),
                     pct((v.get("unknown_false_accept_val") or {}).get("rate"), 2), "sí" if r["automation_enabled"] else "no"])
    return rows


def comparison_rows():
    rows = [["Prot.", "Modelo", "Exactitud", "MAE ángulo", "Éxito e2e", "% ACT", "Error|ACT", "IC95"]]
    for p in P:
        for key, lab in (("nested_classic", "Selección anidada"), ("recipe_classic", "Config. desplegada (clásica)"),
                         ("baseline_raw_1nn", "Base píxeles 1-NN (siempre ACT)"), ("recipe_resnet18", "Config. desplegada + ResNet18")):
            m = RS[p].get(key)
            if not m:
                continue
            rows.append([p.replace("_strict", " estr."), lab, pct(m["class_accuracy"], 2), deg(m["angle_mae_deg"]),
                         pct(m["end_to_end_success"]), pct(m["coverage_act"]), pct(m["error_among_act"], 2),
                         ci(m.get("error_among_act_wilson95"))])
    return rows


def lat_rows():
    lat = RES["cost"]["latency"]
    rows = [["Medición", "p50 (ms)", "p95 (ms)", "media (ms)"]]
    for k, v in lat.items():
        rows.append([k, num(v.get("p50_ms")), num(v.get("p95_ms")), num(v.get("mean_ms"))])
    return rows


def _min_val_risk(p, feature="resnet18"):
    g = [x for x in RES["recipe"]["results"][feature][p]["val_uncertainty_grid"] if x["n_act"] >= 50]
    return min((x["error_among_act"] for x in g), default=None)


# ---------------------------------------------------------------- model card

def model_card_md() -> str:
    th = META["thresholds"]
    tv = META["threshold_validation"]
    ufa = tv.get("unknown_false_accept_val") or {}
    lat = RES["cost"]["latency"]
    lc = next((v.get("p50_ms") for k, v in lat.items() if k.startswith("classic")), None)
    ds_ = RS["D_strict"]["nested_classic"]
    dv = RS["D"]["nested_classic"]
    robA = RR["A"]["robustness"]
    lines = [
        f"# Model card — {META['version']}",
        "",
        f"*Generada automáticamente por `scripts/build_vision_report.py` el {date.today().isoformat()} a partir de "
        "`docs/report/results.json` y `models/<versión>/metadata.json`.*",
        "",
        "## Uso previsto",
        "Identificar el objeto (SKU) de una imagen de estación, estimar su ángulo de rotación en el plano [0, 360) con "
        "un score de incertidumbre en grados, y devolver las vistas de referencia más parecidas como evidencia. La "
        "salida incluye una decisión **ACT / REVIEW / REJECT**: el robot solo recibe un comando `PICK` con ángulo cuando "
        "la decisión es ACT; en cualquier otro caso recibe `HOLD` y el caso va a cola humana. ACT no garantiza acierto: "
        "el riesgo residual observado se reporta abajo.",
        "",
        "## Modelo desplegado",
        f"- Configuración `{RECIPE['name']}` (`src/vision/recipe.py`): preprocesado ({RECIPE['preprocessing']}) → "
        f"{RECIPE['features']} → `{RECIPE['clf']}` {json.dumps(RECIPE['params'])}.",
        f"- Aumentación: {RECIPE['augmentation']}.",
        f"- Ángulo: recuperación condicionada al objeto; k de interpolación elegido en validación (artefacto: k = {META['k_angle_refine']}).",
        "- Score de incertidumbre angular: separación angular equivalente a la distancia al vecino más cercano según la "
        "curva distancia-vs-ángulo del objeto. Es un **score heurístico en grados, no una cota del error**; su umbral se "
        "elige para que el riesgo observado en validación P(clase incorrecta o |error| > 10° | ACT) sea ≤ 2 %.",
        f"- Umbrales: confianza ≥ {th['min_class_confidence']}, score ≤ {th['max_angle_uncertainty_deg']}°, distancia de "
        f"desconocido ≤ {th['unknown_distance']:.2f}, razón de novedad ≤ {th.get('max_novelty_ratio', float('nan')):.2f}. Validación de producción (90 conocidos paso 10° + 10 pseudo-desconocidos, "
        f"vistas limpias + perturbadas): {tv['val_n_act']} ACT, error {pct(tv['val_error_among_act'], 2)} (IC95 "
        f"{ci(tv['val_error_wilson95'])}); pseudo-desconocidos que llegan a ACT {pct(ufa.get('rate'), 2)} (criterio ≤ 5 %). "
        "Esta validación comparte imágenes con tests de los protocolos, así que no es una estimación independiente.",
        f"- Clases con ambigüedad rotacional (→ REVIEW): {', '.join(META.get('symmetric_classes', [])) or 'ninguna'}.",
        f"- Artefacto: {META.get('artifact_size_mb')} MB, SHA-256 `{META.get('model_sha256', '')[:16]}…`, snapshot de código "
        f"`{META.get('code_snapshot_sha256', '')[:16]}…` (incluido como code_snapshot.tar.gz), commit "
        f"`{META.get('git', {}).get('commit', '')[:10]}` (árbol sucio: {META.get('git', {}).get('dirty')}); latencia p50 ≈ "
        f"{num(lc, 1)} ms/imagen en CPU.",
        "- Trazabilidad: `uv run mlflow ui --backend-store-uri sqlite:///mlflow.db` (experimento `vision-coil100-v2`, "
        "artefactos en `<repo>/mlruns`; el experimento histórico `vision-coil100` se conserva).",
        "",
        "## Datos",
        f"- COIL-100: 100 objetos × 72 vistas (paso 5°), 128×128 RGB, fondo oscuro y luz controlada. Hash de datos "
        f"`{META['data_version_hash'][:16]}…`. Semilla 42.",
        "- El artefacto usa las 7.200 vistas (+1 copia aumentada por vista) y **no tiene test propio**.",
        "",
        "## Métricas independientes (selección anidada por protocolo, test evaluado una vez)",
        "",
        "Familia, hiperparámetros, k y umbrales se eligen solo con la validación interna del train de cada protocolo.",
        "",
        "| Protocolo | Modelo elegido | Exactitud | MAE ángulo | Éxito e2e | % ACT | n ACT | Error entre ACT (IC95) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in P:
        c = RS[p]["nested_classic"]
        lines.append(f"| {PNAME[p]} | {c['selected_candidate']} | {pct(c['class_accuracy'], 2)} | {deg(c['angle_mae_deg'])} | "
                     f"{pct(c['end_to_end_success'])} | {pct(c['coverage_act'])} | {c['n_act']} | "
                     f"{pct(c['error_among_act'], 2)} {ci(c['error_among_act_wilson95'])} |")
    lines += [
        "",
        "## Métricas de la configuración desplegada (con salvedad)",
        "",
        "Familia/C de la configuración desplegada se fijaron con validaciones agregadas de A–D que, en conjunto, se solapan "
        "con tests de otros protocolos: estas cifras **no** son independientes.",
        "",
        "| Protocolo | Exactitud | MAE ángulo | % ACT | n ACT | Error entre ACT (IC95) |",
        "|---|---|---|---|---|---|",
    ]
    for p in P:
        c = RS[p]["recipe_classic"]
        lines.append(f"| {PNAME[p]} | {pct(c['class_accuracy'], 2)} | {deg(c['angle_mae_deg'])} | {pct(c['coverage_act'])} | "
                     f"{c['n_act']} | {pct(c['error_among_act'], 2)} {ci(c['error_among_act_wilson95'])} |")
    lines += [
        "",
        f"Desconocidos (selección anidada) — D estricto: rechazados {pct(ds_.get('unknown_rejected'))}, llegan a ACT "
        f"{pct(ds_.get('unknown_reaches_act'), 2)} (IC95 {ci(ds_.get('unknown_reaches_act_wilson95'))}). D variante: AUROC "
        f"combinado {num(dv.get('auroc_combined_rank'), 3)}, conocidos rechazados {pct(dv.get('known_false_reject'), 2)}.",
        "",
        "## Robustez a perturbaciones (configuración desplegada, test de A)",
        "",
        "| Perturbación | Exactitud | MAE ángulo | % ACT | Error entre ACT (IC95) |",
        "|---|---|---|---|---|",
    ]
    for name, r in robA.items():
        e = r["error_among_act"]
        lines.append(f"| {name} | {pct(r['class_accuracy'], 2)} | {deg(r['angle_mae_deg'])} | {pct(r['coverage_act'])} | "
                     f"{pct(e['rate'], 2)} {ci(e['wilson95'])} |")
    lines += [
        "",
        "Aumentación cubre ruido σ≤6 y brillo ×0.8–1.2; σ=8 y ×0.7/×1.3 son extrapolación. Desconocidos bajo perturbación: "
        "ver reporte (D estricto).",
        "",
        "*Éxito e2e* = clase correcta y error angular circular ≤ 10°. *Error entre ACT* = fracción de acciones del robot que "
        "serían empaque equivocado, desconocido aceptado o agarre fallido. N/A = política efectiva sin acciones.",
        "",
        "## Limitaciones conocidas",
        "- Dataset de laboratorio: fondo oscuro uniforme, iluminación fija, cámara fija, un eje de rotación, sin oclusión, "
        "sin desenfoque por movimiento ni cambios de escala. El desempeño en planta **no** está demostrado.",
        "- Perturbaciones fuera del rango de aumentación (ruido fuerte, cambios de brillo > ±20 %) degradan ángulo y "
        "cobertura y elevan el riesgo observado.",
        "- Objetos rotacionalmente simétricos o con vistas opuestas parecidas: ángulo ambiguo → REVIEW.",
        "- Arco ciego (B) y 12 vistas por SKU (C): política efectiva sin ACT.",
        "- Desconocidos casi duplicados de un conocido (obj61 ≈ obj92): la 2ª compuerta de novedad (1.0.2) reduce mucho su "
        "paso a ACT pero no lo elimina; ver tabla de la compuerta en el reporte.",
        "- El score de incertidumbre no es una probabilidad ni una cota.",
        "",
        "## Cuándo NO usarlo",
        "- Con cámaras, fondos o iluminación distintos sin re-captura de referencias y recalibración de umbrales.",
        "- Para rotaciones fuera del plano, objetos parcialmente ocultos o varios objetos en el cuadro.",
        "- Como única barrera de seguridad: el robot debe mantener sus propias verificaciones (peso, código de barras, "
        "sensores de agarre).",
        "- Para SKUs nuevos sin staging (≥ 12 vistas cubriendo ≥ 300°, métricas leave-one-out) y promoción explícita.",
        "",
        "## Recomendaciones para planta",
        "Piloto en sombra (100 % revisión humana) para recalibrar umbrales con imágenes reales; ampliar la aumentación al "
        "rango de iluminación medido; monitorear ACT/REVIEW/REJECT, la distancia de novedad y las razones de REVIEW; "
        "versionar cada re-entrenamiento (hash de datos, SHA del artefacto, snapshot de código, MLflow) sin sobrescribir "
        "versiones publicadas.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- PDF

def build_pdf(out: Path):
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12, alignment=TA_JUSTIFY)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceAfter=6, textColor=colors.HexColor("#1d3557"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#1d3557"))
    st = []

    def para(t, style=body):
        st.append(Paragraph(t, style))
        st.append(Spacer(1, 4))

    def table(rows, widths=None, font=7.5):
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", font), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", font),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef5")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b0b7c3")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
        ]))
        st.append(t)
        st.append(Spacer(1, 6))

    def fig(name, width=17 * cm, caption=None):
        p = FIG / f"{name}.png"
        if not p.exists():
            return
        from PIL import Image as PI

        w, h = PI.open(p).size
        items = [Image(str(p), width=width, height=width * h / w)]
        if caption:
            items.append(Paragraph(caption, small))
        st.append(KeepTogether(items))
        st.append(Spacer(1, 6))

    th = META["thresholds"]
    A, Ds, Dv = RS["A"]["nested_classic"], RS["D_strict"]["nested_classic"], RS["D"]["nested_classic"]
    Ad = RS["A"]["recipe_classic"]
    st.append(Paragraph("Reto 2 — Visión por computador en COIL-100", h1))
    para(f"Identificación de objeto, estimación de ángulo con incertidumbre y recuperación de vistas similares. "
         f"Artefacto <b>{META['version']}</b> · configuración <b>{RECIPE['name']}</b> · hash de datos "
         f"<font face='Courier'>{META['data_version_hash'][:16]}…</font> · SHA modelo "
         f"<font face='Courier'>{META.get('model_sha256', '')[:16]}…</font> · generado {date.today().isoformat()}.", small)

    st.append(Paragraph("1. Resumen ejecutivo", h2))
    para("Solución productiva <b>clásica</b>: preprocesado robusto a ruido (mediana 3×3 + supresión de fondo) → histogramas HSV + "
         "HOG + LBP + Hu → PCA(150) → clasificador; ángulo por recuperación condicionada al objeto con un score de incertidumbre en "
         "grados; regla ACT / REVIEW / REJECT que solo habilita ACT si en validación el riesgo observado es ≤ 2 % con ≥ 50 casos y, "
         "con pseudo-desconocidos, ≤ 5 % de ellos llega a ACT. La tolerancia de ±10° se traduce en <b>éxito extremo a extremo</b> "
         "(clase correcta y error circular ≤ 10°) y en el par de negocio <b>fracción automatizada</b> / <b>error entre ACT</b>.")
    para("<b>Cifras independientes</b> (selección anidada: familia, hiperparámetros, k y umbrales elegidos solo con la validación interna "
         "del train de cada protocolo; test evaluado una vez):", small)
    table(headline_rows("nested_classic"), [3.2 * cm, 2.6 * cm, 1.5 * cm, 1.5 * cm, 1.4 * cm, 1.2 * cm, 1.1 * cm, 1.4 * cm, 2.4 * cm], font=6.8)
    para(f"Lectura: con referencias densas (A) se automatiza {pct(A['coverage_act'])} con error entre ACT observado "
         f"{pct(A['error_among_act'], 2)} (IC95 {ci(A['error_among_act_wilson95'])}). En B (arco ciego) y C (12 vistas) la política "
         f"efectiva es 0 ACT: la validación no aporta evidencia suficiente. En D estricto se rechaza {pct(Ds.get('unknown_rejected'))} "
         f"de las vistas de objetos desconocidos y {pct(Ds.get('unknown_reaches_act'), 2)} llega a ACT (con el modelo elegido por la selección anidada; ver 6b): "
         "el criterio medido con pseudo-desconocidos de validación no se traslada por completo al test. En la variante D el error entre ACT observado ({pct(Dv['error_among_act'], 2)}) supera el objetivo de 2 %. "
         f"La configuración desplegada (logística C=1) obtiene en A {pct(Ad['coverage_act'])} ACT con error {pct(Ad['error_among_act'], 2)}, "
         "pero su familia/C se eligió con validaciones agregadas que se solapan con tests de otros protocolos (sección 5). El artefacto "
         "se entrena con las 7.200 vistas y no tiene test propio.")

    st.append(Paragraph("2. Análisis del dataset", h2))
    para("100 objetos × 72 vistas (paso exacto de 5°), 128×128 RGB, fondo oscuro casi uniforme (mediana del borde ≈ 25/255, con "
         "ruido) e iluminación controlada. Propiedades que condicionan el diseño: (i) la clase es separable en laboratorio, el riesgo "
         "está en el ángulo; (ii) objetos <b>rotacionalmente simétricos</b> (latas, tazas, donas, frascos); (iii) en ~3/4 de los objetos "
         "la vista a 180° se parece más que la de 90°; (iv) familias de empaques casi duplicadas; (v) el fondo no es exactamente negro: "
         "el ruido de sensor en el fondo genera textura espuria si no se suprime.")
    fig("01_sample_grid", 11 * cm, "Figura 1. Vistas de ejemplo (filas: objetos; columnas: ángulo).")
    fig("02_symmetry", 17 * cm, "Figura 2. Izquierda: distancia media entre vistas vs. separación angular (plana = simétrico). "
        "Centro: índice de ambigüedad por objeto. Derecha: razón d(180°)/d(90°) (<1 = vistas opuestas parecidas).")
    sym = RES["symmetry"]["profile_score"]
    top = sorted(sym.items(), key=lambda kv: -kv[1])[:8]
    para("Objetos más ambiguos según el índice de perfil: " + ", ".join(f"{k} ({v:.2f})" for k, v in top) +
         ". Tratamiento: bandera <i>rotational_ambiguity</i> por instancia, por clase (índice LOO) y para clases cuyo ángulo falla en "
         f"> 25 % de su validación (artefacto: {', '.join(META.get('symmetric_classes', []))}) → REVIEW.")
    pairs = RES["confusable_pairs"][:6]
    table([["Objeto A", "Objeto B", "Dist. mínima inter-objeto", "Dist. típica vista adyacente"]] +
          [[r["a"], r["b"], num(r["min_inter_distance"]), num(r["mean_intra_nn_distance"])] for r in pairs],
          [2.5 * cm, 2.5 * cm, 4.5 * cm, 4.5 * cm])
    para("<b>Laboratorio vs. planta.</b> En planta habrá cinta con textura y reflejos, iluminación variable, oclusión, desenfoque por "
         "movimiento, pose y escala variables y empaques nuevos. Mitigación: preprocesado robusto y aumentación (sección 6), captura de "
         "referencias por estación, compuerta de calidad, recalibración con datos reales, piloto en sombra y monitoreo de deriva.")

    st.append(Paragraph("3. Metodología", h2))
    table([["Protocolo", "Train / objeto", "Test", "Validación (solo dentro de train)"],
           ["A Interpolación", "0,10,…,350 (36)", "5,15,…,355 (36)", "fit paso 20°, val resto"],
           ["B Sector ciego", "60", "arco contiguo 60° (12); inicio aleatorio, semilla 42", "otro arco de 60° dentro de train"],
           ["C Escasez", "1 cada 30° (12)", "las otras 60", "fit paso 60°, val resto"],
           ["D estricto", "90 objetos, 72 vistas", "solo las 720 vistas de 10 desconocidos", "10 pseudo-desconocidos (semilla 43)"],
           ["D variante", "90 objetos, paso 10°", "vistas impares de los 90 + 720 desconocidas", "10 pseudo-desconocidos (semilla 43)"]],
          [2.8 * cm, 3.2 * cm, 5.5 * cm, 5.5 * cm])
    para(f"Desconocidos en D: {', '.join(RES['protocols']['D']['classic']['unknown_objects'])}. <b>Selección anidada</b>: 6 candidatos "
         "(logística C∈{0.1,1,10}, SVM RBF C=10, kNN k=1, Random Forest 300); para cada uno, con la validación interna del protocolo, "
         "se eligen k angular, clases de ángulo poco fiable y umbrales; se elige el candidato con automatización habilitada y mayor "
         "cobertura de validación (desempate: exactitud). Preprocesado igual en entrenamiento e inferencia; aumentación solo en vistas "
         "de train; validación con vistas limpias + perturbadas. Riesgos con IC de Wilson 95 %. MLflow (sqlite:///mlflow.db, experimento "
         "vision-coil100) registra cada corrida anidada, desplegada y el entrenamiento final (model.joblib, SHA-256, snapshot del código, "
         "commit y versiones de librerías).")

    st.append(Paragraph("4. Selección anidada por protocolo (cifras independientes)", h2))
    para("Validación interna de cada protocolo (* = elegido). La columna ECE (validación) muestra que la logística está bien calibrada "
         "y que SVM (Platt) y Random Forest no lo están.", small)
    for p in P:
        para(f"<b>{PNAME[p]}</b>", small)
        table(nested_val_rows(p), [4.6 * cm, 2 * cm, 1.6 * cm, 1.8 * cm, 2 * cm, 1 * cm, 1.4 * cm], font=6.5)

    st.append(Paragraph("5. Configuración desplegada, línea base y referencia moderna", h2))
    para("<b>Salvedad:</b> la configuración desplegada (logística C=1) se fijó con una exploración inicial que promedió validaciones de "
         "A–D; como los protocolos cubren juntos todas las imágenes, esa elección tocó imágenes que son test de otros protocolos. Sus "
         "cifras se reportan como referencia, no como estimación independiente. La línea base (1-NN en píxeles crudos 32×32) siempre "
         "actúa, por lo que en D sus decisiones sobre desconocidos cuentan como error entre ACT. ResNet18 usa la misma configuración "
         "sin aumentación.")
    table(comparison_rows(), [1.3 * cm, 4.4 * cm, 1.8 * cm, 1.8 * cm, 1.7 * cm, 1.4 * cm, 1.6 * cm, 3 * cm], font=6.8)
    para("Umbrales de la configuración desplegada por protocolo (elegidos en validación):", small)
    table(thresholds_rows(), [3.2 * cm, 1.1 * cm, 1.4 * cm, 1.8 * cm, 1.5 * cm, 1.5 * cm, 1.8 * cm, 1.8 * cm, 1.2 * cm], font=6.5)
    fig("03_per_protocol", 17.5 * cm, "Figura 3. Resultados por protocolo (barra ausente = N/A, sin ACT).")

    st.append(Paragraph("6. Robustez a perturbaciones", h2))
    para("En la prueba de punta a punta, ruido gaussiano casi imperceptible (σ=1–4) sobre el fondo creaba textura espuria y disparaba "
         "la distancia de novedad (REJECT). Corrección: mediana 3×3 + máscara sobre gris suavizado + fondo a 0, igual en entrenamiento e "
         "inferencia; aumentación de train (ruido σ≤6, brillo ×0.8–1.2, blur leve) y umbrales calibrados con validación perturbada. "
         "Evaluación: el modelo ya ajustado de la configuración desplegada, con sus umbrales, sobre el test perturbado (semillas fijas). "
         "σ=8 y brillo ×0.7/×1.3 están fuera del rango de aumentación.")
    para("<b>Protocolo A (conocidos)</b>", small)
    table(robustness_rows("A"), [2.6 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm, 1.4 * cm, 1.2 * cm, 1.6 * cm, 2.8 * cm], font=7)
    para("<b>D estricto (desconocidos)</b>", small)
    table(robustness_rows("D_strict"), [3 * cm, 3.5 * cm, 3.5 * cm, 3.5 * cm], font=7)
    para("Lectura honesta: dentro del rango aumentado (ruido σ≤4, blur σ=1) la exactitud y la cobertura cambian poco. Fuera del rango "
         "(σ=8, brillo ×0.7/×1.3) el MAE angular crece, la cobertura baja y el riesgo observado entre ACT sube (puede superar el 2 %; ver "
         "IC). La fracción de desconocidos que llega a ACT no es cero y se mantiene en el mismo orden bajo perturbación. En planta, "
         "variaciones de iluminación mayores exigen ampliar la aumentación y recalibrar.")

    st.append(Paragraph("6b. Segunda compuerta de novedad (desconocidos casi duplicados)", h2))
    para("Los desconocidos que llegaban a ACT eran casi duplicados de un conocido (obj61 ≈ obj92). Compuerta adicional: razón = "
         "distancia del query a la clase predicha / espaciado típico de esa clase (mediana de la distancia de cada referencia a la más "
         "cercana del mismo objeto con otro ángulo); umbral = percentil 99 de la razón en las vistas conocidas de validación. Efecto en "
         "la configuración desplegada (test):")
    rows = [["Compuerta 2", "Protocolo", "Umbral razón", "% ACT", "Error|ACT", "Desc.→ACT", "IC95 desc.→ACT", "Conocidos rechazados"]]
    for g in RES.get("novelty_gate_comparison", []):
        thr = g["umbral razón"]
        rows.append(["sí" if g["compuerta 2"] else "no", g["protocolo"], "—" if thr is None or thr > 1e6 else num(thr),
                     pct(g["% ACT"]), pct(g["error|ACT"], 2), pct(g["desconocidos→ACT"], 2), ci(g["IC95 desc.→ACT"]),
                     pct(g["conocidos rechazados"], 2)])
    table(rows, [1.6 * cm, 1.6 * cm, 1.8 * cm, 1.4 * cm, 1.6 * cm, 1.7 * cm, 3 * cm, 2.4 * cm], font=6.8)
    para("Se publica en vision-1.0.2 porque reduce de forma medible los desconocidos que llegan a ACT en D estricto sin cambiar la "
         "cobertura ni el error de A (tabla). No elimina el problema: los casos que quedan son vistas de desconocidos que caen dentro "
         "del espaciado típico de un conocido.")

    st.append(Paragraph("6c. ¿Por qué la selección anidada (SVM) y la desplegada (logística) coinciden en A?", h2))
    na, da = RN["A"], RR["A"]
    para(f"En A ambos aciertan el 100 % de las clases del test. Ángulo, score de incertidumbre, clases ambiguas y distancia de novedad "
         f"salen de la misma recuperación (mismas características, k = {na['k_selected_on_val']}), no del clasificador; las probabilidades "
         f"solo alimentan la compuerta de confianza mínima. El SVM (Platt multiclase) es sub-confiado: ECE {num(na['ece'], 3)} frente a "
         f"{num(da['ece'], 3)} de la logística, con confianza media muy inferior a su exactitud; su validación eligió confianza mínima "
         f"{na['thresholds']['min_class_confidence']}, por lo que esa compuerta casi no actúa, y el corte angular distinto "
         f"({na['thresholds']['max_angle_uncertainty_deg']}° vs {da['thresholds']['max_angle_uncertainty_deg']}°) afecta muy pocos casos. "
         "Si se desplegara el SVM, su 'confidence' no debería leerse como probabilidad.")

    st.append(Paragraph("7. Ángulo", h2))
    para("Métrica: error circular min(|a−b|, 360−|a−b|), con MAE y fracción ≤ 10° sobre predicciones con clase correcta. Alternativas "
         "comparadas en validación: copiar el ángulo del vecino (k=1) o interpolar con 2–3 vecinos; interpolar ayuda con referencias "
         "cercanas (A, D) y no con brechas grandes (B, C).")
    rows = [["Protocolo", "k", "val ≤10°", "val MAE"]]
    for p in P:
        for r in RR[p]["k_table_val"]:
            rows.append([p, r["k"], pct(r["val_within_10"]), num(r["val_mae_deg"])])
    table(rows, [3 * cm, 1.5 * cm, 2.5 * cm, 2.5 * cm], font=6.5)
    fig("04_angle_error_hist", 17 * cm, "Figura 4. Error angular circular (configuración desplegada, escala log); rojo = ±10°.")

    st.append(Paragraph("8. Rechazo y calibración", h2))
    fig("05_reliability_risk", 17.5 * cm, "Figura 5. Confiabilidad (clase), riesgo–cobertura y P(|error| ≤ 10°) vs. score de incertidumbre.")
    fig("06_unknown_roc", 7.5 * cm, "Figura 6. D variante: ROC conocido vs. desconocido (configuración desplegada).")
    para(f"Las curvas riesgo–cobertura bajan al priorizar mayor confianza o menor incertidumbre pero <b>no son estrictamente "
         f"monótonas</b>, y en B no hay tramo con riesgo aceptable. Criterio explícito de desconocidos: ≤ 5 % de pseudo-desconocidos de "
         f"validación (≥ 100 vistas) pueden llegar a ACT. Selección anidada — D variante: AUROC combinado {num(Dv.get('auroc_combined_rank'), 3)}, "
         f"conocidos rechazados {pct(Dv.get('known_false_reject'), 2)}; D estricto (AUROC no aplica): rechazados {pct(Ds.get('unknown_rejected'))} "
         f"(IC95 {ci(Ds.get('unknown_rejected_wilson95'))}), llegan a ACT {pct(Ds.get('unknown_reaches_act'), 2)} "
         f"(IC95 {ci(Ds.get('unknown_reaches_act_wilson95'))}).")

    st.append(Paragraph("9. Criterio de aceptación: ±10° y el umbral de incertidumbre", h2))
    para("Éxito extremo a extremo = clase correcta ∧ error circular ≤ 10°; métrica de negocio = (fracción automatizada, P(clase incorrecta "
         "o desconocido o |error| > 10° | ACT)). El score de incertidumbre está en grados pero es <b>heurístico</b>: un corte de 30° no "
         "significa error ≤ 30°. El umbral se elige como el mayor cuyo riesgo observado en validación es ≤ 2 % con ≥ 50 ACT. En A, una vez "
         "aplicados la ambigüedad rotacional y la confianza, subir el corte por encima de ~10–15° añade pocos ACT y cambia poco el riesgo "
         "observado; en D el riesgo observado crece con el corte. Son observaciones sobre estos datos, no garantías.")
    fig("09_uncertainty_sweep", 17 * cm, "Figura 7. Riesgo entre ACT (IC Wilson) vs. umbral del score: validación (selección) y test (descriptivo).")
    tv = META["threshold_validation"]
    para(f"Artefacto productivo: confianza ≥ {th['min_class_confidence']}, score ≤ {th['max_angle_uncertainty_deg']}°, distancia ≤ "
         f"{th['unknown_distance']:.2f}; en su validación {tv['val_n_act']} ACT con error {pct(tv['val_error_among_act'], 2)} "
         f"(IC95 {ci(tv['val_error_wilson95'])}). Cuando la decisión no es ACT el robot recibe {{\"action\": \"HOLD\", \"reason\": código}}.")

    st.append(Paragraph("10. Análisis de errores", h2))
    for p, rows in RES["error_analysis"].items():
        table([[f"Prot. {p}: objeto", "Fallos e2e", "Vistas test", "Errores de clase", "Confundido con"]] +
              [[r["object"], r["failures"], r["n"], r["class_errors"], r["confused_with"] or "—"] for r in rows[:6]],
              [3.2 * cm, 2.2 * cm, 2.2 * cm, 2.8 * cm, 3 * cm], font=7)
    fig("07_failures_C", 16 * cm, "Figura 8. Ejemplos de fallos en C (arriba: consulta; abajo: referencia de la predicción).")
    fig("07_failures_B", 16 * cm, "Figura 9. Ejemplos de fallos en B.")
    para("Los fallos angulares se concentran en objetos simétricos o con vistas opuestas parecidas y en el arco no visto (B); los errores de "
         "clase, entre objetos de la misma familia visual. La mayoría de estos casos se envía a REVIEW.")

    st.append(Paragraph("11. Costo: latencia y tamaño", h2))
    table(lat_rows(), [8 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm])
    para(f"Artefacto clásico: {num(RES['cost']['classic_artifact_mb'], 1)} MB (incluye referencias aumentadas y el snapshot de código). "
         f"Pesos ResNet18 fp32: {RES['cost']['resnet18_weights_mb']} MB (+ PyTorch). La línea base se mide como media por imagen en lote.")
    fig("08_latency", 12 * cm, "Figura 10. Latencia por imagen.")
    ev = RES.get("modern_reference_evidence_A", {})
    para("<b>Referencia moderna con trato justo.</b> ResNet18 recibe la misma aumentación, la misma validación limpia + perturbada y la "
         "misma regla de riesgo. Resultado: la regla no habilita ACT en ningún protocolo. Evidencia (validación interna de A, misma "
         "configuración):")
    rows = [["Representación", "Exactitud val", "Confianza p5 (aciertos)", "Fallo angular | clase OK", "AUROC score→fallo", "MAE | clase OK"]]
    for k, v in ev.items():
        rows.append([k, pct(v["val_class_accuracy"], 2), num(v["confidence_p5_correct"], 3), pct(v["angle_fail_rate_given_correct_class"]),
                     num(v["auroc_uncertainty_for_angle_fail"], 3), deg(v["angle_mae_given_correct_class"])])
    table(rows, [3 * cm, 2.2 * cm, 3 * cm, 3 * cm, 2.8 * cm, 2.4 * cm], font=7)
    minr = _min_val_risk("A")
    para(f"ResNet18 clasifica igual o mejor y con confianza alta: el problema no es la confianza sino el <b>ángulo</b>. Sus embeddings "
         f"de ImageNet son más invariantes a la rotación, por lo que con brecha de 10° y perturbaciones falla más el ángulo con la clase "
         f"correcta; su riesgo mínimo observado con ≥ 50 ACT en la validación de A es {pct(minr, 2)} (> 2 %). En test limpio su MAE es "
         f"menor, pero la regla se decide en la validación perturbada, que es la condición realista. Con más latencia en CPU (solo el "
         "embedding supera al pipeline clásico completo), ~45 MB de pesos + PyTorch y sin automatización habilitada, la brecha no "
         "justifica el costo: producción = clásico.")

    st.append(PageBreak())
    st.append(Paragraph("12. Model card", h2))
    for line in model_card_md().splitlines()[3:]:
        if not line.strip():
            continue
        if line.startswith("## "):
            st.append(Paragraph(line[3:], ParagraphStyle("h3", parent=h2, fontSize=10)))
        elif line.startswith("|"):
            continue  # las tablas ya están en el cuerpo del reporte
        else:
            t = line.lstrip("- ").replace("**", "")
            t = re.sub(r"`([^`]*)`", r"<font face='Courier'>\1</font>", t)
            t = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", t)
            para(("• " if line.startswith("- ") else "") + t, small)

    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.5 * cm,
                            bottomMargin=1.5 * cm, title="Reto 2 — Visión COIL-100", author="Equipo IA")
    doc.build(st)


if __name__ == "__main__":
    (ROOT / "docs" / "model_card.md").write_text(model_card_md())
    out = REP / "vision_report.pdf"
    build_pdf(out)
    print("escritos:", out, ROOT / "docs" / "model_card.md")
