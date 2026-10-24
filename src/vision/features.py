"""Extracción de características clásicas (sin aprendizaje profundo).

Bloques:
- color: histograma HSV conjunto (H×S×V) sobre píxeles de objeto (máscara por brillo) → invariante a la rotación
  en el plano y muy discriminativo para la clase.
- hog: HOG en escala de grises 64×64 → sensible a la orientación/forma; es el bloque que informa el ángulo.
- lbp: histogramas LBP uniformes (P=8, R=1) en rejilla 2×2 → textura.
- hu: momentos de Hu (log) de la silueta → forma global.
Cada bloque se normaliza (L1/L2) y se pondera para que ningún bloque domine por número de dimensiones.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np
from skimage.feature import hog, local_binary_pattern


@dataclass(frozen=True)
class FeatureConfig:
    size: int = 128  # tamaño canónico de entrada
    hog_size: int = 64
    hog_orientations: int = 9
    hog_ppc: int = 8
    hog_cpb: int = 2
    h_bins: int = 16
    s_bins: int = 4
    v_bins: int = 4
    lbp_grid: int = 2
    mask_threshold: int = 40  # gris (0-255) > umbral → "objeto" (el fondo de COIL no es 0: mediana ≈25)
    w_color: float = 1.0
    w_hog: float = 1.0
    w_lbp: float = 0.5
    w_hu: float = 0.25
    blocks: tuple[str, ...] = ("color", "hog", "lbp", "hu")
    denoise_ksize: int = 3  # mediana k×k antes de todo (0 = sin filtro): robustez a ruido de sensor
    suppress_background: bool = True  # pone a 0 el fondo (máscara sobre gris suavizado + apertura)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["blocks"] = list(self.blocks)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FeatureConfig":
        d = dict(d)
        # artefactos anteriores a 1.0.1 no tenían preprocesado: se reproducen tal cual se entrenaron
        d.setdefault("denoise_ksize", 0)
        d.setdefault("suppress_background", False)
        if "blocks" in d:
            d["blocks"] = tuple(d["blocks"])
        return cls(**d)


def to_canonical(image_rgb: np.ndarray, size: int = 128) -> np.ndarray:
    """Asegura RGB uint8 de tamaño size×size."""
    img = np.asarray(image_rgb)
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    if img.shape[-1] == 4:
        img = img[..., :3]
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    if img.shape[0] != size or img.shape[1] != size:
        img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    return img


def _color_hist(img: np.ndarray, mask: np.ndarray, cfg: FeatureConfig) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    m = mask.astype(np.uint8)
    if m.sum() < 10:
        m = None
    h = cv2.calcHist([hsv], [0, 1, 2], m, [cfg.h_bins, cfg.s_bins, cfg.v_bins], [0, 180, 0, 256, 0, 256])
    h = h.ravel().astype(np.float32)
    h /= h.sum() + 1e-8
    return np.sqrt(h)  # Hellinger: distancias euclídeas ≈ distancia de Bhattacharyya


def _hog(gray: np.ndarray, cfg: FeatureConfig) -> np.ndarray:
    g = cv2.resize(gray, (cfg.hog_size, cfg.hog_size), interpolation=cv2.INTER_AREA)
    f = hog(g, orientations=cfg.hog_orientations, pixels_per_cell=(cfg.hog_ppc, cfg.hog_ppc),
            cells_per_block=(cfg.hog_cpb, cfg.hog_cpb), block_norm="L2-Hys", feature_vector=True)
    f = f.astype(np.float32)
    return f / (np.linalg.norm(f) + 1e-8)


def _lbp(gray: np.ndarray, mask: np.ndarray, cfg: FeatureConfig) -> np.ndarray:
    lbp = local_binary_pattern(gray, P=8, R=1, method="uniform")  # valores 0..9
    n = cfg.lbp_grid
    H, W = gray.shape
    feats = []
    for i in range(n):
        for j in range(n):
            sl = (slice(i * H // n, (i + 1) * H // n), slice(j * W // n, (j + 1) * W // n))
            v = lbp[sl][mask[sl]]
            h = np.bincount(v.astype(int), minlength=10)[:10].astype(np.float32)
            h /= h.sum() + 1e-8
            feats.append(np.sqrt(h))
    f = np.concatenate(feats)
    return f / (np.linalg.norm(f) + 1e-8)


def _hu(mask: np.ndarray) -> np.ndarray:
    m = cv2.moments(mask.astype(np.uint8), binaryImage=True)
    hu = cv2.HuMoments(m).ravel()
    hu = -np.sign(hu) * np.log10(np.abs(hu) + 1e-12)
    return (hu / 10.0).astype(np.float32)


def preprocess(image_rgb: np.ndarray, cfg: FeatureConfig = FeatureConfig()) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalización robusta a ruido de sensor, idéntica en entrenamiento e inferencia.

    1) tamaño canónico; 2) mediana k×k (elimina ruido impulsivo/gaussiano leve sin borrar bordes);
    3) máscara de objeto sobre el gris suavizado (Gauss 5×5) + apertura morfológica (quita motas);
    4) supresión de fondo: los píxeles fuera de la máscara se ponen a 0, así el ruido del fondo no crea
       textura para HOG/LBP ni masa para el histograma de color.
    """
    img = to_canonical(image_rgb, cfg.size)
    if cfg.denoise_ksize and cfg.denoise_ksize > 1:
        img = cv2.medianBlur(img, cfg.denoise_ksize)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    if not cfg.suppress_background and not cfg.denoise_ksize:  # modo legado (≤ 1.0.0): máscara directa
        return img, gray, gray > cfg.mask_threshold
    smooth = cv2.GaussianBlur(gray, (5, 5), 0)
    mask = (smooth > cfg.mask_threshold).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
    if cfg.suppress_background:
        img = img * mask[..., None].astype(np.uint8)
        gray = gray * mask.astype(np.uint8)
    return img, gray, mask


def extract_features(image_rgb: np.ndarray, cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    img, gray, mask = preprocess(image_rgb, cfg)
    parts = []
    if "color" in cfg.blocks:
        parts.append(cfg.w_color * _color_hist(img, mask, cfg))
    if "hog" in cfg.blocks:
        parts.append(cfg.w_hog * _hog(gray, cfg))
    if "lbp" in cfg.blocks:
        parts.append(cfg.w_lbp * _lbp(gray, mask, cfg))
    if "hu" in cfg.blocks:
        parts.append(cfg.w_hu * _hu(mask))
    return np.concatenate(parts).astype(np.float32)


def extract_batch(images: np.ndarray, cfg: FeatureConfig = FeatureConfig(), n_jobs: int = 1) -> np.ndarray:
    if n_jobs == 1:
        return np.stack([extract_features(im, cfg) for im in images])
    from joblib import Parallel, delayed

    chunks = np.array_split(np.arange(len(images)), n_jobs * 4)
    res = Parallel(n_jobs=n_jobs)(
        delayed(lambda ix: np.stack([extract_features(images[i], cfg) for i in ix]))(ix) for ix in chunks if len(ix)
    )
    return np.concatenate(res)


def raw_pixels(images: np.ndarray, size: int = 32) -> np.ndarray:
    """Línea base: píxeles crudos RGB reducidos a size×size, escalados a [0,1]."""
    out = np.stack([cv2.resize(to_canonical(im), (size, size), interpolation=cv2.INTER_AREA) for im in images])
    return (out.reshape(len(images), -1) / 255.0).astype(np.float32)
