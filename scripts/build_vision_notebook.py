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
Hay dos niveles: (i) **selección anidada por protocolo** (sección 4): familia de clasificador, hiperparámetros,
k angular y umbrales se eligen solo con la validación interna del train de ese protocolo y se evalúa una vez en
su test — son las únicas cifras que presentamos como estimación independiente; (ii) la **configuración
desplegada** (sección 5, receta congelada en `src/vision/recipe.py`), cuya familia/C se eligió con validaciones
agregadas de los cuatro protocolos que, en conjunto, se solapan con tests de otros protocolos: sus cifras se
reportan con esa salvedad.
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
## 4. Selección anidada por protocolo (cifras independientes)

**Preprocesado** (`features.preprocess`, igual en entrenamiento e inferencia): mediana 3×3, máscara de objeto
sobre el gris suavizado (Gauss 5×5, > 40) con apertura 3×3, y fondo puesto a 0. Motivo: en la prueba de punta a
punta, ruido gaussiano de sensor casi imperceptible (σ=1–4) sobre el fondo creaba textura que HOG/LBP/histograma
captaban y disparaba la distancia de novedad (REJECT por "desconocido").

**Aumentación** (solo vistas de entrenamiento): 1 copia por vista con ruido σ~U(0,6), brillo ×U(0.8,1.2) y blur
leve (p=0.3). La **validación** usa sus vistas limpias + 1 copia perturbada con la misma distribución, para que
los umbrales reflejen condiciones realistas. Las vistas de test nunca se perturban para entrenar ni validar.

**Características:** histograma HSV 16×4×4 (Hellinger) sobre la máscara, HOG 64×64, LBP uniforme 2×2 y Hu;
estandarización + PCA(150) ajustados solo con train.

**Candidatos** (6): logística C∈{0.1,1,10}, SVM RBF C=10 (Platt), kNN k=1, Random Forest 300. Para cada uno, en
la validación interna del protocolo: k angular ∈{1,2,3}, clases de ángulo poco fiable, y umbrales. Se elige el
candidato con automatización habilitada y mayor cobertura de validación (desempate: exactitud de validación).

**Ángulo e incertidumbre:** recuperación condicionada a la clase predicha; el *score* de incertidumbre en grados
traduce la distancia al vecino más cercano en "grados de giro equivalentes" con la curva distancia-vs-separación
del objeto. **No es una cota del error**; su umbral se elige por el riesgo empírico.

**Reglas para habilitar ACT (en validación):** error entre ACT ≤ 2 % con ≥ 50 ACT; y, si hay pseudo-desconocidos
(≥ 100 vistas), que como mucho el 5 % de ellos llegue a ACT. Si no se cumple → automatización desactivada:
política efectiva 0 ACT, riesgo `null` (las cifras del candidato descartado se guardan aparte).
""")

code("""
from src.vision import recipe as vrec
OUT_DIR = ROOT / "data" / "runtime" / "experiments"
t = time.time()
R = vrec.run_recipe_all(data, features=("classic", "resnet18"), out_dir=OUT_DIR)
print(f"selección anidada + configuración desplegada + robustez: {time.time() - t:.0f}s")
RS = va.build_recipe_summary(R["results"], R["baselines"], R["nested"])
def fmt_ci(x):
    return "N/A" if x is None or x[0] is None else f"[{x[0]:.2%}, {x[1]:.2%}]"
def rowfmt(p, lab, m):
    return {"protocolo": p, "modelo": lab, "exactitud": m.get("class_accuracy"), "MAE°": m.get("angle_mae_deg"),
            "éxito e2e": m.get("end_to_end_success"), "% ACT": m["coverage_act"], "n ACT": m.get("n_act"),
            "error|ACT": "N/A" if m["error_among_act"] is None else round(m["error_among_act"], 4),
            "IC95": fmt_ci(m.get("error_among_act_wilson95")), "automatización": m.get("automation_enabled", "—")}
nrows = [{**rowfmt(p, RS[p]["nested_classic"]["selected_candidate"], RS[p]["nested_classic"]), "k": RS[p]["nested_classic"]["k"]} for p in RS]
print("CIFRAS INDEPENDIENTES (selección anidada, test evaluado una vez):"); display(pd.DataFrame(nrows))
for p in RS:
    print(f"Validación interna del protocolo {p} (candidatos):")
    display(pd.DataFrame(R["nested"][p]["selection_table_val"]).round(4))
""")

md("""
**Calibración (solo validación):** la columna `val_ece` muestra que la logística está bien calibrada en todos los
protocolos, mientras que SVM (Platt multiclase) y Random Forest tienen ECE alto: sus probabilidades crudas no
sirven como confianza. En A la selección anidada puede preferir otro candidato si cubre más en validación; la
tabla deja ver ese trade-off.
""")

md("""
## 5. Configuración desplegada (receta congelada)

Receta `classic-logreg-v1`: preprocesado + aumentación + PCA(150) + logística C=1 + el mismo procedimiento de
k/umbrales. **Salvedad:** familia y C se fijaron con la exploración inicial, que promedió validaciones de A–D; como
los cuatro protocolos cubren conjuntamente todas las imágenes, esa elección tocó imágenes que son test de otros
protocolos. Por eso estas cifras **no** se presentan como independientes; se comparan con las anidadas (arriba),
con la línea base y con la misma configuración sobre embeddings ResNet18 (sin aumentación).
""")

code("""
hrows = []
for p, row in RS.items():
    for key, lab in (("nested_classic", "Selección anidada (independiente)"), ("recipe_classic", "Config. desplegada (clásica)"),
                     ("baseline_raw_1nn", "Base píxeles 1-NN (siempre ACT)"), ("recipe_resnet18", "Config. desplegada con ResNet18")):
        if row.get(key): hrows.append(rowfmt(p, lab, row[key]))
display(pd.DataFrame(hrows))
thr = pd.DataFrame({p: {**R["results"]["classic"][p]["thresholds"], "k": R["results"]["classic"][p]["k_selected_on_val"],
                        "val n ACT": R["results"]["classic"][p]["threshold_validation"]["val_n_act"],
                        "val error|ACT": R["results"]["classic"][p]["threshold_validation"]["val_error_among_act"],
                        "val desconocidos→ACT": R["results"]["classic"][p]["threshold_validation"]["unknown_false_accept_val"]["rate"]}
                    for p in RS}).T
print("Umbrales de la configuración desplegada por protocolo (elegidos en validación):"); display(thr)
fig = va.recipe_bars(RS); va.savefig(fig, "03_per_protocol"); plt.show()
""")

md("""
`error|ACT` = P(clase incorrecta, desconocido aceptado o |error| > 10° | el robot actúa), con IC de Wilson 95 %.
La línea base siempre actúa: en D sus aciertos sobre desconocidos son imposibles y cuentan como error. "N/A" =
política efectiva sin ninguna acción (automatización desactivada).
""")

md("""
### 5.1 Robustez a perturbaciones

El modelo ya ajustado (mismos umbrales) se re-evalúa sobre el test perturbado de A (conocidos) y D estricto
(desconocidos): ruido gaussiano σ=2/4/8, brillo ×0.7/×1.3 y blur gaussiano σ=1 (semillas fijas). σ=8 y ×0.7/×1.3
están **fuera** del rango de aumentación (σ≤6, ×0.8–1.2): miden extrapolación.
""")

code("""
for p in ("A", "D_strict"):
    print(f"Robustez — protocolo {p}")
    display(pd.DataFrame(va.robustness_table(R["results"]["classic"][p]["robustness"])).round(4))
""")

md("""
**Lectura honesta.** Con el preprocesado y la aumentación, el ruido de sensor moderado (σ≤4) y el blur leve
apenas cambian la exactitud y la fracción automatizada. Las perturbaciones fuera del rango entrenado (σ=8, brillo
×0.7/×1.3) degradan el ángulo (MAE mayor) y la cobertura; el riesgo entre ACT observado sube y puede superar el
objetivo de 2 % (ver IC). La tasa de desconocidos que llegan a ACT se mantiene en el mismo orden bajo
perturbación, pero **no** es cero: los casi duplicados de un conocido pasan igual. En planta, cambios de
iluminación mayores a ±20 % exigen recalibrar o ampliar la aumentación, y la compuerta de calidad debe vigilar
brillo y nitidez.
""")

md("### 5.2 Ángulo: error circular y elección de k en validación")

code("""
outs = {p: va.load_outputs("recipe_classic", p, OUT_DIR) for p in ("A", "B", "C", "D")}
fig = va.angle_error_hist({p: outs[p] for p in "ABC"}); va.savefig(fig, "04_angle_error_hist"); plt.show()
kt = pd.DataFrame([{"protocolo": p, **r} for p in ("A", "B", "C", "D", "D_strict") for r in R["results"]["classic"][p]["k_table_val"]])
display(kt.pivot_table(index="k", columns="protocolo", values=["val_within_10", "val_mae_deg"]).round(3))
print("k elegido en validación:", {p: R["results"]["classic"][p]["k_selected_on_val"] for p in RS})
""")

md("""
**Lectura.** Métrica: error circular `min(|a−b|, 360−|a−b|)`. La tabla de k es de **validación**: interpolar con
2–3 vecinos ayuda cuando el query cae entre referencias cercanas (A, D) y no con brechas grandes (B, C), donde
k=1 queda igual o mejor; por eso k se elige por protocolo en validación. En **B** (arco ciego) ningún método de
apariencia reconstruye las vistas no vistas; en **C** (12 vistas) el ángulo no alcanza la confiabilidad exigida.
""")

md("### 5.3 Confianza: calibración, riesgo–cobertura y desconocidos")

code("""
fig = va.reliability_and_risk(outs); va.savefig(fig, "05_reliability_risk"); plt.show()
fig = va.unknown_roc(outs["D"]); va.savefig(fig, "06_unknown_roc"); plt.show()
for p in ("D", "D_strict"):
    d = R["results"]["classic"][p]["decision"]
    print(p, {k: (round(v["rate"], 4), [round(x, 4) for x in v["wilson95"]]) for k, v in d.items() if k in ("unknown_rejected", "unknown_reaches_act")},
          "| criterio en validación:", R["results"]["classic"][p]["threshold_validation"]["unknown_false_accept_val"])
""")

md("""
(1) La logística está bien calibrada (diagrama de confiabilidad). (2) En riesgo–cobertura, priorizar mayor
confianza o menor incertidumbre deja el riesgo de los primeros tramos por debajo del global; las curvas **no**
son estrictamente monótonas y en B no hay tramo con riesgo aceptable. (3) Criterio explícito de desconocidos:
≤ 5 % de pseudo-desconocidos de validación pueden llegar a ACT (con ≥ 100 vistas); el valor observado en test
(D estricto) se reporta con IC. Los desconocidos que llegan a ACT son casi siempre casi duplicados de un conocido
(p. ej. obj61 ≈ obj92).
""")

md("""
### 5.4 Umbral de incertidumbre vs. tolerancia ±10°

El umbral (p. ej. 30°) es un corte sobre un score heurístico, no "error ≤ 30°". Se elige como el mayor umbral cuyo
riesgo observado en validación `P(clase incorrecta o |error| > 10° | ACT)` es ≤ 2 % con ≥ 50 ACT. La figura muestra
ese riesgo con IC de Wilson en validación (selección) y en test (descriptivo). Lo que se observa es que, una vez
aplicados la ambigüedad rotacional y la confianza, subir el corte por encima de ~10–15° añade pocos ACT y cambia
poco el riesgo observado en A; en D el riesgo observado crece con el corte. No es una garantía fuera de estos datos.
""")

code("""
fig = va.sweep_figure(R["results"]["classic"], ("A", "D")); va.savefig(fig, "09_uncertainty_sweep"); plt.show()
""")

md("## 6. Análisis de errores (configuración desplegada)")

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
**Lectura.** Los fallos angulares se concentran en pocos objetos: donas y taza (obj95, obj94, obj47; índice de
perfil alto) y vistas "gemelas" a 180° (obj98, obj67, obj84; índice LOO alto). El error en esos casos es de ±180°
o arbitrario; la mayoría se envía a REVIEW. Los errores de clase se dan entre objetos de la misma familia visual.
""")

md("## 7. Costo: latencia y tamaño del artefacto")

code("""
import subprocess
VERSION = "vision-1.0.1"
# --force: regenera la MISMA versión aún no publicada desde este notebook (las versiones publicadas no se tocan)
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "train_vision.py"), "--force"], capture_output=True, text=True, cwd=ROOT)
print(r.stdout[-1500:]); print(r.stderr[-800:] if r.returncode else "")
from src.vision.inference import VisionPredictor
pred = VisionPredictor.load(ROOT / "models" / VERSION)
rng = np.random.default_rng(42); sample = ds.images[rng.choice(len(ds.images), 100, replace=False)]
lat = {"classic (pipeline completo, 1 imagen)": va.measure_latency(pred, sample)}
lat["baseline píxeles 1-NN (media/imagen en lote)"] = {"mean_ms": R["baselines"]["A"]["mean_ms_per_image_in_batch"]}
rl = va.measure_resnet_latency(sample, n=50)
lat["resnet18 CPU (solo embedding, 1 imagen)"] = rl["cpu"]
if "mps" in rl: lat["resnet18 MPS (solo embedding, 1 imagen)"] = rl["mps"]
display(pd.DataFrame(lat).T.round(2))
size_mb = sum(f.stat().st_size for f in (ROOT / "models" / VERSION).glob("*") if f.is_file()) / 1e6
print(f"Artefacto clásico (incluye code_snapshot): {size_mb:.1f} MB | pesos ResNet18 fp32: {rl['weights_mb_fp32']} MB (+ torch)")
fig = va.latency_figure(lat); va.savefig(fig, "08_latency"); plt.show()
COST = {"latency": lat, "classic_artifact_mb": size_mb, "resnet18_weights_mb": rl["weights_mb_fp32"]}
""")

md("""
**¿Justifica la brecha el costo?** Con la misma configuración, ResNet18 (sin aumentación) clasifica mejor en B/C,
pero tampoco habilita automatizar B ni C y en su validación no alcanza el criterio de riesgo en varios
protocolos; solo su embedding ya cuesta más en CPU que el pipeline clásico completo y añade ~45 MB de pesos más
PyTorch. **Producción = clásico**; ResNet18 queda como referencia.
""")

md("## 8. Exportación del artefacto y registro")

code("""
results = {"data_version_hash": DATA_HASH, "seed": 42, "feature_config": data["feature_cfg"].to_dict()}
results["recipe"] = vexp.to_jsonable(R)
results["recipe_summary"] = vexp.to_jsonable(RS)
results["cost"] = COST
results["symmetry"] = {"profile_score": {f"obj{k}": v for k, v in sym["profile_score"].items()},
                       "loo_score": {f"obj{k}": v for k, v in sym["loo_score"].items()}}
results["confusable_pairs"] = pairs
results["error_analysis"] = fails
results["protocols"] = {"D": {"classic": {"unknown_objects": [f"obj{o}" for o in spD["unknown_objects"]]}}}
p = va.write_results(vexp.to_jsonable(results)); print("escrito", p)
json.loads(p.read_text())  # JSON estricto (sin NaN)
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "train_vision.py"), "--force"], capture_output=True, text=True, cwd=ROOT)
print(r.stdout[-1200:])
meta = json.loads((ROOT / "models" / VERSION / "metadata.json").read_text())
print({k: meta[k] for k in ("version", "classifier", "k_angle_refine", "thresholds", "symmetric_classes", "artifact_size_mb",
                            "model_sha256", "code_snapshot_sha256", "git")})
print(f"Tiempo total del notebook: {(time.time() - T0) / 60:.1f} min")
""")

md("""
## 9. Conclusiones

* **Cifras independientes** = selección anidada por protocolo (sección 4). La configuración desplegada
  (`vision-1.0.1`, logística C=1) se reporta aparte con su salvedad de selección.
* **Robustez:** el preprocesado (mediana + supresión de fondo) y la aumentación eliminan la fragilidad ante ruido
  de sensor moderado; perturbaciones fuera del rango entrenado siguen degradando ángulo y cobertura (sección 5.1).
* **Protocolos:** A y D son automatizables con riesgo observado bajo (IC en tablas); B y C no: la política efectiva
  es 0 ACT (todo a REVIEW). Para dar de alta un SKU: ≥ 12 vistas cubriendo ≥ 300° para staging y referencias densas
  (cada 5–10°) para habilitar ACT.
* **Limitaciones:** dataset de laboratorio; desconocidos casi duplicados pueden llegar a ACT; el score angular es
  heurístico. Ninguna regla de decisión garantiza acierto: el riesgo es el observado en estos datos.
""")

nb = nbf.v4.new_notebook()
nb["cells"] = cells
nb["metadata"]["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
out = ROOT / "notebooks" / "01_experimentacion.ipynb"
out.parent.mkdir(parents=True, exist_ok=True)
nbf.write(nb, out)
print("notebook escrito en", out)
