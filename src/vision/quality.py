"""Compuerta de calidad de imagen determinista (antes de cualquier modelo)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass
class QualityThresholds:
    min_side: int = 32
    max_side: int = 4096
    min_brightness: float = 0.05  # brillo medio del cuadro (0-1); COIL: p0.1 ≈ 0.13 (fondo oscuro ≈ 25/255)
    max_brightness: float = 0.80  # COIL: p99.9 ≈ 0.49
    min_sharpness: float = 20.0  # varianza del Laplaciano en 128×128; COIL: p0.1 ≈ 47
    min_object_fraction: float = 0.05  # fracción de píxeles "objeto" (gris > 40); COIL: p0.1 ≈ 0.13
    max_object_fraction: float = 0.98

    def to_dict(self) -> dict:
        return asdict(self)


def assess_quality(image_rgb: np.ndarray, th: QualityThresholds | None = None) -> dict:
    th = th or QualityThresholds()
    img = np.asarray(image_rgb)
    issues: list[str] = []
    h, w = img.shape[:2]
    if min(h, w) < th.min_side:
        issues.append("too_small")
    if max(h, w) > th.max_side:
        issues.append("too_large")
    if img.ndim == 2:
        gray = img.astype(np.uint8)
    else:
        gray = cv2.cvtColor(img[..., :3].astype(np.uint8), cv2.COLOR_RGB2GRAY)
    g128 = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA) if gray.shape != (128, 128) else gray
    brightness = float(g128.mean() / 255.0)
    sharpness = float(cv2.Laplacian(g128, cv2.CV_64F).var())
    obj_frac = float((g128 > 40).mean())
    if brightness < th.min_brightness:
        issues.append("too_dark")
    if brightness > th.max_brightness:
        issues.append("too_bright")
    if sharpness < th.min_sharpness:
        issues.append("blurry")
    if obj_frac < th.min_object_fraction:
        issues.append("empty_frame")
    if obj_frac > th.max_object_fraction:
        issues.append("no_background")
    return {"ok": not issues, "issues": issues, "brightness": round(brightness, 4),
            "sharpness": round(sharpness, 2), "object_fraction": round(obj_frac, 4), "width": int(w), "height": int(h)}


def calibrate_quality(images: np.ndarray, q_low: float = 0.1, q_high: float = 99.9, margin: float = 0.5) -> QualityThresholds:
    """Fija umbrales con estadísticas del dataset: percentil extremo × margen (no rechaza vistas válidas)."""
    stats = [assess_quality(im) for im in images]
    b = np.array([s["brightness"] for s in stats])
    s = np.array([s_["sharpness"] for s_ in stats])
    f = np.array([s_["object_fraction"] for s_ in stats])
    return QualityThresholds(
        min_brightness=float(np.percentile(b, q_low) * margin),
        max_brightness=float(min(0.95, np.percentile(b, q_high) * 1.5)),
        min_sharpness=float(np.percentile(s, q_low) * margin),
        min_object_fraction=float(np.percentile(f, q_low) * margin),
        max_object_fraction=float(min(0.99, np.percentile(f, q_high) + 0.05)),
    )
