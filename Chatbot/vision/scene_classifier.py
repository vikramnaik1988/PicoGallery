"""
scene_classifier.py — Scene classification using MobileNetV2 ONNX via cv2.dnn.

No onnxruntime required — cv2.dnn reads ONNX natively (armv7l safe).

Model : MobileNetV2 ONNX (ImageNet pretrained, ~14 MB)
        Top-5 ImageNet classes mapped to 16 scene buckets.
        Falls back to a colour-histogram heuristic if model download fails.

RAM: ~20 MB model + OpenCV DNN backend (shared).
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import cv2
import numpy as np

MODELS_DIR = Path(__file__).parent / "models"

_ONNX_URL = (
    "https://github.com/onnx/models/raw/main/validated/vision/"
    "classification/mobilenet/model/mobilenetv2-12.onnx"
)
_ONNX_PATH = MODELS_DIR / "mobilenetv2-12.onnx"

SCENE_LABELS = [
    "beach", "mountain", "city", "forest", "indoor", "office", "street",
    "restaurant", "bedroom", "kitchen", "park", "desert", "snow",
    "stadium", "airport", "farm",
]

_HEURISTIC_ONLY = False
_net = None


def _ensure_model() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if not _ONNX_PATH.exists():
        print(f"[vision] mobilenetv2-12.onnx not found — copy to {MODELS_DIR}. Using colour heuristic for scene.")


def _load() -> None:
    global _net, _HEURISTIC_ONLY
    if _net is not None or _HEURISTIC_ONLY:
        return
    try:
        _ensure_model()
        if not _ONNX_PATH.exists():
            _HEURISTIC_ONLY = True
            return
        _net = cv2.dnn.readNetFromONNX(str(_ONNX_PATH))
    except Exception as e:
        print(f"[vision] scene model load failed ({e}), using heuristic")
        _HEURISTIC_ONLY = True


# ImageNet index → scene bucket (selected indices)
_IMAGENET_TO_SCENE: dict[int, str] = {
    **{i: "beach"    for i in [977, 978, 979, 980, 953]},
    **{i: "mountain" for i in [970, 971, 972, 579, 576]},
    **{i: "forest"   for i in [985, 984, 340, 341]},
    **{i: "city"     for i in [833, 834, 835, 836, 910, 920]},
    **{i: "snow"     for i in [973, 974, 975]},
}


def classify(image_bgr: np.ndarray) -> tuple[str, float]:
    """Return (scene_label, confidence 0–1)."""
    _load()

    if _HEURISTIC_ONLY or _net is None:
        return _heuristic(image_bgr)

    resized = cv2.resize(image_bgr, (224, 224))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
    rgb = (rgb / 127.5) - 1.0
    inp = rgb.transpose(2, 0, 1)[np.newaxis]  # NCHW

    _net.setInput(inp)
    logits = _net.forward()[0]   # cv2.dnn forward → (1000,)
    probs = _softmax(logits)

    top5 = np.argsort(probs)[::-1][:5]
    for idx in top5:
        if int(idx) in _IMAGENET_TO_SCENE:
            return _IMAGENET_TO_SCENE[int(idx)], float(probs[idx])

    return _heuristic(image_bgr)


def _heuristic(image_bgr: np.ndarray) -> tuple[str, float]:
    small = cv2.resize(image_bgr, (64, 64))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mean_h = float(hsv[:, :, 0].mean())
    mean_s = float(hsv[:, :, 1].mean())
    mean_v = float(hsv[:, :, 2].mean())

    if mean_v > 180 and 90 <= mean_h <= 130:
        return "beach", 0.5
    if 36 <= mean_h <= 85 and mean_s > 60 and mean_v < 160:
        return "forest", 0.5
    if mean_v > 210 and mean_s < 30:
        return "snow", 0.5
    if 15 <= mean_h <= 30 and mean_s > 40 and mean_v > 100:
        return "desert", 0.5
    return "indoor", 0.3


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()
