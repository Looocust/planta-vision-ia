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

Notebook de punta a punta y reproducible: **adquisición de datos → EDA → exploración y selección de modelo →
evaluación de la receta productiva congelada → análisis de errores → costo → exportación del artefacto**. Toda la lógica vive en `src/vision/`;
aquí solo se orquesta y se muestran resultados. Semilla global 42. Los experimentos quedan registrados en
MLflow (`sqlite:///mlflow.db`, experimento `vision-coil100`).

**Regla metodológica:** todo hiperparámetro y todo umbral se elige con particiones de *validación* construidas
exclusivamente con las vistas de entrenamiento de cada protocolo; ningún número de test se usa para elegir nada.
Hay dos niveles: (i) **exploración** (sección 4), que compara clasificadores y reporta también su test a título
informativo; (ii) la **receta productiva congelada** (sección 5), definida una sola vez en `src/vision/recipe.py`,
que se ajusta en el train de cada protocolo y se evalúa **una única vez** en su test. Las cifras de cabecera son
las de la receta.
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
from src.vision.angle import circular_error
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
| D estricto | 90 objetos, **todas** sus vistas (72) | solo las 720 vistas de los 10 objetos desconocidos | 10 pseudo-desconocidos entre los 90 (semilla 43); fit paso 10° de los otros 80 |
| D variante | 90 objetos, paso 10° (36) | vistas impares de los 90 + todas las de los 10 desconocidos | 10 pseudo-desconocidos entre los 90 (semilla 43) |

**D estricto** es el protocolo pedido: como el test solo tiene desconocidos, no se puede calcular AUROC; se
reporta la tasa de desconocidos rechazados y la de **desconocidos que llegan a ACT** (falsa aceptación). La
**variante D** (experimento adicional) entrena con la mitad de las vistas de los conocidos para poder medir
*ambos lados* con vistas no vistas (AUROC, conocidos rechazados por error). La validación imita (y suele exagerar)
la brecha de cada protocolo; en C la brecha de validación (30°) es mayor que la del test (≤15°).
""")

code("""
rows = []
for p in ("A", "B", "C", "D_strict", "D"):
    sp = protocol_split(ds.labels, ds.angles, p); vs = validation_split(ds.labels, ds.angles, p)
    rows.append({"protocolo": p, "train": len(sp["train"]), "test": len(sp["test"]), "fit (val)": len(vs["fit"]),
                 "val": len(vs["val"]), "fuga train∩test": len(np.intersect1d(sp["train"], sp["test"])),
                 "val ⊂ train": bool(np.isin(np.r_[vs["fit"], vs["val"]], sp["train"]).all())})
display(pd.DataFrame(rows))
spB = protocol_split(ds.labels, ds.angles, "B"); spD = protocol_split(ds.labels, ds.angles, "D")
print("B: inicio del arco ciego (primeros 10 objetos):", {f"obj{k}": v for k, v in list(spB["arc_starts"].items())[:10]})
print("D: objetos desconocidos (semilla 42):", [f"obj{o}" for o in spD["unknown_objects"]])
""")

md("""
## 4. Exploración: comparación de clasificadores y selección de la receta

**Características clásicas** (`features.py`, ~3 ms/imagen): histograma HSV 16×4×4 sobre la máscara de objeto
(raíz cuadrada → Hellinger), HOG 64×64 (9 orientaciones, celdas 8×8), LBP uniforme en rejilla 2×2 y momentos
de Hu; estandarización + PCA(150) ajustados **solo con train**.

**Clasificadores comparados** (rejilla pequeña, elegida en validación): kNN (k∈{1,5}), regresión logística
(C∈{0.1,1,10}), SVM RBF con probabilidades de Platt (C∈{1,10}) y Random Forest (300 árboles).

**Ángulo:** recuperación condicionada al objeto — entre las vistas de referencia de la clase predicha se toma
la más cercana y se refina con la media circular ponderada de los k vecinos cercanos a ese modo (k∈{1,2,3} se
elige en validación). La **incertidumbre angular** es un *score heurístico expresado en grados*: la distancia del
query a su vecino más cercano se traduce, con la curva distancia-vs-separación del propio objeto, en "cuántos
grados de giro equivalen a esa diferencia de apariencia". **No es una cota ni un intervalo del error real**; su
umbral se elige por el criterio empírico de riesgo (sección 5.3).

**Decisión:** distancia al vecino más cercano (novedad) → REJECT; confianza de clase baja, incertidumbre alta o
ambigüedad rotacional → REVIEW. Umbrales en validación: máxima fracción automatizada con error entre ACT ≤ 2 %
y **al menos 50 decisiones ACT en validación**; si no se cumple, la automatización se desactiva (todo a REVIEW)
y el riesgo se reporta N/A. Los riesgos se acompañan de intervalos de Wilson 95 %.

**Comparaciones obligatorias:** línea base 1-NN en píxeles crudos 32×32 (ángulo = el del vecino, sin rechazo:
siempre actúa) y referencia moderna: **ResNet18 ImageNet congelada** (embedding 512-d) con el mismo pipeline.
""")

code("""
OUT_DIR = ROOT / "data" / "runtime" / "experiments"
t = time.time()
results = vexp.run_all(data, features=("classic", "resnet18"), out_dir=OUT_DIR, log=lambda *a: None)
print(f"exploración: {time.time() - t:.0f}s")
vrows = []
for p, rr in results["protocols"].items():
    for k, m in rr["classic"]["validation"].items():
        clf, params = k.split(":", 1)
        vrows.append({"protocolo": p, "clasificador": clf, "params": params, "val_exactitud": m["class_accuracy"]})
vdf = pd.DataFrame(vrows)
display(vdf.pivot_table(index=["clasificador", "params"], columns="protocolo", values="val_exactitud").round(4))
prod = vexp.select_production_classifier(results, "classic")
print("Mejor exactitud media de VALIDACIÓN en A–D:", prod["clf"], prod["params"])
display(pd.DataFrame({k: v for k, v in prod["mean_val"].items() if not k.startswith("_")}).T.round(4))
""")

md("""
La regresión logística tiene la mejor exactitud media de validación (≈3 puntos sobre SVM RBF y ≈5 sobre kNN y
Random Forest) y además es la mejor calibrada (tabla de ECE abajo), rápida de entrenar y con artefacto pequeño. Con
esto se **congela la receta**: características clásicas + PCA(150) + logística C=1 + el procedimiento de umbrales.
La tabla siguiente es el test de *cada* clasificador de la exploración (cada uno con su mejor configuración de
validación por protocolo): es informativa y no se usa para elegir.
""")

code("""
erows = []
for p, rr in results["protocols"].items():
    for feat in ("classic", "resnet18"):
        r = rr[feat]
        for clf, m in r["test"].items():
            erows.append({"protocolo": p, "rep": feat, "clf": clf, "exactitud": m["class_accuracy"], "MAE°": m["angle_mae_deg"],
                          "éxito e2e": m["end_to_end_success"], "ECE": m["ece"]})
edf = pd.DataFrame(erows)
display(edf.pivot_table(index=["rep", "clf"], columns="protocolo", values="exactitud").round(4))
display(edf.pivot_table(index=["rep", "clf"], columns="protocolo", values="ECE").round(4))
""")

md("""
**Calibración.** La logística tiene ECE bajo en todos los protocolos (su confianza ≈ probabilidad real de
acierto); SVM con Platt multiclase y Random Forest están mal calibrados aunque acierten parecido, por lo que sus
probabilidades crudas no deben usarse para decidir.
""")

md("""
## 5. Receta productiva congelada: evaluación independiente por protocolo (cifras de cabecera)

Receta (`src/vision/recipe.py`): características clásicas → PCA(150) → logística C=1; k angular ∈ {1,2,3} elegido
en validación; clases con ángulo poco fiable en validación → `rotational_ambiguity`; umbrales con el procedimiento
de la sección 4. En cada protocolo se ajusta **solo** con su train (validación dentro de train) y se evalúa **una
vez** en su test. La misma receta se aplica con embeddings ResNet18 como referencia moderna.
""")

code("""
from src.vision import recipe as vrec
t = time.time()
R = vrec.run_recipe_all(data, features=("classic", "resnet18"), out_dir=OUT_DIR)
print(f"receta: {time.time() - t:.0f}s")
RS = va.build_recipe_summary(R["results"], R["baselines"])
def fmt_ci(x):
    return "N/A" if x is None or x[0] is None else f"[{x[0]:.2%}, {x[1]:.2%}]"
hrows = []
for p, row in RS.items():
    for key, lab in (("recipe_classic", "Receta clásica (prod.)"), ("baseline_raw_1nn", "Base píxeles 1-NN (siempre ACT)"),
                     ("recipe_resnet18", "Receta con ResNet18")):
        m = row.get(key)
        if not m: continue
        hrows.append({"protocolo": p, "modelo": lab, "k": m.get("k"), "exactitud": m["class_accuracy"], "MAE°": m["angle_mae_deg"],
                      "≤10°": m["angle_within_10"], "éxito e2e": m["end_to_end_success"], "% ACT": m["coverage_act"],
                      "n ACT": m.get("n_act"), "error|ACT": "N/A" if m["error_among_act"] is None else round(m["error_among_act"], 4),
                      "IC95 error|ACT": fmt_ci(m.get("error_among_act_wilson95")),
                      "automatización": m.get("automation_enabled", "—")})
hdf = pd.DataFrame(hrows); display(hdf)
thr = pd.DataFrame({p: {**R["results"]["classic"][p]["thresholds"], "k": R["results"]["classic"][p]["k_selected_on_val"],
                        "val n ACT": R["results"]["classic"][p]["threshold_validation"]["val_n_act"],
                        "val error|ACT": R["results"]["classic"][p]["threshold_validation"]["val_error_among_act"]}
                    for p in RS}).T
print("Umbrales de la receta clásica en cada protocolo (todos elegidos en validación):"); display(thr)
fig = va.recipe_bars(RS); va.savefig(fig, "03_per_protocol"); plt.show()
""")

md("""
`error|ACT` = P(clase incorrecta, objeto desconocido aceptado o error angular > 10° | el robot actúa), con IC de
Wilson 95 %. *éxito e2e* = clase correcta **y** error circular ≤ 10° sobre las vistas conocidas, sin rechazo.
La línea base siempre actúa: en D sus desconocidos cuentan como error entre ACT (en D estricto su error es 100 %).
"N/A" = la receta no autorizó ninguna acción (automatización desactivada por falta de evidencia en validación).
""")

md("### 5.1 Ángulo: error circular y elección de k en validación")

code("""
outs = {p: va.load_outputs("recipe_classic", p, OUT_DIR) for p in ("A", "B", "C", "D")}
fig = va.angle_error_hist({p: outs[p] for p in "ABC"}); va.savefig(fig, "04_angle_error_hist"); plt.show()
kt = pd.DataFrame([{"protocolo": p, **r} for p in ("A", "B", "C", "D", "D_strict") for r in R["results"]["classic"][p]["k_table_val"]])
display(kt.pivot_table(index="k", columns="protocolo", values=["val_within_10", "val_mae_deg"]).round(3))
print("k elegido en validación:", {p: R["results"]["classic"][p]["k_selected_on_val"] for p in RS})
""")

md("""
**Lectura.** La métrica es el error circular `min(|a−b|, 360−|a−b|)`. La tabla de k es de **validación** (no de
test): interpolar con 2–3 vecinos ayuda cuando el query cae entre dos vistas de referencia cercanas (A, D), pero
no cuando la brecha es grande (B, C), donde k=1 queda igual o mejor — por eso k se elige por protocolo en
validación. En **B** (arco ciego de 60°) ningún método basado en apariencia puede "inventar" las vistas no vistas;
en **C** (12 vistas) el ángulo es útil pero no alcanza la confiabilidad exigida para automatizar.
""")

md("### 5.2 Confianza: calibración, riesgo–cobertura y desconocidos")

code("""
fig = va.reliability_and_risk(outs); va.savefig(fig, "05_reliability_risk"); plt.show()
fig = va.unknown_roc(outs["D"]); va.savefig(fig, "06_unknown_roc"); plt.show()
for p in ("D", "D_strict"):
    d = R["results"]["classic"][p]["decision"]
    print(p, {k: (round(v["rate"], 4), [round(x, 4) for x in v["wilson95"]]) for k, v in d.items() if k in ("unknown_rejected", "unknown_reaches_act")},
          "| AUROC combinado:", round(R["results"]["classic"][p].get("auroc_combined_rank", float("nan")), 4),
          "| conocidos rechazados:", round(d.get("known_false_reject", float("nan")), 4))
""")

md("""
**¿Es la confianza una base válida para decisiones automáticas?** (1) La logística está bien calibrada (diagrama
de confiabilidad, ECE bajo). (2) En las curvas riesgo–cobertura, priorizar los casos de mayor confianza o menor
incertidumbre deja el riesgo de los primeros tramos de cobertura muy por debajo del riesgo global; las curvas
**no** son estrictamente monótonas (hay subidas locales con pocos casos) y en B no existe un tramo con riesgo
aceptable. (3) En D (variante) distancia y confianza separan conocidos de desconocidos con AUROC alto; en D
estricto se reporta qué fracción de desconocidos se rechaza y cuántos llegan a ACT. Los desconocidos que llegan a
ACT son casi siempre "casi duplicados" de un conocido (p. ej. obj61 ≈ obj92): la confianza no los separa.
""")

md("""
### 5.3 Umbral de incertidumbre vs. tolerancia ±10°

El umbral de incertidumbre (p. ej. 30° en producción) **no** significa "error ≤ 30°": es un corte sobre un score
heurístico. Lo que importa es el riesgo empírico `P(clase incorrecta o |error| > 10° | ACT)`. La figura lo
muestra en función del umbral, en **validación** (donde se elige: el mayor umbral cuyo riesgo ≤ 2 % con ≥ 50 ACT)
y en el **test** de la receta (solo descriptivo), con IC de Wilson 95 %. Los umbrales altos son seguros porque
la ambigüedad y la confianza ya filtran los casos peligrosos; el score angular solo debe cortar la cola.
""")

code("""
fig = va.sweep_figure(R["results"]["classic"], ("A", "D")); va.savefig(fig, "09_uncertainty_sweep"); plt.show()
sw = pd.DataFrame([{"protocolo": p, **{k: v for k, v in r.items() if k in ("max_angle_uncertainty_deg", "coverage", "risk_rate", "risk_n")}}
                   for p in ("A", "D") for r in R["results"]["classic"][p]["test_uncertainty_sweep_descriptive"]])
display(sw.round(4))
""")

md("## 6. Análisis de errores (receta clásica)")

code("""
fails = va.per_object_failures({p: outs[p] for p in "ABC"}, top=8)
for p, rows in fails.items():
    print(f"Protocolo {p}: objetos con más fallos e2e"); display(pd.DataFrame(rows))
for p in ("B", "C"):
    fig = va.failure_examples_figure(ds, outs, p); va.savefig(fig, f"07_failures_{p}"); plt.show()
fA = outs["A"]; okA = (fA["label"] == fA["true_label"]) & (circular_error(fA["angle"], fA["true_angle"]) <= 10)
actA = fA["decision"] == "ACT"
print("A: fallos e2e totales:", int((~okA).sum()), "| fallos que llegaron a ACT:", int((~okA & actA).sum()))
""")

md("""
**Lectura.** Los fallos angulares se concentran en pocos objetos: los de mayor índice de perfil (donas y taza:
obj95, obj94, obj47) y los de mayor índice LOO (vistas "gemelas" a 180°: obj98, obj67, obj84). El error en esos
casos es de ±180° o "cualquier ángulo", no de unos pocos grados, y la mayoría se envía a REVIEW por
`rotational_ambiguity` o `high_angle_uncertainty`. Los errores de clase se dan entre objetos de la misma familia
visual (pares casi duplicados de la sección 2). En B el fallo dominante es estructural (arco no visto).
""")

md("## 7. Costo: latencia y tamaño del artefacto (clásico vs. referencia moderna)")

code("""
import subprocess
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "train_vision.py")], capture_output=True, text=True, cwd=ROOT)
print(r.stdout[-1500:]); print(r.stderr[-800:] if r.returncode else "")
from src.vision.inference import VisionPredictor
pred = VisionPredictor.load(ROOT / "models" / "vision-1.0.0")
rng = np.random.default_rng(42); sample = ds.images[rng.choice(len(ds.images), 100, replace=False)]
lat = {"classic (pipeline completo, 1 imagen)": va.measure_latency(pred, sample)}
lat["baseline píxeles 1-NN (media/imagen en lote)"] = {"mean_ms": R["baselines"]["A"]["mean_ms_per_image_in_batch"]}
rl = va.measure_resnet_latency(sample, n=50)
lat["resnet18 CPU (solo embedding, 1 imagen)"] = rl["cpu"]
if "mps" in rl: lat["resnet18 MPS (solo embedding, 1 imagen)"] = rl["mps"]
display(pd.DataFrame(lat).T.round(2))
size_mb = sum(f.stat().st_size for f in (ROOT / "models" / "vision-1.0.0").glob("*") if f.is_file()) / 1e6
print(f"Artefacto clásico: {size_mb:.1f} MB | pesos ResNet18 fp32: {rl['weights_mb_fp32']} MB (+ torch/torchvision)")
fig = va.latency_figure(lat); va.savefig(fig, "08_latency"); plt.show()
COST = {"latency": lat, "classic_artifact_mb": size_mb, "resnet18_weights_mb": rl["weights_mb_fp32"]}
""")

md("""
**¿Justifica la brecha el costo?** Con la misma receta, ResNet18 mejora la exactitud de clase en los protocolos
con brecha (B, C) y el MAE angular, pero no resuelve el arco ciego ni habilita automatizar C; en A/D la comparación
de fracción automatizada y error entre ACT está en la tabla de la sección 5. Solo el embedding de ResNet18 ya
cuesta más en CPU que el pipeline clásico completo, añade ~45 MB de pesos más la dependencia de PyTorch y es menos
interpretable. **Producción = receta clásica**; ResNet18 queda como referencia y candidata si el piloto en planta
muestra una brecha mayor (en particular en desconocidos casi duplicados).
""")

md("## 8. Exportación del artefacto y registro")

code("""
results["production_selection"] = {"clf": prod["clf"], "params": prod["params"], "criterion": "max exactitud media de validación A–D (exploración)"}
results["production_selection_scores"] = prod["mean_val"]
results["recipe"] = vexp.to_jsonable(R)
results["recipe_summary"] = vexp.to_jsonable(RS)
results["exploration_summary"] = va.build_summary(results)
results["cost"] = COST
results["symmetry"] = {"profile_score": {f"obj{k}": v for k, v in sym["profile_score"].items()},
                       "loo_score": {f"obj{k}": v for k, v in sym["loo_score"].items()}}
results["confusable_pairs"] = pairs
results["error_analysis"] = fails
p = va.write_results(vexp.to_jsonable(results)); print("escrito", p)
# re-exporta para que metadata.json lleve el resumen de la receta
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "train_vision.py")], capture_output=True, text=True, cwd=ROOT)
print(r.stdout[-1200:])
meta = json.loads((ROOT / "models" / "vision-1.0.0" / "metadata.json").read_text())
print({k: meta[k] for k in ("version", "classifier", "k_angle_refine", "thresholds", "symmetric_classes", "artifact_size_mb", "model_sha256", "git")})
print(f"Tiempo total del notebook: {(time.time() - T0) / 60:.1f} min")
""")

md("""
## 9. Conclusiones

* **Receta productiva:** características clásicas (HSV + HOG + LBP + Hu) → PCA(150) → regresión logística C=1;
  ángulo por recuperación condicionada al objeto con un score de incertidumbre en grados; decisión
  ACT/REVIEW/REJECT con umbrales fijados en validación. El artefacto `vision-1.0.0` es esta receta ajustada con
  las 7.200 vistas: sus métricas esperadas son las de la receta en los protocolos (sección 5), no un test propio.
* **Protocolos:** A y D (variante) son automatizables con error entre ACT bajo (ver IC de Wilson en la tabla 5);
  B (arco ciego) y C (12 vistas/objeto) **no** lo son: la validación no aporta evidencia suficiente y la receta
  desactiva la automatización (todo a REVIEW). En D estricto una fracción pequeña de desconocidos llega a ACT
  (casi duplicados). Para dar de alta un SKU: **≥ 12 vistas cubriendo ≥ 300°** para staging, y referencias densas
  (idealmente cada 5–10°) para habilitar ACT.
* **Limitaciones:** ver la model card (`docs/model_card.md`) — dataset de laboratorio, un solo eje de rotación,
  sin oclusión ni variación de fondo/iluminación; requiere piloto en sombra y recalibración en planta. Ninguna
  regla de decisión *garantiza* acierto: reduce el riesgo a la tasa medida.
""")

nb = nbf.v4.new_notebook()
nb["cells"] = cells
nb["metadata"]["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
out = ROOT / "notebooks" / "01_experimentacion.ipynb"
out.parent.mkdir(parents=True, exist_ok=True)
nbf.write(nb, out)
print("notebook escrito en", out)
