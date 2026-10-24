"""Genera docs/report/vision_report.pdf y docs/model_card.md a partir de docs/report/results.json,
models/vision-1.0.0/metadata.json y las figuras de docs/report/figures/ (todas producidas por el notebook).

Las cifras de cabecera son las de la RECETA PRODUCTIVA CONGELADA (src/vision/recipe.py) evaluada con test
independiente por protocolo; la exploración de clasificadores se reporta aparte.

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
META = json.loads((ROOT / "models" / "vision-1.0.0" / "metadata.json").read_text())
RS = RES["recipe_summary"]
RR = RES["recipe"]["results"]["classic"]
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

def headline_rows():
    rows = [["Protocolo", "Exactitud", "MAE ángulo", "≤10°", "Éxito e2e", "% ACT", "n ACT", "Error|ACT", "IC95 Wilson"]]
    for p in P:
        c = RS[p]["recipe_classic"]
        rows.append([PNAME[p], pct(c["class_accuracy"], 2), deg(c["angle_mae_deg"]), pct(c["angle_within_10"]),
                     pct(c["end_to_end_success"]), pct(c["coverage_act"]), c["n_act"], pct(c["error_among_act"], 2),
                     ci(c["error_among_act_wilson95"])])
    return rows


def thresholds_rows():
    rows = [["Protocolo", "k (val)", "Conf. mín.", "Incert. máx.", "Dist. desconocido", "Val: n ACT", "Val: error|ACT", "Automatización"]]
    for p in P:
        r = RR[p]
        t, v = r["thresholds"], r["threshold_validation"]
        rows.append([PNAME[p], r["k_selected_on_val"], num(t["min_class_confidence"]),
                     "desactivada" if t["max_angle_uncertainty_deg"] < 0 else f"{t['max_angle_uncertainty_deg']:.1f}°",
                     num(t["unknown_distance"]), v["val_n_act"], pct(v["val_error_among_act"], 2),
                     "sí" if r["automation_enabled"] else "no"])
    return rows


def comparison_rows():
    rows = [["Prot.", "Modelo", "Exactitud", "MAE ángulo", "Éxito e2e", "% ACT", "Error|ACT", "IC95"]]
    for p in P:
        for key, lab in (("recipe_classic", "Receta clásica (prod.)"), ("baseline_raw_1nn", "Base píxeles 1-NN (siempre ACT)"),
                         ("recipe_resnet18", "Receta con ResNet18")):
            m = RS[p].get(key)
            if not m:
                continue
            rows.append([p.replace("_strict", " estr."), lab, pct(m["class_accuracy"], 2), deg(m["angle_mae_deg"]),
                         pct(m["end_to_end_success"]), pct(m["coverage_act"]), pct(m["error_among_act"], 2),
                         ci(m.get("error_among_act_wilson95"))])
    return rows


def exploration_rows(feature="classic"):
    rows = [["Prot.", "Clasificador", "Exactitud", "MAE°", "Éxito e2e", "ECE", "fit (s)"]]
    for p in ["A", "B", "C", "D"]:
        r = RES["protocols"][p][feature]
        for clf, m in r["test"].items():
            star = " *" if clf == r["selected"]["clf"] else ""
            rows.append([p, clf + star, pct(m["class_accuracy"], 2), num(m["angle_mae_deg"]), pct(m["end_to_end_success"]),
                         num(m["ece"], 3), num(m["fit_s"], 1)])
    return rows


def val_rows():
    rows = [["Clasificador / params"] + ["val A", "val B", "val C", "val D"]]
    acc = {}
    for p in ["A", "B", "C", "D"]:
        for k, m in RES["protocols"][p]["classic"]["validation"].items():
            acc.setdefault(k, {})[p] = m["class_accuracy"]
    for k, v in acc.items():
        rows.append([k.replace(":", " ")] + [pct(v.get(p), 2) for p in ["A", "B", "C", "D"]])
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
    lat = RES["cost"]["latency"]
    lc = next((v.get("p50_ms") for k, v in lat.items() if k.startswith("classic")), None)
    ds_, dv = RS["D_strict"]["recipe_classic"], RS["D"]["recipe_classic"]
    lines = [
        f"# Model card — {META['version']}",
        "",
        f"*Generada automáticamente por `scripts/build_vision_report.py` el {date.today().isoformat()} a partir de "
        "`docs/report/results.json` y `models/vision-1.0.0/metadata.json`.*",
        "",
        "## Uso previsto",
        "Identificar el objeto (SKU) de una imagen de estación, estimar su ángulo de rotación en el plano [0, 360) con "
        "un score de incertidumbre en grados, y devolver las vistas de referencia más parecidas como evidencia. La "
        "salida incluye una decisión **ACT / REVIEW / REJECT**: el robot solo recibe un comando `PICK` con ángulo cuando "
        "la decisión es ACT; en cualquier otro caso recibe `HOLD` y el caso va a cola humana. ACT no garantiza acierto: "
        "el riesgo residual medido se reporta abajo.",
        "",
        "## Modelo (receta congelada)",
        f"- Receta `{RECIPE['name']}` (`src/vision/recipe.py`): {RECIPE['features']} → `{RECIPE['clf']}` "
        f"{json.dumps(RECIPE['params'])}. Clasificador elegido en la exploración por exactitud media de **validación**.",
        f"- Ángulo: recuperación condicionada al objeto; k de interpolación elegido en validación (artefacto: k = {META['k_angle_refine']}).",
        "- Score de incertidumbre angular: separación angular equivalente a la distancia al vecino más cercano según la "
        "curva distancia-vs-ángulo del objeto. Es un **score heurístico en grados, no una cota del error**; su umbral se "
        "elige para que P(clase incorrecta o |error| > 10° | ACT) ≤ 2 % en validación.",
        "- Novedad: distancia al vecino más cercano en el espacio PCA.",
        f"- Umbrales del artefacto: confianza ≥ {th['min_class_confidence']}, score de incertidumbre ≤ "
        f"{th['max_angle_uncertainty_deg']}°, distancia de desconocido ≤ {th['unknown_distance']:.2f}; elegidos en validación "
        f"(90 conocidos paso 10° + 10 pseudo-desconocidos): {tv['val_n_act']} ACT, error {pct(tv['val_error_among_act'], 2)} "
        f"(IC95 {ci(tv['val_error_wilson95'])}). Esa validación comparte imágenes con el test del protocolo A, por lo que **no** "
        "es una estimación independiente. Ajustables en caliente vía `PUT /v1/admin/thresholds` (validados: finitos y en rango).",
        f"- Clases marcadas con ambigüedad rotacional (→ REVIEW): {', '.join(META.get('symmetric_classes', [])) or 'ninguna'}.",
        f"- Artefacto: {META.get('artifact_size_mb')} MB, SHA-256 `{META.get('model_sha256', '')[:16]}…`, commit "
        f"`{META.get('git', {}).get('commit', '')[:10]}` (árbol con cambios sin commit: {META.get('git', {}).get('dirty')}); "
        f"latencia p50 ≈ {num(lc, 1)} ms/imagen en CPU.",
        "",
        "## Datos",
        f"- COIL-100 (Columbia): 100 objetos × 72 vistas (paso 5°), 128×128 RGB, fondo oscuro y luz controlada. "
        f"Hash de versión de datos: `{META['data_version_hash'][:16]}…`. Semilla 42.",
        "- El artefacto usa las 7.200 vistas, así que **no tiene test propio**: sus métricas esperadas son las de la "
        "receta, evaluada en cada protocolo con test independiente (tabla siguiente).",
        "",
        "## Métricas de la receta (test independiente por protocolo, una evaluación)",
        "",
        "| Protocolo | Exactitud | MAE ángulo | ≤10° | Éxito e2e | % ACT | n ACT | Error entre ACT (IC95) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in P:
        c = RS[p]["recipe_classic"]
        lines.append(f"| {PNAME[p]} | {pct(c['class_accuracy'], 2)} | {deg(c['angle_mae_deg'])} | {pct(c['angle_within_10'])} | "
                     f"{pct(c['end_to_end_success'])} | {pct(c['coverage_act'])} | {c['n_act']} | "
                     f"{pct(c['error_among_act'], 2)} {ci(c['error_among_act_wilson95'])} |")
    lines += [
        "",
        f"Desconocidos — D estricto (90 conocidos con todas sus vistas; test = 720 vistas de 10 desconocidos): rechazados "
        f"{pct(ds_.get('unknown_rejected'))} (IC95 {ci(ds_.get('unknown_rejected_wilson95'))}), llegan a ACT "
        f"{pct(ds_.get('unknown_reaches_act'), 2)} (IC95 {ci(ds_.get('unknown_reaches_act_wilson95'))}). D variante (mixto): "
        f"AUROC combinado {num(dv.get('auroc_combined_rank'), 3)}, conocidos rechazados {pct(dv.get('known_false_reject'), 2)}.",
        "",
        "*Éxito e2e* = clase correcta y error angular circular ≤ 10° (tolerancia de agarre). *Error entre ACT* = fracción "
        "de acciones del robot que serían empaque equivocado, objeto desconocido aceptado o agarre fallido. N/A = la receta "
        "no autorizó ninguna acción (automatización desactivada por falta de evidencia en validación).",
        "",
        "## Limitaciones conocidas",
        "- Dataset de laboratorio: fondo oscuro uniforme, iluminación fija, cámara fija, un solo eje de rotación, sin "
        "oclusión, sin desenfoque por movimiento, sin variación de escala. El desempeño en planta **no** está demostrado.",
        "- Objetos rotacionalmente simétricos o con vistas opuestas parecidas: el ángulo es intrínsecamente ambiguo; el "
        "sistema los envía a REVIEW.",
        "- Ángulos nunca vistos (protocolo B, arco ciego de 60°) y pocas vistas por SKU (protocolo C, 12 vistas): la "
        "receta desactiva la automatización (todo a REVIEW).",
        "- Desconocidos casi duplicados de un conocido (misma familia de empaque, p. ej. obj61 ≈ obj92) pueden aceptarse "
        "como el conocido y llegar a ACT.",
        "- El score de incertidumbre angular no es una probabilidad ni una cota; solo su umbral está validado empíricamente.",
        "",
        "## Cuándo NO usarlo",
        "- Con cámaras, fondos o iluminación distintos sin re-captura de referencias y recalibración de umbrales.",
        "- Para rotaciones fuera del plano (vuelcos), objetos parcialmente ocultos o varios objetos en el cuadro.",
        "- Como única barrera de seguridad: el robot debe mantener sus propias verificaciones (peso, código de barras, "
        "sensores de agarre).",
        "- Para SKUs nuevos sin pasar por staging (`POST /v1/skus/stage`: ≥ 12 vistas cubriendo ≥ 300°, métricas "
        "leave-one-out) y promoción explícita.",
        "",
        "## Recomendaciones para planta",
        "Piloto en sombra (el robot no actúa; 100 % revisión humana) para recalibrar umbrales con datos reales; "
        "aumentación (brillo, blur, ruido, escala) y captura de referencias por estación; monitoreo de la tasa "
        "ACT/REVIEW/REJECT, de la distancia de novedad y de las razones de REVIEW; re-entrenamiento versionado con "
        "hash de datos, SHA del artefacto y registro en MLflow.",
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
    A, Ds, Dv = RS["A"]["recipe_classic"], RS["D_strict"]["recipe_classic"], RS["D"]["recipe_classic"]
    st.append(Paragraph("Reto 2 — Visión por computador en COIL-100", h1))
    para(f"Identificación de objeto, estimación de ángulo con incertidumbre y recuperación de vistas similares. "
         f"Artefacto <b>{META['version']}</b> · receta <b>{RECIPE['name']}</b> · hash de datos "
         f"<font face='Courier'>{META['data_version_hash'][:16]}…</font> · SHA modelo "
         f"<font face='Courier'>{META.get('model_sha256', '')[:16]}…</font> · generado {date.today().isoformat()}.", small)

    st.append(Paragraph("1. Resumen ejecutivo", h2))
    para(f"La solución productiva es una <b>receta clásica congelada</b>: histogramas HSV + HOG + LBP + momentos de Hu → "
         f"estandarización → PCA(150) → <b>regresión logística C=1</b>; ángulo por recuperación condicionada al objeto con un "
         f"score de incertidumbre en grados; y una regla ACT / REVIEW / REJECT cuyo objetivo es que el robot solo actúe cuando "
         f"el riesgo medido en validación sea ≤ 2 % (con ≥ 50 casos de evidencia). La tolerancia de ±10° se traduce en "
         f"<b>éxito extremo a extremo</b> (clase correcta y error circular ≤ 10°) y en el par de negocio <b>fracción automatizada</b> / "
         f"<b>error entre ACT</b>. Todas las cifras siguientes son de esa receta, ajustada solo con el train de cada protocolo y "
         f"evaluada <b>una vez</b> en su test.")
    table(headline_rows(), [3.6 * cm, 1.6 * cm, 1.6 * cm, 1.2 * cm, 1.5 * cm, 1.3 * cm, 1.1 * cm, 1.5 * cm, 2.6 * cm], font=7)
    para(f"Lectura: con referencias densas (A) la receta automatiza {pct(A['coverage_act'])} con error entre ACT "
         f"{pct(A['error_among_act'], 2)} (IC95 {ci(A['error_among_act_wilson95'])}). En B (arco ciego) y C (12 vistas) la validación "
         f"no aporta evidencia suficiente y la receta desactiva la automatización: todo va a REVIEW. En D estricto se rechaza "
         f"{pct(Ds.get('unknown_rejected'))} de las vistas de objetos desconocidos y {pct(Ds.get('unknown_reaches_act'), 2)} llega a "
         f"ACT (casi duplicados de un conocido); en la variante mixta, AUROC {num(Dv.get('auroc_combined_rank'), 3)}. "
         f"El artefacto entregado es la misma receta ajustada con las 7.200 vistas: no tiene test propio y sus métricas esperadas "
         f"son las de esta tabla.")

    st.append(Paragraph("2. Análisis del dataset", h2))
    para("100 objetos × 72 vistas (paso exacto de 5°), 128×128 RGB, fondo oscuro casi uniforme (mediana del borde ≈ 25/255) "
         "e iluminación controlada. Propiedades que condicionan el diseño: (i) la clase es separable en laboratorio, el riesgo "
         "está en el ángulo; (ii) objetos <b>rotacionalmente simétricos</b> (latas, tazas, donas, frascos) cuyo ángulo es "
         "intrínsecamente ambiguo; (iii) en ~3/4 de los objetos la vista a 180° se parece más que la de 90°; (iv) familias de "
         "empaques casi duplicadas.")
    fig("01_sample_grid", 11 * cm, "Figura 1. Vistas de ejemplo (filas: objetos; columnas: ángulo).")
    fig("02_symmetry", 17 * cm, "Figura 2. Izquierda: distancia media entre vistas vs. separación angular (plana = simétrico). "
        "Centro: índice de ambigüedad por objeto. Derecha: razón d(180°)/d(90°) (<1 = vistas opuestas parecidas).")
    sym = RES["symmetry"]["profile_score"]
    top = sorted(sym.items(), key=lambda kv: -kv[1])[:8]
    para("Objetos más ambiguos según el índice de perfil: " + ", ".join(f"{k} ({v:.2f})" for k, v in top) +
         ". Tratamiento: bandera <i>rotational_ambiguity</i> por instancia (vista de referencia lejana casi tan parecida como la mejor), "
         "por clase con el índice LOO y con las clases cuyo ángulo falla en > 25 % de su validación "
         f"(artefacto: {', '.join(META.get('symmetric_classes', []))}) → REVIEW.")
    pairs = RES["confusable_pairs"][:6]
    table([["Objeto A", "Objeto B", "Dist. mínima inter-objeto", "Dist. típica vista adyacente"]] +
          [[r["a"], r["b"], num(r["min_inter_distance"]), num(r["mean_intra_nn_distance"])] for r in pairs],
          [2.5 * cm, 2.5 * cm, 4.5 * cm, 4.5 * cm])
    para("<b>Laboratorio vs. planta.</b> En planta habrá cinta con textura y reflejos, iluminación variable, oclusión, desenfoque por "
         "movimiento, pose y escala de cámara variables y empaques nuevos. Mitigación: aumentación (brillo, contraste, blur, ruido, "
         "escala, recortes) y captura de referencias por estación; compuerta de calidad determinista previa al modelo; recalibración "
         "de umbrales con datos reales; piloto en sombra con revisión humana antes de habilitar ACT; monitoreo de deriva.")

    st.append(Paragraph("3. Metodología", h2))
    table([["Protocolo", "Train / objeto", "Test", "Validación (solo dentro de train)"],
           ["A Interpolación", "0,10,…,350 (36)", "5,15,…,355 (36)", "fit paso 20°, val resto"],
           ["B Sector ciego", "60", "arco contiguo 60° (12); inicio aleatorio, semilla 42", "otro arco de 60° dentro de train"],
           ["C Escasez", "1 cada 30° (12)", "las otras 60", "fit paso 60°, val resto"],
           ["D estricto", "90 objetos, 72 vistas", "solo las 720 vistas de 10 desconocidos", "10 pseudo-desconocidos (semilla 43)"],
           ["D variante", "90 objetos, paso 10°", "vistas impares de los 90 + 720 desconocidas", "10 pseudo-desconocidos (semilla 43)"]],
          [2.8 * cm, 3.2 * cm, 5.5 * cm, 5.5 * cm])
    para(f"Desconocidos en D: {', '.join(RES['protocols']['D']['classic']['unknown_objects'])}. Dos niveles: (1) <b>exploración</b> — "
         "cuatro clasificadores (kNN, logística, SVM RBF con Platt, Random Forest) con rejilla pequeña, comparados en validación; su "
         "test se reporta a título informativo y no se usa para elegir; (2) <b>receta congelada</b> — el ganador de la exploración más "
         "el procedimiento de umbrales, definido una vez y evaluado una vez por protocolo. El k de interpolación angular se elige en "
         "validación. Umbrales: máxima cobertura con error entre ACT ≤ 2 % y ≥ 50 ACT en validación; distancia de desconocido por "
         "Youden con pseudo-desconocidos; si no hay evidencia suficiente, automatización desactivada. Riesgos con IC de Wilson 95 %. "
         "Todo se registra en MLflow (sqlite:///mlflow.db, experimento vision-coil100): parámetros, métricas por protocolo, hash de "
         "datos, y para el artefacto model.joblib, su SHA-256, commit git y versiones de librerías.")

    st.append(Paragraph("4. Exploración: comparación de clasificadores", h2))
    para("Exactitud de clase en <b>validación</b> (la única usada para elegir):", small)
    table(val_rows(), [5 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm], font=7)
    para("La regresión logística tiene la mejor exactitud media de validación (≈3 puntos sobre SVM RBF y ≈5 sobre kNN y Random "
         "Forest) y es la mejor calibrada (ECE bajo), por lo que se congela como receta. Test de cada clasificador en la exploración "
         "(* = elegido en validación para ese protocolo; informativo):", small)
    table(exploration_rows("classic"), [1 * cm, 3 * cm, 2 * cm, 1.6 * cm, 2 * cm, 1.6 * cm, 1.6 * cm], font=7)

    st.append(Paragraph("5. Receta congelada: resultados, línea base y referencia moderna", h2))
    para("Umbrales y k de la receta clásica en cada protocolo (todos elegidos en validación dentro del train del protocolo):", small)
    table(thresholds_rows(), [3.4 * cm, 1.2 * cm, 1.6 * cm, 1.9 * cm, 2.2 * cm, 1.7 * cm, 2 * cm, 2 * cm], font=7)
    para("Comparación obligatoria con la misma contabilidad en todos los modelos: la línea base (1-NN en píxeles crudos 32×32) "
         "siempre actúa, por lo que en D sus aciertos sobre desconocidos son imposibles y cuentan como error entre ACT.")
    table(comparison_rows(), [1.3 * cm, 4.4 * cm, 1.8 * cm, 1.8 * cm, 1.7 * cm, 1.4 * cm, 1.6 * cm, 3 * cm], font=7)
    fig("03_per_protocol", 17.5 * cm, "Figura 3. Receta congelada por protocolo (barra ausente = N/A, sin ACT).")

    st.append(Paragraph("6. Ángulo", h2))
    para("Métrica: error circular min(|a−b|, 360−|a−b|), con MAE, mediana y fracción ≤ 10° sobre las predicciones con clase "
         "correcta. Alternativas: copiar el ángulo del vecino más cercano (k=1) o interpolar con 2–3 vecinos. Según la validación, "
         "interpolar ayuda cuando el query cae entre referencias cercanas (A, D) pero no con brechas grandes (B, C), donde k=1 "
         "queda igual o mejor; por eso k se elige por protocolo en validación.")
    rows = [["Protocolo", "k", "val ≤10°", "val MAE"]]
    for p in P:
        for r in RR[p]["k_table_val"]:
            rows.append([p, r["k"], pct(r["val_within_10"]), num(r["val_mae_deg"])])
    table(rows, [3 * cm, 1.5 * cm, 2.5 * cm, 2.5 * cm], font=7)
    fig("04_angle_error_hist", 17 * cm, "Figura 4. Histograma del error angular circular, receta clásica (escala log); rojo = ±10°.")

    st.append(Paragraph("7. Rechazo y calibración: ¿es la confianza una base válida para decidir?", h2))
    fig("05_reliability_risk", 17.5 * cm, "Figura 5. Confiabilidad (clase), riesgo–cobertura y P(|error| ≤ 10°) vs. score de incertidumbre.")
    fig("06_unknown_roc", 7.5 * cm, "Figura 6. D variante: ROC conocido vs. desconocido.")
    para(f"La regresión logística está bien calibrada (ECE {num(A.get('ece'), 3)} en A); SVM (Platt) y Random Forest tienen ECE alto "
         f"en la exploración y sus probabilidades no deben usarse crudas. En las curvas riesgo–cobertura, priorizar los casos de mayor "
         f"confianza o menor incertidumbre deja el riesgo de los primeros tramos muy por debajo del global; las curvas <b>no son "
         f"estrictamente monótonas</b> y en B no existe un tramo con riesgo aceptable. Desconocidos: D variante AUROC distancia "
         f"{num(Dv.get('auroc_nn_distance'), 3)}, confianza {num(Dv.get('auroc_neg_confidence'), 3)}, combinado "
         f"{num(Dv.get('auroc_combined_rank'), 3)}; conocidos rechazados {pct(Dv.get('known_false_reject'), 2)}. D estricto (sin conocidos "
         f"en test, AUROC no aplica): desconocidos rechazados {pct(Ds.get('unknown_rejected'))} (IC95 {ci(Ds.get('unknown_rejected_wilson95'))}), "
         f"desconocidos que llegan a ACT {pct(Ds.get('unknown_reaches_act'), 2)} (IC95 {ci(Ds.get('unknown_reaches_act_wilson95'))}).")

    st.append(Paragraph("8. Criterio de aceptación: ±10° y el umbral de incertidumbre", h2))
    para("Éxito extremo a extremo = clase correcta ∧ error circular ≤ 10°. Como el robot solo actúa en ACT, la métrica de negocio es "
         "(fracción automatizada, P(clase incorrecta o desconocido o |error| > 10° | ACT)). El score de incertidumbre está en grados pero "
         "es <b>heurístico</b>: un corte de 30° no significa error ≤ 30°. Su umbral se elige directamente por el riesgo de negocio: el mayor "
         "umbral cuyo riesgo en validación sea ≤ 2 % con ≥ 50 ACT. Los cortes altos son seguros porque la ambigüedad rotacional y la "
         "confianza ya filtran los casos peligrosos; la figura muestra el riesgo con IC de Wilson en validación (selección) y en el test "
         "de la receta (solo descriptivo).")
    fig("09_uncertainty_sweep", 17 * cm, "Figura 7. Riesgo entre ACT vs. umbral del score de incertidumbre (A y D variante).")
    tv = META["threshold_validation"]
    para(f"Artefacto productivo: confianza ≥ {th['min_class_confidence']}, score ≤ {th['max_angle_uncertainty_deg']}°, distancia ≤ "
         f"{th['unknown_distance']:.2f}; en su validación {tv['val_n_act']} ACT con error {pct(tv['val_error_among_act'], 2)} "
         f"(IC95 {ci(tv['val_error_wilson95'])}). Esa validación no es independiente (comparte imágenes con el test de A); la evidencia "
         "independiente es la de la tabla 1. Cuando la decisión no es ACT el robot recibe {\"action\": \"HOLD\", \"reason\": código}.")

    st.append(Paragraph("9. Análisis de errores (receta clásica)", h2))
    for p, rows in RES["error_analysis"].items():
        table([[f"Prot. {p}: objeto", "Fallos e2e", "Vistas test", "Errores de clase", "Confundido con"]] +
              [[r["object"], r["failures"], r["n"], r["class_errors"], r["confused_with"] or "—"] for r in rows[:6]],
              [3.2 * cm, 2.2 * cm, 2.2 * cm, 2.8 * cm, 3 * cm], font=7)
    fig("07_failures_C", 16 * cm, "Figura 8. Ejemplos de fallos en C (arriba: consulta; abajo: referencia de la predicción).")
    fig("07_failures_B", 16 * cm, "Figura 9. Ejemplos de fallos en B.")
    para("Los fallos angulares se concentran en objetos simétricos o con vistas opuestas parecidas (errores de ~180° o arbitrarios) y en "
         "el arco no visto (B); los errores de clase, entre objetos de la misma familia visual. La mayoría de estos casos se envía a REVIEW.")

    st.append(Paragraph("10. Costo: latencia y tamaño", h2))
    table(lat_rows(), [8 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm])
    para(f"Artefacto clásico: {num(RES['cost']['classic_artifact_mb'], 1)} MB. Pesos ResNet18 fp32: {RES['cost']['resnet18_weights_mb']} MB "
         "(más la dependencia de PyTorch). La línea base se mide como media por imagen de una búsqueda 1-NN en lote (sin percentiles).")
    fig("08_latency", 12 * cm, "Figura 10. Latencia por imagen.")
    rb, rc = RS["B"], RS["C"]
    para(f"¿Justifica la brecha el costo? Con la misma receta, ResNet18 mejora la exactitud de clase en B ({pct(rb['recipe_resnet18']['class_accuracy'], 2)} "
         f"vs {pct(rb['recipe_classic']['class_accuracy'], 2)}) y C ({pct(rc['recipe_resnet18']['class_accuracy'], 2)} vs "
         f"{pct(rc['recipe_classic']['class_accuracy'], 2)}) y el MAE angular, pero tampoco habilita automatizar B ni C. En A: ACT "
         f"{pct(RS['A']['recipe_resnet18']['coverage_act'])} con error {pct(RS['A']['recipe_resnet18']['error_among_act'], 2)} (ResNet18) vs "
         f"{pct(A['coverage_act'])} con {pct(A['error_among_act'], 2)} (clásica): con la misma receta, ResNet18 no alcanza en su "
         f"validación un riesgo ≤ 2 % con ≥ 50 ACT (mínimo con ≥ 50 ACT en A: {pct(_min_val_risk('A'), 2)}), así que su automatización "
         "queda desactivada pese a su mayor exactitud bruta — su score de incertidumbre (misma definición, otro espacio) separa peor "
         "los fallos angulares. Solo el embedding ResNet18 ya cuesta más en CPU que el pipeline "
         "clásico completo y añade ~45 MB de pesos más PyTorch. Producción = receta clásica; ResNet18 queda como referencia y candidata si el "
         "piloto en planta muestra una brecha mayor.")

    st.append(PageBreak())
    st.append(Paragraph("11. Model card", h2))
    for line in model_card_md().splitlines()[3:]:
        if not line.strip():
            continue
        if line.startswith("## "):
            st.append(Paragraph(line[3:], ParagraphStyle("h3", parent=h2, fontSize=10)))
        elif line.startswith("|"):
            continue  # la tabla de métricas ya está en la sección 1
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
