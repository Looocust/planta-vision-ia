"""Perturbaciones de imagen: aumentación de entrenamiento y evaluación de robustez (mismas primitivas)."""
from __future__ import annotations

import cv2
import numpy as np

# perturbaciones fijas de la evaluación de robustez (nombre → (tipo, nivel))
ROBUSTNESS_SUITE = {
    "limpia": ("none", 0.0),
    "ruido σ=2": ("noise", 2.0),
    "ruido σ=4": ("noise", 4.0),
    "ruido σ=8": ("noise", 8.0),
    "brillo ×0.7": ("brightness", 0.7),
    "brillo ×1.3": ("brightness", 1.3),
    "blur σ=1": ("blur", 1.0),
}


def apply(img: np.ndarray, kind: str, level: float, rng: np.random.Generator | None = None) -> np.ndarray:
    img = np.asarray(img)
    if kind == "none":
        return img
    if kind == "noise":
        rng = rng or np.random.default_rng(0)
        out = img.astype(np.float32) + rng.normal(0, level, img.shape)
    elif kind == "brightness":
        out = img.astype(np.float32) * level
    elif kind == "blur":
        return cv2.GaussianBlur(img, (0, 0), level)
    else:
        raise ValueError(kind)
    return np.clip(out, 0, 255).astype(np.uint8)


def random_perturbation(img: np.ndarray, rng: np.random.Generator, sigma_max: float = 6.0,
                        brightness=(0.8, 1.2), blur_p: float = 0.3, blur_sigma=(0.5, 1.0)) -> np.ndarray:
    """Aumentación de entrenamiento: ruido gaussiano σ~U(0,6), brillo ×U(0.8,1.2) y blur leve con prob. 0.3."""
    out = apply(img, "brightness", float(rng.uniform(*brightness)))
    if rng.random() < blur_p:
        out = apply(out, "blur", float(rng.uniform(*blur_sigma)))
    return apply(out, "noise", float(rng.uniform(0, sigma_max)), rng)


def perturb_batch(images: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.stack([random_perturbation(im, rng) for im in images])
