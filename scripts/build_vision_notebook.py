"""Genera notebooks/01_experimentacion.ipynb (las celdas importan src/vision; no duplican lógica).

Uso: uv run python scripts/build_vision_notebook.py
     uv run jupyter nbconvert --to notebook --execute --inplace notebooks/01_experimentacion.ipynb
"""
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
cells: list = []


def md(s):
    cells.append(nbf.v4.new_markdown_cell(s.strip()))


def code(s):
    cells.append(nbf.v4.new_code_cell(s.strip()))


md("""
# Reto 2 — Visión: identificación, ángulo y recuperación en COIL-100

Notebook de punta a punta y reproducible: **adquisición de datos → EDA → experimentación por protocolo →
selección de modelo → análisis de errores → exportación del artefacto**. Toda la lógica vive en `src/vision/`;
aquí solo se orquesta y se muestran resultados. Semilla global 42. Los experimentos quedan registrados en
MLflow (`sqlite:///mlflow.db`, experimento `vision-coil100`).

**Regla de oro metodológica:** todo hiperparámetro y todo umbral se elige con particiones de *validación*
construidas exclusivamente con las vistas de entrenamiento de cada protocolo; el test se evalúa una sola vez.
""")

code("""
import sys, json, time, warnings
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display
from src.vision import data as vdata, analysis as va, experiments as vexp
from src.vision.data import ensure_dataset, sha256_file, data_version_hash, protocol_split, validation_split, COIL_SHA256
pd.set_option("display.width", 200); pd.set_option("display.max_columns", 30)
np.random.seed(42)
T0 = time.time()
""")

md("""
## 1. Adquisición y verificación de datos

Si `data/raw/coil-100/` no existe, se descarga el zip oficial
(`https://www.cs.columbia.edu/CAVE/databases/SLAM_coil-20_coil-100/coil-100/coil-100.zip`), se verifica su
SHA-256 y se extrae. Además se calcula un **hash de versión de datos** (SHA-256 sobre nombre + contenido de
cada PNG) que viaja en MLflow y en `metadata.json` del artefacto.
""")

code("""
img_dir = ensure_dataset()
zip_path = img_dir.parent / "coil-100.zip"
if zip_path.exists():
    print("SHA-256 zip:", sha256_file(zip_path), "OK" if sha256_file(zip_path) == COIL_SHA256 else "DISTINTO")
DATA_HASH = data_version_hash()
print("imágenes:", len(list(img_dir.glob("obj*__*.png"))), "| hash de versión de datos:", DATA_HASH[:16], "…")
data = vexp.load_all()          # imágenes + características clásicas + píxeles crudos + embeddings ResNet18 (cache en data/runtime)
ds = data["ds"]
print(ds.images.shape, "clases:", len(np.unique(ds.labels)), "ángulos:", np.unique(ds.angles)[:4], "…", np.unique(ds.angles)[-1])
""")

md("## 2. Análisis exploratorio (EDA)")

code("""
fig = va.sample_grid(ds); va.savefig(fig, "01_sample_grid"); plt.show()
st = va.image_stats(ds)
desc = pd.DataFrame({k: pd.Series(v).describe(percentiles=[.001, .01, .5, .99, .999]) for k, v in st.items()}).T
display(desc.round(3))
""")

md("""
**Lectura.** 100 objetos × 72 vistas (paso exacto de 5°), 128×128 RGB, **fondo oscuro uniforme** (mediana del
borde ≈ 25/255, *no* 0) e **iluminación controlada**: brillo y nitidez varían poco. Consecuencias de diseño:

* El problema de clase es "fácil" en laboratorio; el riesgo real está en el ángulo y en la extrapolación a planta.
* Como el fondo es casi constante, una máscara por umbral de gris (> 40) separa objeto/fondo; en planta esto no
  vale (cinta, reflejos) → compuerta de calidad + segmentación robusta serían requisitos.
* Las estadísticas de brillo/nitidez/fracción de objeto definen los umbrales de la **compuerta de calidad**
  determinista (`src/vision/quality.py`, percentil extremo × margen).
""")

code("""
from src.vision.models import VisionModel
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
Zall = PCA(150, random_state=42).fit_transform(StandardScaler().fit_transform(data["classic"]))  # solo para EDA
sym = va.symmetry_analysis(Zall, ds)
fig = va.symmetry_figure(sym); va.savefig(fig, "02_symmetry"); plt.show()
symdf = pd.DataFrame({"perfil": sym["profile_score"], "loo": sym["loo_score"], "d180/d90": sym["ratio_180_90"]})
symdf.index = [f"obj{o}" for o in symdf.index]
print("Más rotacionalmente ambiguos según el perfil (d@5° / d@≥45°):")
display(symdf.sort_values("perfil", ascending=False).head(10).round(3))
print("Vista 'gemela' más parecida a ≥30° (índice LOO):")
display(symdf.sort_values("loo", ascending=False).head(8).round(3))
print(f"Objetos con vistas opuestas (180°) más parecidas que a 90°: {(symdf['d180/d90'] < 1).sum()} de 100")
""")

md("""
**Simetría rotacional (cuantificada).** Para cada objeto se mide la distancia media en el espacio de
características entre pares de vistas en función de su separación angular. En un objeto asimétrico la curva
crece con la separación; en uno simétrico (latas, tazas, donas, frascos: p. ej. obj95, obj94, obj47, obj25)
es casi plana → **el ángulo es intrínsecamente ambiguo**. El índice LOO (¿la vista más parecida está a ≥30°?)
detecta además objetos con vistas a 180° casi idénticas (cajas/frascos con dos caras parecidas).

**Tratamiento:** (i) a nivel de instancia, `estimate_angle` marca `ambiguous` si existe una vista de referencia
a ≥30° del ángulo estimado con distancia ≤ 1.10 × la mejor; (ii) a nivel de clase, el modelo productivo
(referencias cada 5°) marca como simétricas las clases con índice LOO ≥ 0.25; (iii) **empíricamente**: en
cada protocolo, las clases cuyo ángulo falla (> ±10°) en más del 25 % de sus vistas de *validación* se marcan
como de ángulo poco fiable (detecta las donas/tazas obj47, obj94, obj95 sin mirar el test); (iv) todo esto genera
`rotational_ambiguity` → **REVIEW** (el robot recibe HOLD).

**Vistas a 180°:** en ~3/4 de los objetos la vista opuesta (180°) está más cerca en el espacio de
características que la vista a 90° (la silueta de un objeto visto desde lados opuestos es casi la imagen espejo).
Por eso el ángulo no se estima con una media circular de todos los vecinos (se cancelarían modos opuestos), sino
con el modo del vecino más cercano refinado localmente, y la ambigüedad se marca explícitamente. Alternativa documentada: para un objeto
perfectamente simétrico el ángulo no importa para el agarre y podría reportarse "módulo simetría"; no lo
hacemos por defecto porque la etiqueta/código de barras sí puede depender del ángulo.
""")

code("""
pairs = va.confusable_pairs(Zall, ds, top=10)
display(pd.DataFrame(pairs).round(2))
""")

md("""
**Clases casi duplicadas.** Los pares con menor distancia inter-objeto (comparada con la distancia típica entre
vistas adyacentes del mismo objeto) son los candidatos a confusión: mismas formas con distinto color/texto
(p. ej. cajas o latas de la misma familia). Esto motiva incluir **color** (HSV) además de forma (HOG).

**Laboratorio vs. planta.** COIL-100 tiene fondo negro, luz controlada, cámara fija a la misma distancia,
un único eje de rotación (tornamesa), sin oclusión ni desenfoque por movimiento. En planta habrá: cinta con
textura y reflejos, iluminación variable, oclusión parcial/objetos múltiples, movimiento de la cinta (blur),
pose de cámara y escala variables, empaques nuevos. Mitigaciones: (1) aumentación de datos (brillo/contraste,
blur, ruido, escala, recortes) y captura de vistas reales por SKU en la estación; (2) compuerta de calidad
(oscuridad, saturación, nitidez, cuadro vacío) antes del modelo; (3) re-calibrar umbrales con datos de planta
(los umbrales son configurables en caliente vía `/v1/admin/thresholds`); (4) **piloto en sombra** con revisión
humana del 100% antes de habilitar ACT, y monitoreo de la tasa ACT/REVIEW/REJECT y de la deriva de distancias.
""")

md("""
## 3. Protocolos y esquema de validación

| Protocolo | Train (por objeto) | Test | Validación (solo dentro de train) |
|---|---|---|---|
| A Interpolación | 0,10,…,350 (36) | 5,15,…,355 (36) | fit paso 20°, val el resto (brecha 10° > 5° del test) |
| B Sector ciego | 60 vistas | arco contiguo de 60° (12), inicio aleatorio por objeto (semilla 42) | se oculta otro arco de 60° dentro de train (semilla 43) |
| C Escasez | 1 cada 30° (12) | las otras 60 | fit paso 60°, val el resto |
| D Desconocido | 90 objetos (paso 10°) | vistas impares de los 90 + **todas** las de 10 objetos desconocidos | 10 pseudo-desconocidos entre los 90 (semilla 43) |

Notas: en D entrenamos con las vistas de paso 10° de los 90 conocidos (no todas) para poder medir *ambos lados*
(conocidos aceptados y desconocidos rechazados) con vistas no vistas. La validación imita (y suele exagerar) la
brecha de cada protocolo; en C la brecha de validación (30°) es mayor que la del test (≤15°), por lo que el
umbral angular no se puede afinar ahí y se usa el prior 5° (= mitad de la tolerancia de ±10°).
""")

code("""
rows = []
for p in "ABCD":
    sp = protocol_split(ds.labels, ds.angles, p); vs = validation_split(ds.labels, ds.angles, p)
    rows.append({"protocolo": p, "train": len(sp["train"]), "test": len(sp["test"]), "fit (val)": len(vs["fit"]),
                 "val": len(vs["val"]), "fuga train∩test": len(np.intersect1d(sp["train"], sp["test"]))})
display(pd.DataFrame(rows))
spB = protocol_split(ds.labels, ds.angles, "B"); spD = protocol_split(ds.labels, ds.angles, "D")
print("B: inicio del arco ciego (primeros 10 objetos):", {f"obj{k}": v for k, v in list(spB["arc_starts"].items())[:10]})
print("D: objetos desconocidos (semilla 42):", [f"obj{o}" for o in spD["unknown_objects"]])
""")

md("""
## 4. Experimentación

**Características clásicas** (`features.py`, ~3 ms/imagen): histograma HSV 16×4×4 sobre la máscara de objeto
(raíz cuadrada → Hellinger), HOG 64×64 (9 orientaciones, celdas 8×8), LBP uniforme en rejilla 2×2 y momentos
de Hu; estandarización + PCA(150) ajustados **solo con train**.

**Clasificadores comparados** (rejilla pequeña, elegida en validación): kNN (k∈{1,5}), regresión logística
(C∈{0.1,1,10}), SVM RBF con probabilidades de Platt (C∈{1,10}) y Random Forest (300 árboles).

**Ángulo:** recuperación condicionada al objeto — entre las vistas de referencia de la clase predicha se toma
la más cercana y se refina con la media circular ponderada de los 2 vecinos (interpolación); la
**incertidumbre** (en grados) se obtiene invirtiendo la curva distancia-vs-separación del propio objeto: "la
distancia del query a su vecino más cercano equivale a X° de giro". La misma recuperación da el top-k similar.

**Desconocido / dudoso:** distancia al vecino más cercano (novedad) → REJECT; confianza de clase baja,
incertidumbre angular alta o ambigüedad rotacional → REVIEW. Umbrales elegidos en validación para maximizar
la fracción automatizada con **error entre ACT ≤ 2 %**; si ningún umbral lo logra, la automatización se
desactiva (todo a REVIEW).

**Comparaciones obligatorias:** línea base 1-NN en píxeles crudos 32×32 (ángulo = el del vecino, sin rechazo)
y referencia moderna: **ResNet18 ImageNet congelada** (embedding 512-d) con exactamente el mismo pipeline
(clasificadores, ángulo, umbrales) para aislar el efecto de la representación.
""")

code("""
OUT_DIR = ROOT / "data" / "runtime" / "experiments"
t = time.time()
results = vexp.run_all(data, features=("classic", "resnet18"), out_dir=OUT_DIR, log=lambda *a: None)
print(f"experimentos: {time.time() - t:.0f}s")
""")

md("### 4.1 Validación y selección de modelo")

code("""
vrows = []
for p, rr in results["protocols"].items():
    for k, m in rr["classic"]["validation"].items():
        clf, params = k.split(":", 1)
        vrows.append({"protocolo": p, "clasificador": clf, "params": params, "val_exactitud": m["class_accuracy"],
                      "val_e2e": m["end_to_end_success"], "fit_s": m["fit_s"]})
vdf = pd.DataFrame(vrows)
display(vdf.pivot_table(index=["clasificador", "params"], columns="protocolo", values="val_exactitud").round(4))
prod = vexp.select_production_classifier(results, "classic")
print("Seleccionado para producción (mejor exactitud media de VALIDACIÓN en A–D):", prod["clf"], prod["params"])
display(pd.DataFrame({k: v for k, v in prod["mean_val"].items() if not k.startswith("_")}).T.round(4))
""")

md("""
La selección usa solo validación. Regresión logística y SVM RBF quedan prácticamente empatadas en exactitud,
pero la logística está **mucho mejor calibrada** (ver ECE abajo), es ~10× más rápida de entrenar y su artefacto
es pequeño; kNN y Random Forest quedan detrás en los protocolos con brecha (B, C).
""")

md("### 4.2 Resultados en test por protocolo (evaluación única)")

code("""
def table(feature):
    rows = []
    for p, rr in results["protocols"].items():
        r = rr[feature]
        for clf, m in r["test"].items():
            rows.append({"protocolo": p, "modelo": f"{feature}:{clf}" + (" *" if clf == r["selected"]["clf"] else ""),
                         "exactitud": m["class_accuracy"], "MAE ángulo°": m["angle_mae_deg"], "≤10°": m["angle_within_10"],
                         "éxito e2e": m["end_to_end_success"], "ACT": m["coverage_act"], "REVIEW": m["review_rate"],
                         "REJECT": m["reject_rate"], "error|ACT": m["error_among_act"], "ECE": m["ece"],
                         "AUROC D": m.get("auroc_combined_rank", np.nan)})
        if feature == "classic":
            b = r["baseline_raw_1nn"]
            rows.append({"protocolo": p, "modelo": "baseline: píxeles 1-NN", "exactitud": b["class_accuracy"],
                         "MAE ángulo°": b["angle_mae_deg"], "≤10°": b["angle_within_10"], "éxito e2e": b["end_to_end_success"],
                         "ACT": 1.0, "REVIEW": 0.0, "REJECT": 0.0, "error|ACT": b["error_among_act"], "ECE": np.nan,
                         "AUROC D": b.get("auroc_nn_distance", np.nan)})
    return pd.DataFrame(rows)
res_classic = table("classic"); res_resnet = table("resnet18")
display(res_classic.round(4)); display(res_resnet.round(4))
fig = va.per_protocol_bars(results); va.savefig(fig, "03_per_protocol"); plt.show()
""")

md("""
`*` = clasificador elegido en validación para ese protocolo. *éxito e2e* = clase correcta **y** error angular
circular ≤ 10° (sobre todas las vistas de test de objetos conocidos); *ACT* = fracción automatizada;
*error|ACT* = fracción de decisiones ACT que habrían sido un empaque equivocado o un agarre fallido (en D incluye
desconocidos aceptados como conocidos). AUROC D = combinación por rangos de distancia y confianza.
""")

md("### 4.3 Ángulo: error circular y alternativas")

code("""
outs = {p: va.load_outputs("classic", p, OUT_DIR) for p in "ABCD"}
fig = va.angle_error_hist({p: outs[p] for p in "ABC"}); va.savefig(fig, "04_angle_error_hist"); plt.show()
# alternativa: sin interpolación (ángulo del 1-NN) vs interpolación con 2 y 3 vecinos — mismo clasificador
from src.vision.experiments import _fit_predict
from src.vision.angle import circular_error
names, X = data["names"], data["classic"]
arows = []
for p in "ABC":
    sp = protocol_split(ds.labels, ds.angles, p); tr, te = sp["train"], sp["test"]
    for k in (1, 2, 3):
        cfg = {"clf": "logreg", "params": prod["params"], "k_angle_refine": k}
        _, o, _, _ = _fit_predict(X[tr], names[tr], ds.angles[tr], ds.image_ids[tr], X[te], cfg)
        ok = o["label"] == names[te]; e = circular_error(o["angle"], ds.angles[te])[ok]
        arows.append({"protocolo": p, "vecinos interpolados": k, "MAE°": e.mean(), "mediana°": np.median(e), "≤10°": (e <= 10).mean()})
display(pd.DataFrame(arows).pivot_table(index="vecinos interpolados", columns="protocolo", values=["MAE°", "≤10°"]).round(3))
""")

md("""
**Lectura del ángulo.** La métrica es el error circular `min(|a−b|, 360−|a−b|)`; reportamos MAE, mediana y
% ≤ 10° (la tolerancia de agarre). La interpolación con 2 vecinos reduce el MAE frente a copiar el ángulo del
vecino más cercano (el test cae *entre* vistas de referencia). En **B** (arco ciego de 60°) ningún método
basado en apariencia puede "inventar" las vistas no vistas: solo las vistas en los bordes del arco se recuperan
bien; el resto debe ir a REVIEW. En **C** (12 vistas) el ángulo es útil pero no alcanza la confiabilidad
exigida para automatizar.
""")

md("### 4.4 Confianza: calibración, riesgo–cobertura y rechazo de desconocidos")

code("""
fig = va.reliability_and_risk({p: outs[p] for p in "ABCD"}); va.savefig(fig, "05_reliability_risk"); plt.show()
fig = va.unknown_roc(outs["D"]); va.savefig(fig, "06_unknown_roc"); plt.show()
cal = []
for feat in ("classic", "resnet18"):
    for p, rr in results["protocols"].items():
        for clf, m in rr[feat]["test"].items():
            cal.append({"rep": feat, "protocolo": p, "clf": clf, "ECE": m["ece"], "AURC(conf)": m["aurc_confidence"]})
display(pd.DataFrame(cal).pivot_table(index=["rep", "clf"], columns="protocolo", values="ECE").round(4))
d = results["protocols"]["D"]["classic"]; m = d["test"][d["selected"]["clf"]]
print({k: round(m[k], 4) for k in ("auroc_nn_distance", "auroc_neg_confidence", "auroc_combined_rank", "unknown_rejected",
                                   "unknown_false_accept_act", "unknown_not_act", "known_false_reject", "known_coverage_act")})
print("umbrales D (validación):", {k: round(v, 3) if isinstance(v, float) else v for k, v in d["thresholds"][d["selected"]["clf"]].items()})
""")

md("""
**¿Es la confianza una base válida para decisiones automáticas?**

1. **Calibración:** la regresión logística tiene ECE bajo (la confianza ≈ probabilidad real de acierto); el SVM
   con Platt multiclase y el Random Forest están mal calibrados (ECE alto) aunque acierten igual → no deben usarse
   sus probabilidades crudas para decidir. Esto es un argumento adicional para la logística.
2. **Riesgo–cobertura:** ordenar por confianza (o por incertidumbre angular) reduce monótonamente el error entre
   los casos automatizados → existe un umbral operable. La incertidumbre angular es la señal que más importa en
   A/C (el error de clase casi no existe); la curva "P(|error| ≤ 10°) vs incertidumbre" muestra que la
   incertidumbre en grados es informativa y coherente con la tolerancia.
3. **Desconocidos (D):** la distancia al vecino más cercano y la confianza separan conocidos de desconocidos con
   AUROC alto; el umbral (Youden en validación con pseudo-desconocidos) rechaza la mayoría de desconocidos y casi
   ninguno termina en ACT.
""")

md("""
### 4.5 Criterio de aceptación: tolerancia ±10° → métrica de negocio

La tolerancia de agarre de ±10° se traduce en: **éxito extremo a extremo = clase correcta ∧ error circular ≤ 10°**.
El robot solo actúa en ACT; por eso la métrica de negocio es el par (**fracción automatizada**, **error entre ACT**
= tasa de empaque equivocado o agarre fallido entre lo que el robot ejecuta). El umbral de incertidumbre angular
se elige para que el error entre ACT ≤ 2 % en validación.
""")

code("""
acc = []
for p, rr in results["protocols"].items():
    for feat, lab in (("classic", "Clásico (prod.)"), ("resnet18", "ResNet18")):
        r = rr[feat]; m = r["test"][r["selected"]["clf"]]; th = r["thresholds"][r["selected"]["clf"]]
        acc.append({"protocolo": p, "modelo": lab, "éxito e2e (sin rechazo)": m["end_to_end_success"], "% ACT": m["coverage_act"],
                    "error|ACT": m["error_among_act"], "éxito automatizado": m["automated_success_rate"],
                    "umbral incert.°": th["max_angle_uncertainty_deg"], "umbral conf.": th["min_class_confidence"],
                    "automatización habilitada": th.get("automation_enabled", True)})
    b = rr["classic"]["baseline_raw_1nn"]
    acc.append({"protocolo": p, "modelo": "Base píxeles 1-NN", "éxito e2e (sin rechazo)": b["end_to_end_success"], "% ACT": 1.0,
                "error|ACT": b["error_among_act"], "éxito automatizado": b["end_to_end_success"]})
acc_df = pd.DataFrame(acc); display(acc_df.round(4))
""")

md("## 5. Análisis de errores")

code("""
fails = va.per_object_failures({p: outs[p] for p in "ABC"}, top=8)
for p, rows in fails.items():
    print(f"Protocolo {p}: objetos con más fallos e2e"); display(pd.DataFrame(rows))
for p in ("B", "C"):
    fig = va.failure_examples_figure(ds, outs, p); va.savefig(fig, f"07_failures_{p}"); plt.show()
# ¿los fallos se concentran en objetos ambiguos?
fA = outs["A"]; okA = (fA["label"] == fA["true_label"]) & (circular_error(fA["angle"], fA["true_angle"]) <= 10)
per_obj = pd.Series(~okA).groupby(fA["true_label"]).mean()
j = symdf.join(per_obj.rename("tasa_fallo_A"))
print("Correlación de Spearman índice de perfil vs. tasa de fallo en A:", round(j[["perfil", "tasa_fallo_A"]].corr("spearman").iloc[0, 1], 3))
""")

md("""
**Lectura.** Los fallos angulares en A/C/D se concentran en pocos objetos: los de mayor índice de perfil
(donas y taza: obj95, obj94, obj47) y los de mayor índice LOO (vistas "gemelas" a 180°: obj98, obj67, obj84). La
correlación de rangos sobre los 100 objetos es moderada porque la mayoría tiene 0 fallos; la concentración en la
cola es lo relevante. El error en esos casos es de ±180° o "cualquier ángulo", no de unos pocos grados, y son los
que el sistema marca como `rotational_ambiguity` / `high_angle_uncertainty` → REVIEW. Los errores de clase se dan entre objetos de la misma familia visual (pares
casi duplicados de la sección 2). En B el fallo dominante es estructural (arco no visto).
""")

md("## 6. Costo: latencia y tamaño del artefacto (clásico vs. referencia moderna)")

code("""
import subprocess
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "train_vision.py")], capture_output=True, text=True, cwd=ROOT)
print(r.stdout[-1500:]); print(r.stderr[-800:] if r.returncode else "")
""")

md("""
*(La celda anterior exporta el artefacto productivo; ver sección 7. Aquí se usa para medir la latencia real del
pipeline completo: calidad + características + clasificador + ángulo + top-k + decisión.)*
""")

code("""
from src.vision.inference import VisionPredictor
pred = VisionPredictor.load(ROOT / "models" / "vision-1.0.0")
rng = np.random.default_rng(42); sample = ds.images[rng.choice(len(ds.images), 100, replace=False)]
lat = {"classic (pipeline completo)": va.measure_latency(pred, sample)}
t = time.perf_counter(); _ = vexp.raw_pixels(sample[:1], 32); lat["baseline píxeles 1-NN*"] = {"p50_ms": results["protocols"]["A"]["classic"]["baseline_raw_1nn"]["predict_ms_per_image_batch"]}
rl = va.measure_resnet_latency(sample, n=50)
lat["resnet18 CPU (solo embedding)"] = rl["cpu"]
if "mps" in rl: lat["resnet18 MPS (solo embedding)"] = rl["mps"]
display(pd.DataFrame(lat).T.round(2))
size_mb = sum(f.stat().st_size for f in (ROOT / "models" / "vision-1.0.0").glob("*") if f.is_file()) / 1e6
print(f"Artefacto clásico: {size_mb:.1f} MB | pesos ResNet18 fp32: {rl['weights_mb_fp32']} MB (+ torch/torchvision ~ cientos de MB)")
fig = va.latency_figure({k: v for k, v in lat.items()}); va.savefig(fig, "08_latency"); plt.show()
COST = {"latency": lat, "classic_artifact_mb": size_mb, "resnet18_weights_mb": rl["weights_mb_fp32"]}
""")

md("""
**¿Justifica la brecha el costo?** La referencia moderna mejora sobre todo la exactitud de clase en los
protocolos con brecha angular (B, C) y el MAE de ángulo, pero (a) **no resuelve** el problema central del arco
ciego (B) ni habilita automatizar C; (b) en A/D —el régimen operativo real, con referencias densas— el pipeline
clásico ya está cerca del techo: con la decisión ACT/REVIEW automatiza más casos que la ResNet, con error entre
ACT bajo (la ResNet logra menor error entre ACT en D, a costa de automatizar menos; ver tabla 4.5); (c) solo el
embedding de ResNet18 ya cuesta más en CPU que el pipeline clásico completo, añade ~45 MB de pesos más la
dependencia de PyTorch, y es menos interpretable. Conclusión: **producción =
clásico**; ResNet18 queda como referencia y como candidata si en planta (con fondos/iluminación reales) la brecha
crece — decisión a tomar con datos del piloto.
""")

md("## 7. Exportación del artefacto y registro")

code("""
results["production_selection"] = {"clf": prod["clf"], "params": prod["params"], "criterion": "max exactitud media de validación A–D"}
results["production_selection_scores"] = prod["mean_val"]
results["summary"] = va.build_summary(results)
results["cost"] = COST
results["symmetry"] = {"profile_score": {f"obj{k}": v for k, v in sym["profile_score"].items()},
                       "loo_score": {f"obj{k}": v for k, v in sym["loo_score"].items()}}
results["confusable_pairs"] = pairs
results["error_analysis"] = fails
results["angle_alternatives"] = arows
results["acceptance_table"] = acc_df.to_dict(orient="records")
p = va.write_results(vexp.to_jsonable(results)); print("escrito", p)
# re-exporta con la selección registrada en results.json (train_vision.py la lee de ahí)
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "train_vision.py")], capture_output=True, text=True, cwd=ROOT)
print(r.stdout[-1200:])
meta = json.loads((ROOT / "models" / "vision-1.0.0" / "metadata.json").read_text())
print({k: meta[k] for k in ("version", "classifier", "thresholds", "symmetric_classes", "artifact_size_mb", "data_version_hash")})
print(f"Tiempo total del notebook: {(time.time() - T0) / 60:.1f} min")
""")

md("""
## 8. Conclusiones

* **Modelo productivo:** características clásicas (HSV + HOG + LBP + Hu) → PCA(150) → regresión logística, con
  ángulo por recuperación condicionada al objeto e incertidumbre en grados; decisión ACT/REVIEW/REJECT con umbrales
  fijados en validación. Artefacto pequeño, ~15 ms/imagen en CPU, trazable (hash de datos, MLflow, metadata).
* **Protocolos:** A y D son automatizables (ver tabla 4.5: fracción ACT alta y error entre ACT cercano al
  objetivo de 2 %); C (12 vistas/objeto) y B (arco ciego) **no** lo son con la confiabilidad exigida — el sistema
  lo detecta en validación y envía a REVIEW (o desactiva la automatización) en lugar de actuar. Esto se
  traduce en el requisito operativo para dar de alta un SKU: **≥ 12 vistas cubriendo ≥ 300°** como mínimo para
  staging, y referencias densas (idealmente cada 5–10°) para habilitar ACT.
* **Limitaciones:** ver la model card (`docs/model_card.md`) — dataset de laboratorio, un solo eje de rotación,
  sin oclusión ni variación de fondo/iluminación; requiere piloto en sombra y recalibración en planta.
""")

nb = nbf.v4.new_notebook()
nb["cells"] = cells
nb["metadata"]["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
out = ROOT / "notebooks" / "01_experimentacion.ipynb"
out.parent.mkdir(parents=True, exist_ok=True)
nbf.write(nb, out)
print("notebook escrito en", out)
