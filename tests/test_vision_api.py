"""Pruebas del servicio de visión con TestClient sobre un modelo pequeño entrenado en el momento."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import io
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.vision.data import DEFAULT_DATA_DIR
from src.vision.features import FeatureConfig, extract_batch
from src.vision.inference import VisionPredictor
from src.vision.models import VisionModel

pytestmark = pytest.mark.skipif(not DEFAULT_DATA_DIR.exists(), reason="dataset COIL-100 no disponible")
OBJS = [1, 2, 3, 4, 5, 6]


def _png(arr: np.ndarray) -> bytes:
    b = io.BytesIO()
    Image.fromarray(arr).save(b, format="PNG")
    return b.getvalue()


def _load(o, a):
    return np.asarray(Image.open(DEFAULT_DATA_DIR / f"obj{o}__{a}.png").convert("RGB"))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("models")
    imgs, labels, angles, ids = [], [], [], []
    for o in OBJS:
        for a in range(0, 360, 10):
            imgs.append(_load(o, a)); labels.append(f"obj{o}"); angles.append(a); ids.append(f"obj{o}__{a}")
    X = extract_batch(np.stack(imgs))
    m = VisionModel("logreg", {"C": 1.0}, n_pca=40).fit(X, np.array(labels), np.array(angles), np.array(ids))
    meta = {"version": "vision-test", "feature_config": FeatureConfig().to_dict(), "classes": sorted(set(labels)),
            "thresholds": {"min_class_confidence": 0.2, "max_angle_uncertainty_deg": 20.0, "unknown_distance": 1e6}}
    VisionPredictor(m, meta).save(root / "vision-test")
    import services.vision_api.app as api

    api.settings.model_dir = str(root / "vision-test")
    api.settings.models_root = str(root)
    api.settings.admin_token = "test-token"
    api.settings.allow_fault_injection = True
    api.settings.fault_timeout_s = 0.01
    with TestClient(api.app) as c:
        yield c


def test_health_and_model(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["model_version"] == "vision-test"
    assert client.get("/v1/model").json()["n_classes"] == len(OBJS)


def test_predict_happy_path_contract(client):
    r = client.post("/v1/predict", files={"file": ("x.png", _png(_load(3, 45)), "image/png")}, data={"top_k": "3"})
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("request_id", "model_version", "quality", "object", "angle", "decision", "reasons",
              "robot_command", "similar", "latency_ms"):
        assert k in body
    assert body["object"]["label"] == "obj3"
    assert body["decision"] in {"ACT", "REVIEW", "REJECT"}
    assert len(body["similar"]) == 3 and set(body["similar"][0]) == {"label", "angle_deg", "similarity", "image_id"}
    if body["decision"] == "ACT":
        assert body["robot_command"]["action"] == "PICK"
        assert abs(((body["angle"]["degrees"] - 45 + 180) % 360) - 180) <= 10
    else:
        assert body["robot_command"]["action"] == "HOLD"


def test_predict_quality_reject(client):
    dark = np.zeros((128, 128, 3), np.uint8)
    body = client.post("/v1/predict", files={"file": ("d.png", _png(dark), "image/png")}).json()
    assert body["decision"] == "REJECT" and body["reasons"] == ["image_quality"]
    assert body["robot_command"] == {"action": "HOLD", "reason": "image_quality"}


def test_predict_invalid_inputs(client):
    assert client.post("/v1/predict", files={"file": ("x.png", b"not an image", "image/png")}).status_code == 400
    assert client.post("/v1/predict", files={"file": ("x.txt", b"hola", "text/plain")}).status_code == 415
    big = b"\x89PNG" + b"0" * (5 * 1024 * 1024 + 10)
    assert client.post("/v1/predict", files={"file": ("x.png", big, "image/png")}).status_code == 413


def test_fault_injection(client):
    r = client.post("/v1/predict", files={"file": ("x.png", _png(_load(1, 0)), "image/png")},
                    headers={"X-Fault": "500"})
    assert r.status_code == 500
    r = client.post("/v1/predict", files={"file": ("x.png", _png(_load(1, 0)), "image/png")},
                    headers={"X-Fault": "timeout"})
    assert r.status_code == 200  # el timeout simulado solo retrasa (fault_timeout_s pequeño en tests)


def test_images_endpoint(client):
    assert client.get("/v1/images/obj1__0").status_code == 200
    assert client.get("/v1/images/..%2Fetc").status_code in (400, 404)
    assert client.get("/v1/images/obj1__999").status_code == 404


def test_admin_thresholds_and_auth(client):
    assert client.put("/v1/admin/thresholds", json={"min_class_confidence": 0.3}).status_code == 401
    r = client.put("/v1/admin/thresholds", json={"min_class_confidence": 0.3}, headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200 and r.json()["min_class_confidence"] == 0.3


def test_stage_and_promote_sku(client):
    o = 7  # objeto no visto por el modelo de prueba
    angs = list(range(0, 360, 20))
    files = [("files", (f"{a}.png", _png(_load(o, a)), "image/png")) for a in angs]
    r = client.post("/v1/skus/stage", data={"sku": "sku-new", "angles": json.dumps(angs)}, files=files,
                    headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_views"] == 18 and "metrics" in body
    assert body["sufficient"], body["issues"]
    r = client.post("/v1/admin/promote", json={"candidate_version": body["candidate_version"]},
                    headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200 and r.json()["active_version"] == body["candidate_version"]
    pr = client.post("/v1/predict", files={"file": ("x.png", _png(_load(o, 30)), "image/png")}).json()
    assert pr["object"]["label"] == "sku-new"
    # pocas vistas → insuficiente y no promovible
    files = [("files", (f"{a}.png", _png(_load(8, a)), "image/png")) for a in (0, 10, 20)]
    r = client.post("/v1/skus/stage", data={"sku": "sku-few", "angles": json.dumps([0, 10, 20])}, files=files,
                    headers={"X-Admin-Token": "test-token"}).json()
    assert not r["sufficient"] and any("too_few_views" in i for i in r["issues"])
    assert client.post("/v1/admin/promote", json={"candidate_version": r["candidate_version"]},
                       headers={"X-Admin-Token": "test-token"}).status_code == 409
