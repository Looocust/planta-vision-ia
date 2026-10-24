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

    def to_dict(self) -> dict:
        return asdict(self)


def decide(quality_ok: bool, nn_distance: float, confidence: float, angle_unc: float, ambiguous: bool,
           th: DecisionThresholds) -> tuple[str, list[str]]:
    """Orden: calidad → desconocido (REJECT) → clase/ángulo dudosos (REVIEW) → ACT."""
    if not quality_ok:
        return "REJECT", ["image_quality"]
    if nn_distance > th.unknown_distance:
        return "REJECT", ["unknown_object"]
    reasons = []
    if confidence < th.min_class_confidence:
        reasons.append("low_class_confidence")
    if angle_unc > th.max_angle_uncertainty_deg:
        reasons.append("high_angle_uncertainty")
    if ambiguous:
        reasons.append("rotational_ambiguity")
    return ("REVIEW", reasons) if reasons else ("ACT", [])


def decide_batch(nn_distance, confidence, angle_unc, ambiguous, th: DecisionThresholds) -> np.ndarray:
    nn_distance, confidence, angle_unc, ambiguous = map(np.asarray, (nn_distance, confidence, angle_unc, ambiguous))
    dec = np.full(len(confidence), "ACT", dtype=object)
    review = (confidence < th.min_class_confidence) | (angle_unc > th.max_angle_uncertainty_deg) | ambiguous
    dec[review] = "REVIEW"
    dec[nn_distance > th.unknown_distance] = "REJECT"
    return dec


def select_thresholds(correct_class, angle_err, confidence, angle_unc, ambiguous, nn_distance,
                      is_unknown=None, target_error: float = 0.02,
                      conf_grid=(0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99),
                      unc_grid=(2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 20.0, 30.0),
                      fallback_angle_uncertainty: float = 5.0) -> tuple[DecisionThresholds, dict]:
    """Elige umbrales SOLO con datos de validación.

    unknown_distance: si hay pseudo-desconocidos en validación → maximiza Youden J; si no → percentil 99 de
    la distancia de vistas conocidas (acepta ~99% de lo conocido).
    min_class_confidence: máxima cobertura con error de CLASE (incl. desconocidos aceptados) ≤ target_error/2.
    max_angle_uncertainty: con esa confianza fija, máxima cobertura con error extremo a extremo entre ACT
    (clase incorrecta o |Δθ|>10°) ≤ target_error; si ninguno lo cumple, el de menor error.
    Si NINGÚN umbral cumple el objetivo en validación, se desactiva la automatización (umbral angular -1 →
    todo REVIEW). Si la validación no es informativa para el ángulo (p. ej. protocolo C: las vistas de validación quedan a
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
    # etapa 2: incertidumbre angular → error extremo a extremo entre ACT ≤ target
    table, best = [], None
    for u in (unc_grid if angle_informative else ()):
        th = DecisionThresholds(conf_th, u, unk_d)
        act = decide_batch(nn_distance, confidence, angle_unc, ambiguous, th) == "ACT"
        cov = act.mean()
        err = 1 - success[act].mean() if act.any() else 0.0
        table.append({"max_angle_uncertainty_deg": u, "coverage": float(cov), "error_among_act": float(err)})
        key = (err <= target_error, cov if err <= target_error else -err)
        if best is None or key > best[0]:
            best = (key, u)
    unc_th = best[1] if best is not None else fallback_angle_uncertainty
    if best is None:  # umbral angular por prior: se verifica igual su error en validación
        th0 = DecisionThresholds(conf_th, unc_th, unk_d)
        act0 = decide_batch(nn_distance, confidence, angle_unc, ambiguous, th0) == "ACT"
        feasible = (not act0.any()) or (1 - success[act0].mean()) <= target_error
    else:
        feasible = bool(best[0][0])
    if not feasible:
        # ningún umbral cumple el objetivo en validación → se desactiva la automatización (todo a REVIEW):
        # preferimos no actuar a actuar con un error conocido > objetivo.
        unc_th = -1.0
    th = DecisionThresholds(conf_th, unc_th, unk_d)
    act = decide_batch(nn_distance, confidence, angle_unc, ambiguous, th) == "ACT"
    cov = float(act.mean())
    err = float(1 - success[act].mean()) if act.any() else 0.0
    return th, {"val_coverage": cov, "val_error_among_act": err, "target_error": target_error,
                "angle_threshold_source": "validation" if best is not None else "prior_tolerance_half",
                "automation_enabled": bool(feasible),
                "grid": table}


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
