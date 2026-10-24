"""Pruebas de la librería de visión: métricas circulares, protocolos (tamaños y no-fuga), calidad, decisión."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest

from src.vision.angle import circular_error, circular_mean, circular_std, estimate_angle
from src.vision.data import ANGLES, blind_arc_starts, protocol_split, unknown_objects, validation_split
from src.vision.quality import assess_quality
from src.vision.reject import DecisionThresholds, decide

LABELS = np.repeat(np.arange(1, 101), 72)
ANG = np.tile(ANGLES, 100)


def test_circular_error_wraps():
    assert circular_error(355, 5) == pytest.approx(10)
    assert circular_error(0, 180) == pytest.approx(180)
    assert circular_error(90, 90) == 0
    np.testing.assert_allclose(circular_error([10, 350], [350, 10]), [20, 20])


def test_circular_mean_and_std():
    assert circular_mean([350, 10]) == pytest.approx(0, abs=1e-6) or circular_mean([350, 10]) == pytest.approx(360)
    assert circular_mean([80, 100]) == pytest.approx(90)
    assert circular_std([30, 30, 30]) == pytest.approx(0, abs=1e-3)
    assert circular_std([0, 90, 180, 270]) > 100


def test_estimate_angle_interpolates_and_flags_ambiguity():
    ref = np.arange(0, 360, 10.0)
    d = circular_error(ref, 15) + 1.0  # query a 15°: equidistante de 10 y 20
    r = estimate_angle(d, ref)
    assert circular_error(r["degrees"], 15) <= 5
    assert not r["ambiguous"]
    d2 = np.minimum(circular_error(ref, 40), circular_error(ref, 220)) + 1.0  # simetría a 180°
    assert estimate_angle(d2, ref)["ambiguous"]


@pytest.mark.parametrize("p,ntr,nte", [("A", 36, 36), ("B", 60, 12), ("C", 12, 60)])
def test_protocol_sizes_per_object(p, ntr, nte):
    sp = protocol_split(LABELS, ANG, p)
    assert len(np.intersect1d(sp["train"], sp["test"])) == 0
    assert len(sp["train"]) + len(sp["test"]) == 7200
    for o in (1, 50, 100):
        assert (LABELS[sp["train"]] == o).sum() == ntr
        assert (LABELS[sp["test"]] == o).sum() == nte


def test_protocol_b_arc_is_contiguous_60():
    sp = protocol_split(LABELS, ANG, "B")
    starts = blind_arc_starts()
    for o in (1, 7, 99):
        test_angles = np.sort(ANG[sp["test"]][LABELS[sp["test"]] == o])
        expected = np.sort((starts[o] + np.arange(0, 60, 5)) % 360)
        np.testing.assert_array_equal(test_angles, expected)


def test_protocol_d_unknown_objects_never_in_train():
    sp = protocol_split(LABELS, ANG, "D")
    unk = unknown_objects()
    assert len(unk) == 10 and len(set(unk)) == 10
    assert not np.isin(LABELS[sp["train"]], unk).any()
    assert len(np.unique(LABELS[sp["train"]])) == 90
    assert sp["test_is_unknown"].sum() == 720
    assert len(np.intersect1d(sp["train"], sp["test"])) == 0


@pytest.mark.parametrize("p", ["A", "B", "C", "D"])
def test_validation_uses_only_training_views(p):
    sp = protocol_split(LABELS, ANG, p)
    vs = validation_split(LABELS, ANG, p)
    used = np.concatenate([vs["fit"], vs["val"]])
    assert np.isin(used, sp["train"]).all(), "la validación no puede tocar el test"
    assert len(np.intersect1d(vs["fit"], vs["val"])) == 0


def test_quality_gate():
    rng = np.random.default_rng(0)
    good = np.zeros((128, 128, 3), np.uint8)
    good[32:96, 32:96] = rng.integers(60, 255, (64, 64, 3))
    assert assess_quality(good)["ok"]
    dark = np.zeros((128, 128, 3), np.uint8)
    q = assess_quality(dark)
    assert not q["ok"] and "too_dark" in q["issues"]
    bright = np.full((128, 128, 3), 250, np.uint8)
    assert "too_bright" in assess_quality(bright)["issues"]
    blurry = np.zeros((128, 128, 3), np.uint8)
    blurry[:, :] = np.linspace(40, 120, 128, dtype=np.uint8)[None, :, None]
    assert "blurry" in assess_quality(blurry)["issues"]
    assert "too_small" in assess_quality(good[:16, :16])["issues"]


def test_decision_rule_order():
    th = DecisionThresholds(min_class_confidence=0.5, max_angle_uncertainty_deg=10, unknown_distance=20)
    assert decide(False, 1, 0.99, 1, False, th) == ("REJECT", ["image_quality"])
    assert decide(True, 25, 0.99, 1, False, th) == ("REJECT", ["unknown_object"])
    assert decide(True, 5, 0.3, 1, False, th)[0] == "REVIEW"
    assert decide(True, 5, 0.9, 15, False, th) == ("REVIEW", ["high_angle_uncertainty"])
    assert decide(True, 5, 0.9, 5, True, th) == ("REVIEW", ["rotational_ambiguity"])
    assert decide(True, 5, 0.9, 5, False, th) == ("ACT", [])
