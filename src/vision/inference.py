"""Predictor productivo: carga el artefacto versionado y produce la respuesta del contrato (CONTRACTS.md)."""
from __future__ import annotations

import io
import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from PIL import Image

from .angle import circular_error, estimate_angle
from .features import FeatureConfig, extract_features
from .models import VisionModel
from .quality import QualityThresholds, assess_quality
from .reject import DecisionThresholds, decide

IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}__\d{1,3}$")
MIN_VIEWS_NEW_SKU = 12
MIN_COVERAGE_DEG = 300.0


def decode_image(data: bytes) -> np.ndarray:
    """Bytes PNG/JPG → RGB uint8. Lanza ValueError si no es una imagen válida."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"imagen inválida: {e}") from e
    return np.asarray(im.convert("RGB"))


def _rotate_cmd(angle: float) -> float:
    r = -float(angle) % 360.0
    return round(r - 360.0 if r > 180.0 else r, 1)


class VisionPredictor:
    def __init__(self, model: VisionModel, metadata: dict, model_dir: Path | None = None):
        self.model = model
        self.metadata = metadata
        self.model_dir = Path(model_dir) if model_dir else None
        self.feature_cfg = FeatureConfig.from_dict(metadata["feature_config"])
        self.thresholds = DecisionThresholds(**metadata["thresholds"])
        self.quality_thresholds = QualityThresholds(**metadata.get("quality_thresholds", {}))

    @property
    def version(self) -> str:
        return self.metadata["version"]

    # ---------------------------------------------------------------- persistencia
    @classmethod
    def load(cls, model_dir: str | Path) -> "VisionPredictor":
        model_dir = Path(model_dir)
        model = joblib.load(model_dir / "model.joblib")
        meta = json.loads((model_dir / "metadata.json").read_text())
        return cls(model, meta, model_dir)

    def save(self, model_dir: str | Path) -> Path:
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, model_dir / "model.joblib", compress=3)
        from .data import sha256_file

        # el hash SIEMPRE se recalcula sobre el archivo recién escrito (nunca se hereda del padre)
        self.metadata["model_sha256"] = sha256_file(model_dir / "model.joblib")
        self.metadata["thresholds"] = self.thresholds.to_dict()
        self.metadata["artifact_size_mb"] = round(sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file()) / 1e6, 2)
        (model_dir / "metadata.json").write_text(json.dumps(self.metadata, indent=2, ensure_ascii=False))
        self.model_dir = model_dir
        return model_dir

    # ---------------------------------------------------------------- inferencia
    def predict(self, image_rgb: np.ndarray, top_k: int = 5, request_id: str | None = None) -> dict:
        t0 = time.perf_counter()
        th = self.thresholds
        q = assess_quality(image_rgb, self.quality_thresholds)
        quality = {k: q[k] for k in ("ok", "issues", "brightness", "sharpness", "width", "height")}
        resp = {"request_id": request_id or str(uuid.uuid4()), "model_version": self.version, "quality": quality}
        if not q["ok"]:
            resp.update(object={"label": None, "confidence": None, "known": False},
                        angle={"degrees": None, "uncertainty_deg": None, "ambiguous": False},
                        decision="REJECT", reasons=["image_quality"],
                        robot_command={"action": "HOLD", "reason": "image_quality"}, similar=[])
        else:
            x = extract_features(image_rgb, self.feature_cfg)[None]
            out = self.model.predict(x, top_k=top_k)
            conf = float(out["confidence"][0])
            d_nn = float(out["nn_distance"][0])
            unc = float(out["angle_unc"][0])
            amb = bool(out["ambiguous"][0])
            label = str(out["label"][0])
            decision, reasons = decide(True, d_nn, conf, unc, amb, th)
            known = d_nn <= th.unknown_distance
            resp.update(
                object={"label": label if known else None, "confidence": round(conf, 4), "known": bool(known),
                        "novelty_distance": round(d_nn, 3)},
                angle={"degrees": (round(float(out["angle"][0]), 1) % 360.0) if known else None,
                       "uncertainty_deg": round(unc, 2) if known else None, "ambiguous": amb if known else False},
                decision=decision, reasons=reasons,
                robot_command=({"action": "PICK", "object": label, "rotate_deg": _rotate_cmd(out["angle"][0])}
                               if decision == "ACT" else {"action": "HOLD", "reason": reasons[0]}),
                similar=([{k: s[k] for k in ("label", "angle_deg", "similarity", "image_id")} for s in out["similar"][0]]
                         if out["similar"] else []),
            )
        resp["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return resp

    def set_thresholds(self, **kw) -> dict:
        """Actualización atómica: se construye y valida el objeto nuevo ANTES de reemplazar el vigente."""
        d = self.thresholds.to_dict()
        d.update({k: v for k, v in kw.items() if v is not None})
        new = DecisionThresholds(**d)  # lanza ValueError si algún valor es NaN/inf/fuera de rango
        self.thresholds = new
        self.metadata["thresholds"] = new.to_dict()
        return new.to_dict()

    # ---------------------------------------------------------------- alta de SKU nuevo
    def stage_sku(self, sku: str, images: list[np.ndarray], angles: list[float],
                  staging_root: str | Path) -> dict:
        """Crea un candidato en staging que incluye el SKU. No toca el modelo activo."""
        issues: list[str] = []
        angles = [float(a) % 360.0 for a in angles]
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,48}", sku or ""):
            issues.append("invalid_sku_name")
        if len(images) != len(angles):
            raise ValueError("files y angles deben tener la misma longitud")
        if sku in set(map(str, self.model.classes_)):
            issues.append("sku_already_known")
        n = len(images)
        if n < MIN_VIEWS_NEW_SKU:
            issues.append(f"too_few_views(<{MIN_VIEWS_NEW_SKU})")
        sa = np.sort(np.unique(np.round(angles, 1)))
        max_gap = float(np.max(np.diff(np.r_[sa, sa[0] + 360.0]))) if len(sa) > 1 else 360.0
        coverage = 360.0 - max_gap
        if coverage < MIN_COVERAGE_DEG:
            issues.append(f"insufficient_angular_coverage({coverage:.0f}<{MIN_COVERAGE_DEG:.0f})")
        qual = [assess_quality(im, self.quality_thresholds) for im in images]
        bad_q = sum(not q["ok"] for q in qual)
        if bad_q:
            issues.append(f"bad_quality_views({bad_q})")
        metrics: dict = {"n_views": n, "angular_coverage_deg": coverage, "max_angular_gap_deg": max_gap}
        if n >= 2:
            X = np.stack([extract_features(im, self.feature_cfg) for im in images])
            Zn = self.model.transform(X)
            # leave-one-out sobre las vistas nuevas: clase por 1-NN contra (referencias + otras vistas nuevas)
            # y ángulo por recuperación entre las otras vistas del SKU
            loo_ok, errs = [], []
            for i in range(n):
                others = np.delete(np.arange(n), i)
                d_new = np.linalg.norm(Zn[others] - Zn[i], axis=1)
                d_old = np.linalg.norm(self.model.ref_Z_ - Zn[i], axis=1)
                loo_ok.append(d_new.min() < d_old.min())
                r = estimate_angle(d_new, np.asarray(angles)[others])
                errs.append(float(circular_error(r["degrees"], angles[i])))
            errs = np.array(errs)
            nearest_known = [str(self.model.ref_labels_[np.argmin(np.linalg.norm(self.model.ref_Z_ - z, axis=1))]) for z in Zn]
            metrics.update(loo_class_accuracy=float(np.mean(loo_ok)), loo_angle_mae_deg=float(errs.mean()),
                           loo_angle_within_10=float((errs <= 10).mean()),
                           most_confusable_known=max(set(nearest_known), key=nearest_known.count))
            if np.mean(loo_ok) < 0.9:
                issues.append("confusable_with_known_sku")
        sufficient = not issues
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        base = self.version.split("+")[0]
        candidate_version = f"{base}+{sku}.{ts}"
        result = {"candidate_version": candidate_version, "sku": sku, "n_views": n, "sufficient": sufficient,
                  "issues": issues, "metrics": metrics}
        if n >= 2 and "invalid_sku_name" not in issues and "sku_already_known" not in issues:
            import copy

            model = copy.deepcopy(self.model)
            ids = np.array([f"{sku}__{int(round(a)) % 360}" for a in angles])
            model.add_class(X, sku, np.asarray(angles), ids)
            meta = copy.deepcopy(self.metadata)
            meta.pop("model_sha256", None)
            meta.update(version=candidate_version, parent_version=self.version, stage="staging",
                        created_at=datetime.now(timezone.utc).isoformat(), classes=[str(c) for c in model.classes_],
                        staged_sku=result)
            cand = VisionPredictor(model, meta)
            cand.thresholds = self.thresholds
            out_dir = Path(staging_root) / candidate_version
            refs = out_dir / "refs"
            refs.mkdir(parents=True, exist_ok=True)
            for im, iid in zip(images, ids):
                Image.fromarray(np.asarray(im, dtype=np.uint8)).save(refs / f"{iid}.png")
            cand.save(out_dir)
            result["staging_dir"] = str(out_dir)
        return result


def promote_candidate(candidate_version: str, models_root: str | Path) -> Path:
    """Copia models/staging/<cand> → models/<cand> y lo marca como promovido. Devuelve la ruta nueva."""
    models_root = Path(models_root)
    if not re.fullmatch(r"[A-Za-z0-9_.+\-]{1,128}", candidate_version):
        raise ValueError("candidate_version inválido")
    src = models_root / "staging" / candidate_version
    if not (src / "metadata.json").exists():
        raise FileNotFoundError(candidate_version)
    dst = models_root / candidate_version
    if dst.exists():
        raise FileExistsError(f"la versión {candidate_version} ya está publicada; no se sobrescribe")
    shutil.copytree(src, dst)
    from .data import sha256_file

    meta = json.loads((dst / "metadata.json").read_text())
    meta.update(stage="production", promoted_at=datetime.now(timezone.utc).isoformat(),
                model_sha256=sha256_file(dst / "model.joblib"))
    (dst / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return dst
