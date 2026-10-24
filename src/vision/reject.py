"""Regla de decisión ACT / REVIEW / REJECT, selección de umbrales en validación y métricas de confianza."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from sklearn.metrics import roc_auc_score

ANGLE_TOLERANCE_DEG = 10.0  # tolerancia de agarre del robot (±10°)


@dataclass
class DecisionThresholds:
    min_class_confidence: float = 0.5
    max_angle_uncertainty_deg: float = 10.0
    unknown_distance: float = 1e9

    def __post_init__(self):
        """Valida antes de usar: NaN/inf o fuera de rango abrirían la compuerta (p. ej. `100 > NaN` es False)."""
        for k in ("min_class_confidence", "max_angle_uncertainty_deg", "unknown_distance"):
            v = getattr(self, k)
            if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)) or not np.isfinite(v):
                raise ValueError(f"{k} debe ser un número finito")
            setattr(self, k, float(v))
        if not 0.0 <= self.min_class_confidence <= 1.0:
            raise ValueError("min_class_confidence debe estar en [0, 1]")
        if not (self.max_angle_uncertainty_deg == -1.0 or 0.0 <= self.max_angle_uncertainty_deg <= 180.0):
            raise ValueError("max_angle_uncertainty_deg debe estar en [0, 180] (o -1 = automatización desactivada)")
        if self.unknown_distance <= 0:
            raise ValueError("unknown_distance debe ser > 0")

    def to_dict(self) -> dict:
        return asdict(self)


def decide(quality_ok: bool, nn_distance: float, confidence: float, angle_unc: float, ambiguous: bool,
           th: DecisionThresholds) -> tuple[str, list[str]]:
    """Orden: calidad → desconocido (REJECT) → clase/ángulo dudosos (REVIEW) → ACT."""
    if not quality_ok:
        return "REJECT", ["image_quality"]
    # comparaciones escritas para que un NaN en las entradas NUNCA produzca ACT
    if not nn_distance <= th.unknown_distance:
        return "REJECT", ["unknown_object"]
    reasons = []
    if not confidence >= th.min_class_confidence:
        reasons.append("low_class_confidence")
    if not angle_unc <= th.max_angle_uncertainty_deg:
        reasons.append("high_angle_uncertainty")
    if ambiguous:
        reasons.append("rotational_ambiguity")
    return ("REVIEW", reasons) if reasons else ("ACT", [])


def decide_batch(nn_distance, confidence, angle_unc, ambiguous, th: DecisionThresholds) -> np.ndarray:
    nn_distance, confidence, angle_unc, ambiguous = map(np.asarray, (nn_distance, confidence, angle_unc, ambiguous))
    dec = np.full(len(confidence), "ACT", dtype=object)
    review = ~(confidence >= th.min_class_confidence) | ~(angle_unc <= th.max_angle_uncertainty_deg) | ambiguous
    dec[review] = "REVIEW"
    dec[~(nn_distance <= th.unknown_distance)] = "REJECT"
    return dec


def select_thresholds(correct_class, angle_err, confidence, angle_unc, ambiguous, nn_distance,
                      is_unknown=None, target_error: float = 0.02,
                      conf_grid=(0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99),
                      unc_grid=(2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 20.0, 30.0),
                      fallback_angle_uncertainty: float = 5.0, min_act: int = 50,
                      max_unknown_false_accept: float = 0.05, min_unknown: int = 100) -> tuple[DecisionThresholds, dict]:
    """Elige umbrales SOLO con datos de validación.

    unknown_distance: si hay pseudo-desconocidos en validación → maximiza Youden J; si no → percentil 99 de
    la distancia de vistas conocidas (acepta ~99% de lo conocido).
    min_class_confidence: máxima cobertura con error de CLASE (incl. desconocidos aceptados) ≤ target_error/2.
    max_angle_uncertainty: con esa confianza fija, máxima cobertura con error extremo a extremo entre ACT
    (clase incorrecta o |Δθ|>10°) ≤ target_error; si ninguno lo cumple, el de menor error.
    La automatización solo se habilita si en validación hay ≥ min_act decisiones ACT y su error puntual es
    ≤ target_error (se reporta además el intervalo de Wilson 95 %); si no, se desactiva (umbral angular -1 →
    todo REVIEW) y el riesgo se reporta como N/A. Si la validación incluye pseudo-desconocidos (≥ min_unknown
    vistas), además se exige que como mucho `max_unknown_false_accept` (5 %) de ellas llegue a ACT (se sube la
    confianza mínima si hace falta); si no se logra, también se desactiva. Si la validación no es informativa para el ángulo (p. ej. protocolo C: las vistas de validación quedan a
    30° de las de ajuste, el test a ≤15°), se usa el prior 5° = mitad de la tolerancia de ±10°.
    """
    correct_class = np.asarray(correct_class, bool)
    angle_err = np.asarray(angle_err, float)
    confidence = np.asarray(confidence, float)
    angle_unc = np.asarray(angle_unc, float)
    ambiguous = np.asarray(ambiguous, bool)
    nn_distance = np.asarray(nn_distance, float)
    known = np.ones(len(confidence), bool) if is_unknown is None else ~np.asarray(is_unknown, bool)
    if is_unknown is not None and (~known).any():
        cand = np.unique(np.quantile(nn_distance, np.linspace(0.01, 0.99, 197)))
        tpr = np.array([(nn_distance[~known] > c).mean() for c in cand])
        fpr = np.array([(nn_distance[known] > c).mean() for c in cand])
        unk_d = float(cand[np.argmax(tpr - fpr)])
    else:
        unk_d = float(np.percentile(nn_distance[known], 99))
    success = correct_class & (angle_err <= ANGLE_TOLERANCE_DEG) & known
    # ¿la validación tiene señal angular comparable al test? (si casi nada queda dentro de ±10°,
    # la brecha de validación es mayor que la de test y el umbral angular no se puede afinar ahí)
    angle_informative = bool(success[correct_class].mean() >= 0.5) if correct_class.any() else False
    accepted = nn_distance <= unk_d
    # etapa 1: confianza de clase → error de clase (incluye desconocidos aceptados) ≤ la mitad del presupuesto;
    # la otra mitad queda para el error angular
    class_budget = target_error / 2
    best_c = None
    for c in conf_grid:
        a = accepted & (confidence >= c)
        cov = a.mean()
        err = 1 - correct_class[a].mean() if a.any() else 0.0
        key = (err <= class_budget, cov if err <= class_budget else -err)
        if best_c is None or key > best_c[0]:
            best_c = (key, c)
    conf_th = best_c[1]
    # margen de seguridad "gratis": la confianza más exigente que cueste ≤ 0.5 pp de cobertura en validación
    # (los desconocidos casi-duplicados de un conocido no se ven en validación; la confianza sí ayuda en test)
    if best_c[0][0]:
        cov_best = best_c[0][1]
        for c in sorted(conf_grid):
            a = accepted & (confidence >= c)
            e = 1 - correct_class[a].mean() if a.any() else 0.0
            if c > conf_th and a.mean() >= cov_best - 0.005 and e <= class_budget:
                conf_th = c
    # etapa 2: incertidumbre angular → error extremo a extremo entre ACT ≤ target, con al menos min_act casos ACT
    table, best = [], None
    for u in (unc_grid if angle_informative else ()):
        th = DecisionThresholds(conf_th, u, unk_d)
        act = decide_batch(nn_distance, confidence, angle_unc, ambiguous, th) == "ACT"
        n_act, n_err = int(act.sum()), int((act & ~success).sum())
        err = n_err / n_act if n_act else float("nan")
        lo, hi = wilson_interval(n_err, n_act)
        table.append({"max_angle_uncertainty_deg": u, "coverage": float(act.mean()), "n_act": n_act, "n_errors": n_err,
                      "error_among_act": err, "wilson95_low": lo, "wilson95_high": hi})
        ok = n_act >= min_act and err <= target_error
        key = (ok, act.mean() if ok else -(err if n_act else 1.0))
        if best is None or key > best[0]:
            best = (key, u)
    unc_th = best[1] if best is not None else fallback_angle_uncertainty
    unk = ~known

    def _stats(th_):
        act_ = decide_batch(nn_distance, confidence, angle_unc, ambiguous, th_) == "ACT"
        n_a, n_e = int(act_.sum()), int((act_ & ~success).sum())
        fa_k, fa_n = int((act_ & unk).sum()), int(unk.sum())
        return act_, n_a, n_e, fa_k, fa_n

    # criterio explícito de falsa aceptación de desconocidos (si la validación tiene pseudo-desconocidos):
    # ≤ max_unknown_false_accept con ≥ min_unknown casos; si no se cumple, se sube la confianza mínima
    th = DecisionThresholds(conf_th, unc_th, unk_d)
    _, n_act, n_err, fa_k, fa_n = _stats(th)
    if fa_n >= min_unknown and fa_k / fa_n > max_unknown_false_accept:
        for c in sorted(conf_grid):
            if c <= conf_th:
                continue
            t2 = DecisionThresholds(c, unc_th, unk_d)
            _, _, _, k2, _ = _stats(t2)
            if k2 / fa_n <= max_unknown_false_accept:
                th = t2
                break
        _, n_act, n_err, fa_k, fa_n = _stats(th)
    fa_ok = fa_n < min_unknown or fa_k / fa_n <= max_unknown_false_accept
    # factible solo con evidencia suficiente: ≥ min_act ACT, error puntual ≤ objetivo y criterio de desconocidos
    feasible = n_act >= min_act and (n_err / n_act) <= target_error and fa_ok
    lo, hi = wilson_interval(n_err, n_act)
    candidate = {"thresholds": th.to_dict(), "val_n_act": n_act, "val_n_errors": n_err,
                 "val_coverage": n_act / len(confidence), "val_error_among_act": (n_err / n_act) if n_act else None,
                 "val_error_wilson95": [lo, hi]}
    ufa = {"k": fa_k, "n": fa_n, "rate": (fa_k / fa_n) if fa_n else None,
           "wilson95": list(wilson_interval(fa_k, fa_n)), "max_allowed": max_unknown_false_accept,
           "min_cases": min_unknown, "ok": bool(fa_ok)}
    info = {**candidate, "target_error": target_error, "min_act": min_act,
            "unknown_false_accept_val": ufa,
            "angle_threshold_source": "validation" if best is not None else "prior_tolerance_half",
            "automation_enabled": bool(feasible), "grid": table}
    if not feasible:
        # política EFECTIVA: automatización desactivada (todo a REVIEW) → 0 ACT, riesgo null.
        # Las cifras del candidato descartado se conservan aparte.
        th = DecisionThresholds(th.min_class_confidence, -1.0, unk_d)
        info.update(val_n_act=0, val_n_errors=0, val_coverage=0.0, val_error_among_act=None,
                    val_error_wilson95=[None, None], discarded_candidate=candidate)
        info["unknown_false_accept_val"] = {**ufa, "k": 0, "rate": 0.0 if fa_n else None,
                                            "wilson95": list(wilson_interval(0, fa_n)),
                                            "discarded_candidate_rate": ufa["rate"]}
    return th, info


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Intervalo de Wilson 95 % para una proporción k/n (None si n = 0)."""
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z**2 / n
    c = (p + z**2 / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
    return float(max(0.0, c - h)), float(min(1.0, c + h))


# ---------------------------------------------------------------- métricas de confianza

def expected_calibration_error(confidence, correct, n_bins: int = 15) -> tuple[float, dict]:
    confidence = np.asarray(confidence, float)
    correct = np.asarray(correct, float)
    bins = np.linspace(0, 1, n_bins + 1)
    ece, rows = 0.0, {"bin_conf": [], "bin_acc": [], "bin_count": []}
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (confidence > lo) & (confidence <= hi) if lo > 0 else (confidence >= lo) & (confidence <= hi)
        if m.any():
            ece += m.mean() * abs(confidence[m].mean() - correct[m].mean())
            rows["bin_conf"].append(float(confidence[m].mean()))
            rows["bin_acc"].append(float(correct[m].mean()))
            rows["bin_count"].append(int(m.sum()))
    return float(ece), rows


def risk_coverage(score, success) -> dict:
    """Curva riesgo–cobertura: ordena por score descendente y acumula el error."""
    score = np.asarray(score, float)
    success = np.asarray(success, float)
    o = np.argsort(-score, kind="stable")
    s = success[o]
    n = np.arange(1, len(s) + 1)
    risk = 1 - np.cumsum(s) / n
    cov = n / len(s)
    aurc = float(np.mean(risk))
    return {"coverage": cov, "risk": risk, "aurc": aurc}


def auroc(score_positive_is_unknown, is_unknown) -> float:
    return float(roc_auc_score(np.asarray(is_unknown, int), np.asarray(score_positive_is_unknown, float)))


def unreliable_angle_classes(true_labels, pred_labels, angle_err, max_fail_rate: float = 0.25, min_n: int = 3) -> list[str]:
    """Clases cuyo ángulo falla (> ±10°) en más de `max_fail_rate` de sus vistas de VALIDACIÓN con clase correcta.

    Detecta empíricamente objetos con ambigüedad rotacional (simetría, vistas opuestas parecidas) con datos de
    entrenamiento solamente; en inferencia se marcan `rotational_ambiguity` → REVIEW.
    """
    true_labels = np.asarray(true_labels)
    ok = np.asarray(pred_labels) == true_labels
    fail = np.asarray(angle_err) > ANGLE_TOLERANCE_DEG
    out = []
    for c in np.unique(true_labels[ok]):
        m = ok & (true_labels == c)
        if m.sum() >= min_n and fail[m].mean() > max_fail_rate:
            out.append(str(c))
    return sorted(out)
