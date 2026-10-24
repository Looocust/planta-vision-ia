"""Genera imágenes de demo a partir de COIL-100 para mostrar cada decisión del sistema.

Las imágenes llevan perturbaciones (ruido, brillo, desenfoque, mezcla) para que no sean
copias exactas de las vistas de referencia del modelo.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

SRC = Path("data/raw/coil-100")
OUT = Path("demo/imagenes")
rng = np.random.default_rng(42)


def load(name: str) -> np.ndarray:
    return np.asarray(Image.open(SRC / f"{name}.png").convert("RGB"), dtype=np.float32)


def save(arr: np.ndarray, name: str) -> None:
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(OUT / name)


OUT.mkdir(parents=True, exist_ok=True)
# 1. Objeto conocido con ruido de sensor leve -> se espera ACT
save(load("obj5__35") + rng.normal(0, 4, (128, 128, 3)), "01_conocido_ruido_obj5_35.png")
# 2. Vista intermedia entre referencias (mezcla de 30° y 40°) -> ángulo interpolado
save(0.5 * load("obj23__30") + 0.5 * load("obj23__40"), "02_vista_intermedia_obj23_35.png")
# 3. Objeto rotacionalmente simétrico -> se espera REVIEW (rotational_ambiguity)
save(load("obj47__120") + rng.normal(0, 3, (128, 128, 3)), "03_simetrico_obj47.png")
# 4. Imagen muy oscura -> se espera REJECT (image_quality)
save(load("obj12__90") * 0.08, "04_oscura_obj12.png")
# 5. Imagen desenfocada -> se espera REJECT o REVIEW
blur = Image.open(SRC / "obj30__180.png").convert("RGB").filter(ImageFilter.GaussianBlur(6))
blur.save(OUT / "05_borrosa_obj30.png")
# 6. Objeto "desconocido": superposición de dos productos distintos -> fuera de dominio
save(0.5 * load("obj3__0") + 0.5 * load("obj77__200"), "06_desconocido_mezcla.png")
# 7. Patrón sintético fuera de dominio
yy, xx = np.mgrid[0:128, 0:128]
pattern = np.stack([(xx * 2) % 256, (yy * 2) % 256, ((xx + yy) * 3) % 256], -1).astype(np.float32)
save(pattern, "07_fuera_de_dominio_patron.png")
print("ok", sorted(p.name for p in OUT.iterdir()))
