"""Estimación de ángulo: métricas circulares y recuperación condicionada al objeto."""
from __future__ import annotations

import numpy as np


def circular_error(a, b) -> np.ndarray:
    """Error angular circular en grados: min(|a-b|, 360-|a-b|) ∈ [0, 180]."""
    d = np.abs(np.asarray(a, float) - np.asarray(b, float)) % 360.0
    return np.minimum(d, 360.0 - d)


def circular_mean(angles_deg, weights=None) -> float:
    a = np.deg2rad(np.asarray(angles_deg, float))
    w = np.ones_like(a) if weights is None else np.asarray(weights, float)
    s, c = np.sum(w * np.sin(a)), np.sum(w * np.cos(a))
    if abs(s) < 1e-12 and abs(c) < 1e-12:
        return float("nan")
    return float(np.rad2deg(np.arctan2(s, c)) % 360.0)


def circular_std(angles_deg, weights=None) -> float:
    """Desviación estándar circular (grados): sqrt(-2 ln R)."""
    a = np.deg2rad(np.asarray(angles_deg, float))
    w = np.ones_like(a) if weights is None else np.asarray(weights, float)
    w = w / w.sum()
    R = np.hypot(np.sum(w * np.sin(a)), np.sum(w * np.cos(a)))
    R = np.clip(R, 1e-12, 1.0)
    return float(np.rad2deg(np.sqrt(-2.0 * np.log(R))))


def estimate_angle(dists: np.ndarray, ref_angles: np.ndarray, k_refine: int = 2, k_unc: int = 5,
                   local_window: float = 45.0, power: float = 4.0, ambiguity_ratio: float = 1.10,
                   ambiguity_min_sep: float = 30.0) -> dict:
    """Estima el ángulo a partir de distancias a las vistas de referencia de UN objeto.

    1) Vecino más cercano → modo angular.
    2) Refinamiento: media circular ponderada (w = (d1/d)^p) de los k_refine vecinos más cercanos que
       caen a ≤ local_window del modo (interpola entre vistas de referencia vecinas).
    3) Incertidumbre: dispersión RMS circular ponderada de los k_unc vecinos alrededor de la estimación.
       Si hay vistas lejanas (≥ ambiguity_min_sep) casi tan parecidas como la mejor (d ≤ ratio·d1),
       la vista es ambigua (simetría rotacional / vistas a 180° similares).
    """
    dists = np.asarray(dists, float)
    ref_angles = np.asarray(ref_angles, float)
    # varias referencias con el mismo ángulo (p. ej. copias aumentadas): se conserva la más cercana por ángulo
    ua, inv = np.unique(np.round(ref_angles, 3), return_inverse=True)
    if len(ua) < len(ref_angles):
        dmin = np.full(len(ua), np.inf)
        np.minimum.at(dmin, inv, dists)
        dists, ref_angles = dmin, ua
    order = np.argsort(dists)
    d1 = max(dists[order[0]], 1e-9)
    mode = ref_angles[order[0]]
    loc = [i for i in order[: max(k_refine * 3, k_refine)] if circular_error(ref_angles[i], mode) <= local_window]
    loc = loc[:k_refine]
    w_loc = (d1 / np.maximum(dists[loc], 1e-9)) ** power
    est = circular_mean(ref_angles[loc], w_loc)
    top = order[:k_unc]
    w = (d1 / np.maximum(dists[top], 1e-9)) ** power
    err = circular_error(ref_angles[top], est)
    unc = float(np.sqrt(np.sum(w * err**2) / np.sum(w)))
    rivals = [i for i in order[1:] if dists[i] <= ambiguity_ratio * d1]
    ambiguous = any(circular_error(ref_angles[i], est) >= ambiguity_min_sep for i in rivals)
    return {"degrees": float(est), "uncertainty_deg": unc, "ambiguous": bool(ambiguous),
            "neighbors": order[:k_unc].tolist()}


def symmetry_profile(feats: np.ndarray, angles: np.ndarray) -> dict:
    """Perfil de auto-similitud de un objeto: distancia media entre vistas en función de la separación angular.

    Devuelve separaciones (5..180) y distancia media normalizada por la distancia a 5°.
    Un objeto rotacionalmente simétrico tiene un perfil plano (≈1); uno asimétrico crece con la separación.
    """
    from scipy.spatial.distance import cdist

    D = cdist(feats, feats)
    sep = circular_error(angles[:, None], angles[None, :])
    seps = np.arange(5, 185, 5)
    prof = np.array([D[np.isclose(sep, s)].mean() for s in seps])
    return {"separations": seps, "profile": prof / max(prof[0], 1e-9)}


def symmetry_score(feats: np.ndarray, angles: np.ndarray) -> float:
    """Índice de ambigüedad rotacional ∈ (0,1]: distancia media a 5° / distancia media a ≥45°.

    ≈1 → las vistas lejanas son tan parecidas como las adyacentes (ángulo intrínsecamente ambiguo).
    """
    p = symmetry_profile(feats, angles)
    far = p["profile"][p["separations"] >= 45].mean()
    return float(1.0 / max(far, 1e-9))


def distance_profile(feats: np.ndarray, angles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Curva distancia-en-características vs separación angular para las vistas de referencia de UN objeto.

    Devuelve (separaciones, distancia media) con (0, 0) al inicio y envolvente monótona creciente, para
    poder invertirla: dada la distancia del query a su vecino más cercano, ¿a cuántos grados equivale?
    """
    from scipy.spatial.distance import cdist

    feats = np.asarray(feats, float)
    angles = np.asarray(angles, float)
    if len(angles) < 2:
        return np.array([0.0, 180.0]), np.array([0.0, 1e9])
    D = cdist(feats, feats)
    S = np.round(circular_error(angles[:, None], angles[None, :]), 3)
    seps = np.unique(S[S > 0])
    m = np.array([D[S == s].mean() for s in seps])
    m = np.maximum.accumulate(m)
    return np.r_[0.0, seps].astype(np.float32), np.r_[0.0, m].astype(np.float32)


def implied_angular_gap(d_nn: float, profile: tuple[np.ndarray, np.ndarray]) -> float:
    """Incertidumbre angular (grados) implicada por la distancia al vecino más cercano del mismo objeto."""
    seps, m = profile
    return float(np.interp(d_nn, m, seps))
