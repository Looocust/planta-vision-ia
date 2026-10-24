"""Genera docs/report/vision_report.pdf y docs/model_card.md a partir de docs/report/results.json,
models/vision-1.0.0/metadata.json y las figuras de docs/report/figures/ (todas producidas por el notebook).

Uso: uv run python scripts/build_vision_report.py
"""
from __future__ import annotations

import json
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
S = RES["summary"]
P = ["A", "B", "C", "D"]
PNAME = {"A": "A · Interpolación", "B": "B · Sector ciego 60°", "C": "C · Escasez (12 vistas)", "D": "D · Objeto desconocido"}


def pct(x, d=1):
    return "—" if x is None else f"{100 * x:.{d}f}%"


def num(x, d=2):
    return "—" if x is None else f"{x:.{d}f}"


# ---------------------------------------------------------------- textos compartidos (PDF y model card)

def headline_rows():
    rows = [["Protocolo", "Exactitud", "MAE ángulo", "≤10°", "Éxito e2e", "% ACT", "Error | ACT", "AUROC desc."]]
    for p in P:
        c = S[p]["classic"]
        rows.append([PNAME[p], pct(c["class_accuracy"], 2), f"{c['angle_mae_deg']:.2f}°", pct(c["angle_within_10"]),
                     pct(c["end_to_end_success"]), pct(c["coverage_act"]), pct(c["error_among_act"], 2),
                     num(c.get("auroc_combined_rank"), 3) if p == "D" else "—"])
    return rows


def comparison_rows():
    rows = [["Prot.", "Modelo", "Exactitud", "MAE ángulo", "Éxito e2e", "% ACT", "Error | ACT"]]
    for p in P:
        for key, lab in (("classic", "Clásico (prod.)"), ("baseline_raw_1nn", "Base: píxeles 1-NN"), ("resnet18", "ResNet18 congelada")):
            m = S[p].get(key)
            if not m:
                continue
            rows.append([p, lab, pct(m["class_accuracy"], 2), f"{m['angle_mae_deg']:.2f}°", pct(m["end_to_end_success"]),
                         pct(m.get("coverage_act", 1.0)), pct(m.get("error_among_act"), 2)])
    return rows


def all_classifier_rows(feature="classic"):
    rows = [["Prot.", "Clasificador", "Exactitud", "MAE°", "Éxito e2e", "% ACT", "Error|ACT", "ECE", "fit (s)"]]
    for p in P:
        r = RES["protocols"][p][feature]
        for clf, m in r["test"].items():
            star = " *" if clf == r["selected"]["clf"] else ""
            rows.append([p, clf + star, pct(m["class_accuracy"], 2), num(m["angle_mae_deg"]), pct(m["end_to_end_success"]),
                         pct(m["coverage_act"]), pct(m["error_among_act"], 2), num(m["ece"], 3), num(m["fit_s"], 1)])
    return rows


def lat_rows():
    lat = RES["cost"]["latency"]
    rows = [["Pipeline", "p50 (ms)", "p95 (ms)"]]
    for k, v in lat.items():
        rows.append([k, num(v.get("p50_ms"), 2), num(v.get("p95_ms"), 2) if "p95_ms" in v else "—"])
    return rows


def model_card_md() -> str:
    th = META["thresholds"]
    d = S["D"]["classic"]
    lat = RES["cost"]["latency"]
    classic_lat = lat.get("classic (pipeline completo)", {}).get("p50_ms")
    lines = [
        f"# Model card — {META['version']}",
        "",
        f"*Generada automáticamente por `scripts/build_vision_report.py` el {date.today().isoformat()} a partir de "
        "`docs/report/results.json` y `models/vision-1.0.0/metadata.json`.*",
        "",
        "## Uso previsto",
        "Identificar el objeto (SKU) de una imagen de estación, estimar su ángulo de rotación en el plano [0, 360) con "
        "una medida de incertidumbre en grados, y devolver las vistas de referencia más parecidas como evidencia. La "
        "salida incluye una decisión **ACT / REVIEW / REJECT**: el robot solo recibe un comando `PICK` con ángulo cuando "
        "la decisión es ACT; en cualquier otro caso recibe `HOLD` y el caso va a cola humana.",
        "",
        "## Modelo",
        f"- Familia: {META['model_family']} (ML clásico; sin redes profundas en producción).",
        f"- Clasificador: `{META['classifier']['name']}` {json.dumps(META['classifier']['params'])}, elegido por exactitud "
        "media de **validación** en los 4 protocolos (nunca con test).",
        "- Ángulo: recuperación condicionada al objeto (vecino más cercano + media circular ponderada de 2 vecinos); "
        "incertidumbre = separación angular equivalente a la distancia al vecino más cercano según la curva "
        "distancia-vs-ángulo del propio objeto.",
        "- Novedad: distancia al vecino más cercano en el espacio PCA.",
        f"- Umbrales vigentes: confianza mínima {th['min_class_confidence']}, incertidumbre angular máxima "
        f"{th['max_angle_uncertainty_deg']}°, distancia de desconocido {th['unknown_distance']:.2f} "
        "(elegidos en validación con 10 objetos pseudo-desconocidos; error objetivo entre ACT ≤ 2 %). Ajustables en caliente vía "
        "`PUT /v1/admin/thresholds`.",
        f"- Clases marcadas como rotacionalmente simétricas (→ REVIEW): {', '.join(META.get('symmetric_classes', [])) or 'ninguna'}.",
        f"- Tamaño del artefacto: {META.get('artifact_size_mb')} MB; latencia p50 ≈ {num(classic_lat, 1)} ms/imagen en CPU.",
        "",
        "## Datos",
        f"- COIL-100 (Columbia): 100 objetos × 72 vistas (paso 5°), 128×128 RGB, fondo oscuro y luz controlada. "
        f"Hash de versión de datos: `{META['data_version_hash'][:16]}…`. Semilla 42.",
        "- El modelo productivo se entrena con las 7.200 vistas; las métricas provienen de los protocolos A–D (abajo).",
        "",
        "## Métricas (test, una sola evaluación por protocolo)",
        "",
        "| Protocolo | Exactitud | MAE ángulo | ≤10° | Éxito e2e | % ACT | Error entre ACT |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in P:
        c = S[p]["classic"]
        lines.append(f"| {PNAME[p]} | {pct(c['class_accuracy'], 2)} | {c['angle_mae_deg']:.2f}° | {pct(c['angle_within_10'])} | "
                     f"{pct(c['end_to_end_success'])} | {pct(c['coverage_act'])} | {pct(c['error_among_act'], 2)} |")
    lines += [
        "",
        f"Desconocidos (D): AUROC distancia {num(d.get('auroc_nn_distance'), 3)}, confianza {num(d.get('auroc_neg_confidence'), 3)}, "
        f"combinado {num(d.get('auroc_combined_rank'), 3)}; desconocidos rechazados {pct(d.get('unknown_rejected'))}, "
        f"desconocidos que llegan a ACT {pct(d.get('unknown_false_accept_act'), 2)}, conocidos rechazados {pct(d.get('known_false_reject'), 2)}.",
        "",
        "*Éxito e2e* = clase correcta y error angular circular ≤ 10° (tolerancia de agarre). *Error entre ACT* = fracción "
        "de acciones del robot que serían empaque equivocado o agarre fallido.",
        "",
        "## Limitaciones conocidas",
        "- Dataset de laboratorio: fondo oscuro uniforme, iluminación fija, cámara fija, un solo eje de rotación, sin "
        "oclusión, sin desenfoque por movimiento, sin variación de escala. El desempeño en planta **no** está demostrado.",
        "- Objetos rotacionalmente simétricos o con vistas opuestas parecidas: el ángulo es intrínsecamente ambiguo; el "
        "sistema los envía a REVIEW.",
        "- Ángulos nunca vistos (protocolo B, arco ciego de 60°): la recuperación no puede extrapolar; casi todo va a "
        "REVIEW y la automatización se desactiva si la validación no alcanza el objetivo de error.",
        "- Pocas vistas por SKU (protocolo C, 12 vistas): la clase es fiable pero el ángulo no alcanza la confiabilidad "
        "exigida para ACT.",
        "- La novedad se mide por distancia en el espacio de características: un objeto desconocido muy parecido a uno "
        "conocido (misma familia de empaque) puede aceptarse como conocido.",
        "",
        "## Cuándo NO usarlo",
        "- Con cámaras, fondos o iluminación distintos sin re-captura de referencias y recalibración de umbrales.",
        "- Para rotaciones fuera del plano (vuelcos), objetos parcialmente ocultos o varios objetos en el cuadro.",
        "- Como única barrera de seguridad: la decisión ACT reduce pero no elimina el error; el robot debe mantener sus "
        "propias verificaciones (peso, código de barras, sensores de agarre).",
        "- Para SKUs nuevos sin pasar por staging (`POST /v1/skus/stage`: ≥ 12 vistas cubriendo ≥ 300°, métricas "
        "leave-one-out) y promoción explícita.",
        "",
        "## Recomendaciones para planta",
        "Piloto en sombra (el robot no actúa; 100 % revisión humana) para recalibrar umbrales con datos reales; "
        "aumentación (brillo, blur, ruido, escala) y captura de referencias por estación; monitoreo de la tasa "
        "ACT/REVIEW/REJECT, de la distancia de novedad y de las razones de REVIEW; re-entrenamiento versionado con "
        "hash de datos y registro en MLflow.",
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
    d = S["D"]["classic"]
    st.append(Paragraph("Reto 2 — Visión por computador en COIL-100", h1))
    para(f"Identificación de objeto, estimación de ángulo con incertidumbre y recuperación de vistas similares. "
         f"Modelo productivo <b>{META['version']}</b> · hash de datos <font face='Courier'>{META['data_version_hash'][:16]}…</font> · "
         f"generado {date.today().isoformat()}.", small)

    st.append(Paragraph("1. Resumen ejecutivo", h2))
    para(f"El modelo productivo es <b>clásico</b>: histogramas HSV + HOG + LBP + momentos de Hu → estandarización → PCA(150) → "
         f"<b>{META['classifier']['name']}</b> {json.dumps(META['classifier']['params'])}, elegido por exactitud media de validación "
         f"en los cuatro protocolos. El ángulo se estima por recuperación condicionada al objeto, con una incertidumbre expresada "
         f"en grados, y una regla de decisión ACT / REVIEW / REJECT garantiza que el robot solo reciba ángulos confiables. "
         f"La tolerancia de agarre de ±10° se traduce en la métrica de negocio <b>éxito extremo a extremo</b> (clase correcta y "
         f"error circular ≤ 10°), reportada junto a la <b>fracción automatizada</b> y el <b>error entre las decisiones ACT</b>.")
    table(headline_rows(), [3.4 * cm, 1.7 * cm, 1.8 * cm, 1.4 * cm, 1.7 * cm, 1.4 * cm, 1.8 * cm, 1.8 * cm])
    para(f"Lectura: en el régimen operativo (A: referencias densas; D: con objetos desconocidos mezclados) el sistema "
         f"automatiza {pct(S['A']['classic']['coverage_act'])} / {pct(S['D']['classic']['coverage_act'])} de los casos con error entre "
         f"ACT de {pct(S['A']['classic']['error_among_act'], 2)} / {pct(S['D']['classic']['error_among_act'], 2)}. En B (arco ciego) y C (12 vistas) "
         f"la apariencia no basta para un ángulo confiable y el sistema lo detecta: la mayoría de casos va a REVIEW en lugar de "
         f"generar agarres fallidos. Los desconocidos se separan con AUROC {num(d.get('auroc_combined_rank'), 3)}; solo "
         f"{pct(d.get('unknown_false_accept_act'), 2)} de las vistas de objetos desconocidos llega a ACT.")

    st.append(Paragraph("2. Análisis del dataset", h2))
    para("100 objetos × 72 vistas (paso exacto de 5°), 128×128 RGB, fondo oscuro casi uniforme (mediana del borde ≈ 25/255) "
         "e iluminación controlada. Propiedades que condicionan el diseño: (i) la clase es separable en laboratorio, el riesgo "
         "está en el ángulo; (ii) objetos <b>rotacionalmente simétricos</b> (latas, tazas, donas, frascos) cuyo ángulo es "
         "intrínsecamente ambiguo; (iii) objetos con vistas a 180° parecidas; (iv) familias de empaques casi duplicadas.")
    fig("01_sample_grid", 11 * cm, "Figura 1. Vistas de ejemplo (filas: objetos; columnas: ángulo).")
    fig("02_symmetry", 17 * cm, "Figura 2. Izquierda: distancia media entre vistas vs. separación angular (plana = simétrico). "
        "Centro: índice de ambigüedad por objeto. Derecha: razón d(180°)/d(90°) (<1 = vistas opuestas parecidas).")
    sym = RES["symmetry"]["profile_score"]
    top = sorted(sym.items(), key=lambda kv: -kv[1])[:8]
    para("Objetos más ambiguos según el índice de perfil: " + ", ".join(f"{k} ({v:.2f})" for k, v in top) +
         ". Tratamiento: bandera <i>rotational_ambiguity</i> por instancia (vista de referencia lejana casi tan parecida como la mejor) "
         f"y por clase en el modelo productivo (clases: {', '.join(META.get('symmetric_classes', []))}) → REVIEW.")
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
           ["D Desconocido", "90 objetos, paso 10°", "vistas impares de los 90 + todas las de 10 desconocidos",
            "10 pseudo-desconocidos (semilla 43)"]],
          [3 * cm, 3.2 * cm, 5.3 * cm, 5.5 * cm])
    para(f"Objetos desconocidos en D: {', '.join(RES['protocols']['D']['classic']['unknown_objects'])}. "
         "Se comparan cuatro clasificadores (kNN, regresión logística, SVM RBF con Platt, Random Forest) con una rejilla pequeña; el "
         "mejor hiperparámetro por clasificador y el mejor clasificador se eligen en validación y luego se evalúa <b>una sola vez</b> en test. "
         "Los umbrales (confianza mínima, incertidumbre angular máxima, distancia de desconocido) también se fijan en validación: "
         "máxima cobertura con error entre ACT ≤ 2 %; distancia de desconocido por Youden con pseudo-desconocidos; si ningún umbral "
         "cumple el objetivo, la automatización se desactiva. En C la validación no tiene vistas a ≤15° de las de ajuste, por lo que el "
         "umbral angular usa el prior 5° (mitad de la tolerancia). Todo queda en MLflow (sqlite:///mlflow.db, experimento vision-coil100) "
         "con parámetros, métricas por protocolo, hash de datos y artefactos.")

    st.append(Paragraph("4. Resultados por protocolo", h2))
    para("Comparación obligatoria: modelo clásico seleccionado vs. línea base (1-NN en píxeles crudos 32×32, sin rechazo) vs. "
         "referencia moderna (ResNet18 ImageNet congelada + mismo pipeline).")
    table(comparison_rows(), [1.2 * cm, 3.8 * cm, 2 * cm, 2.2 * cm, 2 * cm, 1.8 * cm, 2 * cm])
    fig("03_per_protocol", 17.5 * cm, "Figura 3. Métricas por protocolo y modelo.")
    para("Todos los clasificadores clásicos (test; * = elegido en validación para ese protocolo):", small)
    table(all_classifier_rows("classic"), [1 * cm, 2.8 * cm, 1.8 * cm, 1.4 * cm, 1.8 * cm, 1.5 * cm, 1.7 * cm, 1.3 * cm, 1.3 * cm], font=7)
    para("Referencia moderna ResNet18 (test):", small)
    table(all_classifier_rows("resnet18"), [1 * cm, 2.8 * cm, 1.8 * cm, 1.4 * cm, 1.8 * cm, 1.5 * cm, 1.7 * cm, 1.3 * cm, 1.3 * cm], font=7)

    st.append(Paragraph("5. Ángulo", h2))
    para("Métrica: error circular min(|a−b|, 360−|a−b|). Se reporta MAE, mediana y fracción ≤ 10° sobre las predicciones con clase "
         "correcta. Alternativas comparadas: ángulo del vecino más cercano (sin interpolar) vs. interpolación con 2 y 3 vecinos.")
    alt = RES["angle_alternatives"]
    table([["Protocolo", "Vecinos", "MAE (°)", "Mediana (°)", "≤10°"]] +
          [[a["protocolo"], a["vecinos interpolados"], num(a["MAE°"]), num(a["mediana°"]), pct(a["≤10°"])] for a in alt],
          [2.5 * cm, 2 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm])
    fig("04_angle_error_hist", 17 * cm, "Figura 4. Histograma del error angular circular (escala log); línea roja = ±10°.")

    st.append(Paragraph("6. Rechazo y calibración: ¿es la confianza una base válida para decidir?", h2))
    fig("05_reliability_risk", 17.5 * cm, "Figura 5. Diagrama de confiabilidad (clase), curvas riesgo–cobertura y P(|error| ≤ 10°) vs. incertidumbre angular.")
    fig("06_unknown_roc", 7.5 * cm, "Figura 6. Protocolo D: ROC conocido vs. desconocido.")
    para(f"La regresión logística está bien calibrada (ECE bajo en los cuatro protocolos), mientras que SVM (Platt) y Random Forest "
         f"tienen ECE alto: sus probabilidades no deben usarse crudas para decidir. Las curvas riesgo–cobertura bajan monótonamente al "
         f"exigir más confianza / menos incertidumbre, por lo que existe un umbral operable. En D: AUROC distancia "
         f"{num(d.get('auroc_nn_distance'), 3)}, confianza {num(d.get('auroc_neg_confidence'), 3)}, combinado {num(d.get('auroc_combined_rank'), 3)}; "
         f"con el umbral de validación se rechaza {pct(d.get('unknown_rejected'))} de las vistas desconocidas (TPR), "
         f"{pct(d.get('unknown_not_act'))} no llega a ACT, y solo {pct(d.get('known_false_reject'), 2)} de las vistas conocidas se rechaza.")

    st.append(Paragraph("7. Criterio de aceptación (±10° → métrica de negocio)", h2))
    para("Éxito extremo a extremo = clase correcta ∧ error circular ≤ 10°. Como el robot solo actúa en ACT, la métrica de negocio es el "
         "par (fracción automatizada, error entre ACT). El umbral de incertidumbre angular está en grados y se elige para que el error "
         "entre ACT ≤ 2 % en validación.")
    acc = RES["acceptance_table"]
    table([["Prot.", "Modelo", "Éxito e2e", "% ACT", "Error|ACT", "Umbral incert.", "Automatización"]] +
          [[a["protocolo"], a["modelo"], pct(a["éxito e2e (sin rechazo)"]), pct(a["% ACT"]), pct(a["error|ACT"], 2),
            (f"{a['umbral incert.°']}°" if a.get("umbral incert.°") not in (None, float("nan")) and a.get("umbral incert.°") == a.get("umbral incert.°") else "—"),
            {True: "sí", False: "no"}.get(a.get("automatización habilitada"), "—")] for a in acc],
          [1.2 * cm, 3.6 * cm, 2 * cm, 1.8 * cm, 2 * cm, 2.4 * cm, 2.4 * cm])
    para(f"Umbrales del modelo productivo (validación con 10 pseudo-desconocidos): confianza ≥ {th['min_class_confidence']}, "
         f"incertidumbre ≤ {th['max_angle_uncertainty_deg']}°, distancia de desconocido ≤ {th['unknown_distance']:.2f}. "
         "Cuando la decisión no es ACT el robot recibe {\"action\": \"HOLD\", \"reason\": código} y nunca un ángulo dudoso.")

    st.append(Paragraph("8. Análisis de errores", h2))
    for p, rows in RES["error_analysis"].items():
        table([[f"Prot. {p}: objeto", "Fallos e2e", "Vistas test", "Errores de clase", "Confundido con"]] +
              [[r["object"], r["failures"], r["n"], r["class_errors"], r["confused_with"] or "—"] for r in rows[:6]],
              [3.2 * cm, 2.2 * cm, 2.2 * cm, 2.8 * cm, 3 * cm])
    fig("07_failures_C", 16 * cm, "Figura 7. Ejemplos de fallos en C (arriba: consulta; abajo: referencia de la predicción).")
    fig("07_failures_B", 16 * cm, "Figura 8. Ejemplos de fallos en B.")
    para("Los fallos angulares se concentran en objetos simétricos o con vistas opuestas parecidas (errores de ~180° o arbitrarios) y en "
         "el arco no visto (B); los errores de clase, entre objetos de la misma familia visual. Ambos tipos de caso son los que la regla de "
         "decisión envía a REVIEW.")

    st.append(Paragraph("9. Costo: latencia y tamaño", h2))
    table(lat_rows(), [8 * cm, 3 * cm, 3 * cm])
    para(f"Artefacto clásico: {num(RES['cost']['classic_artifact_mb'], 1)} MB. Pesos ResNet18 fp32: {RES['cost']['resnet18_weights_mb']} MB "
         "(más la dependencia de PyTorch). *La línea base se mide en lote (búsqueda 1-NN sin extracción ni decisión).")
    fig("08_latency", 12 * cm, "Figura 9. Latencia por imagen.")
    lat = RES["cost"]["latency"]
    lc = lat.get("classic (pipeline completo)", {}).get("p50_ms")
    lr = lat.get("resnet18 CPU (solo embedding)", {}).get("p50_ms")
    sa, sd = S["A"], S["D"]
    para(f"¿Justifica la brecha el costo? La referencia moderna mejora la exactitud de clase en los protocolos con brecha "
         f"(B: {pct(S['B']['resnet18']['class_accuracy'], 2)} vs {pct(S['B']['classic']['class_accuracy'], 2)}; C: "
         f"{pct(S['C']['resnet18']['class_accuracy'], 2)} vs {pct(S['C']['classic']['class_accuracy'], 2)}) y el MAE angular, pero no resuelve "
         f"el arco ciego ni habilita automatizar C. En el régimen operativo, el clásico automatiza más (A: {pct(sa['classic']['coverage_act'])} vs "
         f"{pct(sa['resnet18']['coverage_act'])}; D: {pct(sd['classic']['coverage_act'])} vs {pct(sd['resnet18']['coverage_act'])}) con error entre ACT "
         f"de {pct(sa['classic']['error_among_act'], 2)} / {pct(sd['classic']['error_among_act'], 2)} (ResNet: {pct(sa['resnet18']['error_among_act'], 2)} / "
         f"{pct(sd['resnet18']['error_among_act'], 2)}). Solo el embedding ResNet18 cuesta {num(lr, 1)} ms p50 en CPU frente a {num(lc, 1)} ms del "
         "pipeline clásico completo, y añade ~45 MB de pesos más la dependencia de PyTorch. Producción = clásico; ResNet18 queda como referencia y "
         "candidata si el piloto en planta (fondos e iluminación reales) muestra una brecha mayor, en particular en desconocidos casi duplicados.")

    st.append(PageBreak())
    st.append(Paragraph("10. Model card", h2))
    for line in model_card_md().splitlines()[3:]:
        if not line.strip():
            continue
        if line.startswith("## "):
            st.append(Paragraph(line[3:], ParagraphStyle("h3", parent=h2, fontSize=10)))
        elif line.startswith("|"):
            continue  # la tabla de métricas ya está en la sección 1
        else:
            t = line.lstrip("- ").replace("**", "")
            import re

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
