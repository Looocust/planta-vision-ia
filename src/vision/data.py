"""Carga del dataset COIL-100, protocolos de evaluación y hash de versión de datos."""
from __future__ import annotations

import hashlib
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

SEED = 42
COIL_URL = "https://www.cs.columbia.edu/CAVE/databases/SLAM_coil-20_coil-100/coil-100/coil-100.zip"
COIL_SHA256 = "fcb4b8b01c0778f416d8d7a09f45082c4f7075cc74743dcddd5c44307bd4afe7"
N_OBJECTS = 100
ANGLES = np.arange(0, 360, 5)
_NAME_RE = re.compile(r"obj(\d+)__(\d+)\.png$")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "raw" / "coil-100"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def ensure_dataset(data_dir: Path = DEFAULT_DATA_DIR, zip_path: Path | None = None) -> Path:
    """Descarga (si falta) el zip oficial, verifica SHA-256 y lo extrae. Devuelve la carpeta de imágenes."""
    data_dir = Path(data_dir)
    if data_dir.exists() and len(list(data_dir.glob("obj*__*.png"))) == 7200:
        return data_dir
    zip_path = Path(zip_path or data_dir.parent / "coil-100.zip")
    if not zip_path.exists():
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(COIL_URL, zip_path)
    digest = sha256_file(zip_path)
    if digest != COIL_SHA256:
        raise ValueError(f"SHA-256 inesperado para {zip_path}: {digest}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(data_dir.parent)
    return data_dir


@dataclass
class Dataset:
    images: np.ndarray  # (N, 128, 128, 3) uint8 RGB
    labels: np.ndarray  # (N,) int, 1..100
    angles: np.ndarray  # (N,) int degrees
    image_ids: np.ndarray  # (N,) str "objN__A"

    def __len__(self) -> int:
        return len(self.labels)

    def subset(self, idx: np.ndarray) -> "Dataset":
        return Dataset(self.images[idx], self.labels[idx], self.angles[idx], self.image_ids[idx])


def parse_name(name: str) -> tuple[int, int]:
    m = _NAME_RE.search(name)
    if not m:
        raise ValueError(name)
    return int(m.group(1)), int(m.group(2))


def load_dataset(data_dir: Path = DEFAULT_DATA_DIR, cache: Path | None = None) -> Dataset:
    """Carga todas las imágenes (orden determinista: objeto, ángulo)."""
    data_dir = Path(data_dir)
    if cache is not None and Path(cache).exists():
        z = np.load(cache, allow_pickle=False)
        return Dataset(z["images"], z["labels"], z["angles"], z["image_ids"])
    files = sorted(data_dir.glob("obj*__*.png"), key=lambda p: parse_name(p.name))
    labels, angles, ids, imgs = [], [], [], []
    for p in files:
        o, a = parse_name(p.name)
        labels.append(o)
        angles.append(a)
        ids.append(f"obj{o}__{a}")
        imgs.append(np.asarray(Image.open(p).convert("RGB")))
    ds = Dataset(np.stack(imgs), np.array(labels), np.array(angles), np.array(ids))
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, images=ds.images, labels=ds.labels, angles=ds.angles, image_ids=ds.image_ids)
    return ds


def data_version_hash(data_dir: Path = DEFAULT_DATA_DIR) -> str:
    """Hash estable del contenido: SHA-256 sobre (nombre, sha256 de cada archivo) ordenado."""
    h = hashlib.sha256()
    for p in sorted(Path(data_dir).glob("obj*__*.png"), key=lambda p: parse_name(p.name)):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


# ---------------------------------------------------------------- protocolos

def blind_arc_starts(seed: int = SEED, n_objects: int = N_OBJECTS) -> dict[int, int]:
    """Inicio del arco ciego de 60° por objeto (múltiplo de 5), aleatorio con semilla fija."""
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, 72, size=n_objects) * 5
    return {o + 1: int(s) for o, s in enumerate(starts)}


def in_arc(angles: np.ndarray, start: int | np.ndarray, width: int = 60) -> np.ndarray:
    """True si el ángulo cae en [start, start+width) (circular)."""
    return ((np.asarray(angles) - start) % 360) < width


def unknown_objects(seed: int = SEED, n_unknown: int = 10) -> list[int]:
    rng = np.random.default_rng(seed)
    return sorted(int(x) for x in rng.choice(np.arange(1, N_OBJECTS + 1), size=n_unknown, replace=False))


def protocol_split(labels: np.ndarray, angles: np.ndarray, protocol: str, seed: int = SEED) -> dict:
    """Devuelve índices train/test (y, para D, máscara de desconocidos) para el protocolo A|B|C|D.

    A: train ángulos múltiplos de 10, test los restantes (36/36 por objeto).
    B: test = arco contiguo de 60° (12 vistas) por objeto con inicio aleatorio (semilla 42); train = 60 vistas.
    C: train 1 vista cada 30° (12/obj), test las otras 60.
    D: 90 objetos conocidos (train = ángulos múltiplos de 10, para poder medir el lado "conocido"),
       test = vistas impares de los 90 conocidos + TODAS las vistas de los 10 desconocidos.
    """
    labels = np.asarray(labels)
    angles = np.asarray(angles)
    out: dict = {"protocol": protocol}
    if protocol == "A":
        tr = angles % 10 == 0
    elif protocol == "B":
        starts = blind_arc_starts(seed)
        st = np.array([starts[int(l)] for l in labels])
        tr = ~in_arc(angles, st)
        out["arc_starts"] = starts
    elif protocol == "C":
        tr = angles % 30 == 0
    elif protocol == "D":
        unk = unknown_objects(seed)
        is_unk = np.isin(labels, unk)
        tr = (~is_unk) & (angles % 10 == 0)
        te = is_unk | ((~is_unk) & (angles % 10 != 0))
        out.update(train=np.where(tr)[0], test=np.where(te)[0], unknown_objects=unk,
                   test_is_unknown=is_unk[te])
        return out
    else:
        raise ValueError(protocol)
    out.update(train=np.where(tr)[0], test=np.where(~tr)[0])
    return out


def validation_split(labels: np.ndarray, angles: np.ndarray, protocol: str, seed: int = SEED) -> dict:
    """Partición de VALIDACIÓN construida solo con las vistas de entrenamiento de cada protocolo.

    Imita la brecha angular del protocolo (más exigente que test):
    A: dentro de train (paso 10°) → fit en paso 20°, val en el resto (brecha 10° vs 5° en test).
    B: dentro de las 60 vistas de train → se oculta otro arco contiguo de 60° (fit 48 / val 12).
    C: dentro de las 12 vistas (paso 30°) → fit paso 60°, val el resto (brecha 30° vs ≤15° en test).
    D: dentro de los 90 conocidos → 10 objetos pseudo-desconocidos (semilla 43), conocidos como A.
    Los índices devueltos son posiciones sobre el arreglo completo.
    """
    sp = protocol_split(labels, angles, protocol, seed)
    tr = sp["train"]
    L, Ang = np.asarray(labels)[tr], np.asarray(angles)[tr]
    out: dict = {"protocol": protocol}
    if protocol == "A":
        fit = Ang % 20 == 0
    elif protocol == "B":
        test_starts = sp["arc_starts"]
        rng = np.random.default_rng(seed + 1)
        fit = np.ones(len(tr), bool)
        for o in np.unique(L):
            # arco de validación: empieza justo después del arco de test + desplazamiento aleatorio,
            # restringido a caer completo fuera del arco de test
            s0 = (test_starts[int(o)] + 60 + int(rng.integers(0, 49)) * 5) % 360
            m = L == o
            fit[m] = ~in_arc(Ang[m], s0)
    elif protocol == "C":
        fit = Ang % 60 == 0
    elif protocol == "D":
        known = np.unique(L)
        rng = np.random.default_rng(seed + 1)
        pseudo = sorted(int(x) for x in rng.choice(known, size=10, replace=False))
        is_ps = np.isin(L, pseudo)
        fit = (~is_ps) & (Ang % 20 == 0)
        val = is_ps | ((~is_ps) & (Ang % 20 != 0))
        out.update(fit=tr[fit], val=tr[val], pseudo_unknown=pseudo, val_is_unknown=is_ps[val])
        return out
    else:
        raise ValueError(protocol)
    out.update(fit=tr[fit], val=tr[~fit])
    return out


def production_validation_split(labels: np.ndarray, angles: np.ndarray, seed: int = SEED) -> dict:
    """Validación para fijar los umbrales del modelo productivo (que se entrena con TODO).

    10 objetos pseudo-desconocidos (semilla 43); fit = vistas múltiplo de 10° de los otros 90;
    val = vistas impares de los 90 + todas las vistas de los 10 pseudo-desconocidos.
    """
    labels = np.asarray(labels)
    angles = np.asarray(angles)
    rng = np.random.default_rng(seed + 1)
    pseudo = sorted(int(x) for x in rng.choice(np.arange(1, N_OBJECTS + 1), size=10, replace=False))
    is_ps = np.isin(labels, pseudo)
    fit = (~is_ps) & (angles % 10 == 0)
    val = is_ps | ((~is_ps) & (angles % 10 != 0))
    return {"fit": np.where(fit)[0], "val": np.where(val)[0], "pseudo_unknown": pseudo, "val_is_unknown": is_ps[val]}
