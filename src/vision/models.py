"""Modelo de visión sobre vectores de características: clasificador + recuperación + ángulo + novedad.

`VisionModel` es agnóstico al extractor: sirve para las características clásicas (producción) y para los
embeddings de la referencia moderna (comparación), de modo que la comparación aísla el efecto de la
representación.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import KNeighborsClassifier, NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from .angle import circular_error, distance_profile, estimate_angle, implied_angular_gap
from .data import SEED

CLASSIFIERS = {
    "knn": lambda p: KNeighborsClassifier(n_neighbors=p.get("n_neighbors", 5), weights="distance"),
    "logreg": lambda p: LogisticRegression(C=p.get("C", 1.0), max_iter=3000),
    "svm_rbf": lambda p: SVC(C=p.get("C", 10.0), gamma=p.get("gamma", "scale"), probability=True, random_state=SEED),
    "random_forest": lambda p: RandomForestClassifier(n_estimators=p.get("n_estimators", 300),
                                                       max_features=p.get("max_features", "sqrt"),
                                                       n_jobs=-1, random_state=SEED),
}


@dataclass
class VisionModel:
    clf_name: str = "logreg"
    clf_params: dict = field(default_factory=dict)
    n_pca: int | None = 150
    k_angle_refine: int = 2
    symmetry_threshold: float = 0.25
    symmetric_classes: list = field(default_factory=list)

    # ---------------------------------------------------------------- entrenamiento
    def fit(self, X: np.ndarray, labels: np.ndarray, angles: np.ndarray, image_ids: np.ndarray) -> "VisionModel":
        self.scaler_ = StandardScaler().fit(X)
        Z = self.scaler_.transform(X)
        if self.n_pca:
            self.pca_ = PCA(n_components=min(self.n_pca, Z.shape[0] - 1, Z.shape[1]), random_state=SEED).fit(Z)
            Z = self.pca_.transform(Z)
        else:
            self.pca_ = None
        self.clf_ = CLASSIFIERS[self.clf_name](self.clf_params).fit(Z, labels)
        self.classes_ = self.clf_.classes_
        self._set_references(Z.astype(np.float32), labels, angles, image_ids)
        return self

    def _set_references(self, Z, labels, angles, image_ids):
        self.ref_Z_ = Z
        self.ref_labels_ = np.asarray(labels)
        self.ref_angles_ = np.asarray(angles, dtype=np.float32)
        self.ref_ids_ = np.asarray(image_ids)
        self.nn_ = NearestNeighbors(n_neighbors=1).fit(Z)
        self.class_index_ = {c: np.where(self.ref_labels_ == c)[0] for c in np.unique(self.ref_labels_)}
        self.profiles_ = {c: distance_profile(Z[ix], self.ref_angles_[ix]) for c, ix in self.class_index_.items()}
        self.symmetry_scores_ = {c: rotational_symmetry_score(Z[ix], self.ref_angles_[ix])
                                 for c, ix in self.class_index_.items()}
        self.symmetric_classes = sorted(str(c) for c, v in self.symmetry_scores_.items()
                                        if np.isfinite(v) and v >= self.symmetry_threshold)

    def transform(self, X: np.ndarray) -> np.ndarray:
        Z = self.scaler_.transform(np.atleast_2d(X))
        return (self.pca_.transform(Z) if self.pca_ is not None else Z).astype(np.float32)

    # ---------------------------------------------------------------- inferencia
    def predict(self, X: np.ndarray, top_k: int = 5, angle_from: str = "predicted") -> dict:
        """Predicción vectorizada. Devuelve arreglos por muestra."""
        Z = self.transform(X)
        proba = self.clf_.predict_proba(Z)
        pidx = proba.argmax(1)
        pred = self.classes_[pidx]
        conf = proba[np.arange(len(Z)), pidx]
        d_nn, _ = self.nn_.kneighbors(Z, n_neighbors=1)
        out = {"label": pred, "confidence": conf, "nn_distance": d_nn[:, 0], "proba": proba,
               "angle": np.zeros(len(Z)), "angle_unc": np.zeros(len(Z)), "ambiguous": np.zeros(len(Z), bool),
               "similar": []}
        for i, z in enumerate(Z):
            ix = self.class_index_[pred[i]]
            d = np.linalg.norm(self.ref_Z_[ix] - z, axis=1)
            r = estimate_angle(d, self.ref_angles_[ix], k_refine=self.k_angle_refine)
            out["angle"][i] = r["degrees"]
            out["angle_unc"][i] = implied_angular_gap(float(d.min()), self.profiles_[pred[i]])
            out["ambiguous"][i] = r["ambiguous"] or (pred[i] in self.symmetric_classes)
            if top_k:
                dall = np.linalg.norm(self.ref_Z_ - z, axis=1)
                o = np.argsort(dall)[:top_k]
                scale = np.median(dall) + 1e-9
                out["similar"].append([
                    {"label": str(self.ref_labels_[j]), "angle_deg": float(self.ref_angles_[j]),
                     "similarity": round(float(np.exp(-dall[j] / scale * 3)), 4), "image_id": str(self.ref_ids_[j]),
                     "distance": round(float(dall[j]), 4)} for j in o])
        return out

    # ---------------------------------------------------------------- SKU nuevo
    def add_class(self, X_new: np.ndarray, label, angles: np.ndarray, image_ids: np.ndarray) -> "VisionModel":
        """Agrega un SKU sin re-ajustar escalado/PCA: re-entrena el clasificador sobre referencias + nuevas."""
        Znew = self.transform(X_new)
        Z = np.vstack([self.ref_Z_, Znew])
        labels = np.concatenate([self.ref_labels_.astype(object), np.array([label] * len(Znew), dtype=object)])
        labels = np.array([str(x) for x in labels])
        angs = np.concatenate([self.ref_angles_, np.asarray(angles, np.float32)])
        ids = np.concatenate([self.ref_ids_, np.asarray(image_ids)])
        self.clf_ = CLASSIFIERS[self.clf_name](self.clf_params).fit(Z, labels)
        self.classes_ = self.clf_.classes_
        self._set_references(Z, labels, angs, ids)
        return self


def rotational_symmetry_score(Z: np.ndarray, angles: np.ndarray, min_sep: float = 30.0) -> float:
    """Ambigüedad rotacional de un objeto (leave-one-out sobre sus vistas de referencia).

    Para cada vista, se busca la vista MÁS PARECIDA entre las demás del mismo objeto; el índice es la
    fracción de vistas cuya gemela más parecida está a ≥ min_sep grados. ≈0 → el aspecto cambia con el
    ángulo (ángulo recuperable); alto → vistas lejanas indistinguibles (latas, tazas, donas, simetría a 180°).
    """
    from scipy.spatial.distance import cdist

    if len(angles) < 4:
        return float("nan")
    S = circular_error(angles[:, None], angles[None, :])
    if S[S > 0].min() > 5:  # solo definido con referencias densas (paso 5°, como en producción)
        return float("nan")
    D = cdist(Z, Z)
    np.fill_diagonal(D, np.inf)
    j = D.argmin(1)
    return float((circular_error(angles, angles[j]) >= min_sep).mean())


def evaluate_predictions(out: dict, true_labels: np.ndarray, true_angles: np.ndarray) -> dict:
    correct = out["label"] == true_labels
    err = circular_error(out["angle"], true_angles)
    return {
        "class_accuracy": float(correct.mean()),
        "angle_mae_deg": float(err[correct].mean()) if correct.any() else float("nan"),
        "angle_median_deg": float(np.median(err[correct])) if correct.any() else float("nan"),
        "angle_within_10": float((err[correct] <= 10).mean()) if correct.any() else float("nan"),
        "end_to_end_success": float((correct & (err <= 10)).mean()),
        "n": int(len(true_labels)),
    }
