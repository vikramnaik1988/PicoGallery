"""
object_detector.py — Object detection + colour/size attribute extraction.

Model : MobileNet SSD v1  (Caffe, ~23 MB) — 20 PASCAL VOC classes.
        Loaded via cv2.dnn (no onnxruntime — armv7l safe).

Attributes derived without a separate model:
  - Dominant colour  → HSV binning on object crop
  - Relative size    → bbox area / image area → small / medium / large

RAM: ~25 MB model + OpenCV DNN backend (shared with face detector).
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MODELS_DIR = Path(__file__).parent / "models"

_PROTOTXT_URL = (
    "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/"
    "deploy.prototxt"
)
_CAFFEMODEL_URL = (
    "https://drive.google.com/uc?export=download&id=0B3gersZ2cHIxRm5PMWRoTkdHdHc"
)
# Mirror that works without auth:
_CAFFEMODEL_MIRROR = (
    "https://github.com/djmv/MobilNet_SSD_opencv/raw/master/"
    "MobileNetSSD_deploy.caffemodel"
)
_PROTOTXT_PATH  = MODELS_DIR / "MobileNetSSD_deploy.prototxt"
_CAFFEMODEL_PATH = MODELS_DIR / "MobileNetSSD_deploy.caffemodel"

# 21 classes (0 = background)
VOC_LABELS = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle",
    "bus", "car", "cat", "chair", "cow", "diningtable", "dog", "horse",
    "motorbike", "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor",
]

_COLOUR_BUCKETS = [
    ("red",    [0, 10]),
    ("orange", [11, 20]),
    ("yellow", [21, 35]),
    ("green",  [36, 85]),
    ("cyan",   [86, 100]),
    ("blue",   [101, 130]),
    ("purple", [131, 150]),
    ("pink",   [151, 170]),
    ("red",    [171, 179]),
]

_net = None


def _ensure_model() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if not _PROTOTXT_PATH.exists() or not _CAFFEMODEL_PATH.exists():
        print(f"[vision] MobileNet SSD models not found — copy to {MODELS_DIR}. Object detection disabled.")


def _load() -> None:
    global _net
    if _net is not None:
        return
    _ensure_model()
    if _PROTOTXT_PATH.exists() and _CAFFEMODEL_PATH.exists():
        _net = cv2.dnn.readNetFromCaffe(
            str(_PROTOTXT_PATH), str(_CAFFEMODEL_PATH)
        )


@dataclass
class DetectedObject:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]   # x, y, w, h
    dominant_colour: str = ""
    size_class: str = ""              # small / medium / large


def detect(image_bgr: np.ndarray, conf_threshold: float = 0.4) -> list[DetectedObject]:
    _load()
    if _net is None:
        return []

    h, w = image_bgr.shape[:2]
    blob = cv2.dnn.blobFromImage(
        cv2.resize(image_bgr, (300, 300)),
        0.007843, (300, 300), 127.5,
    )
    _net.setInput(blob)
    detections = _net.forward()  # (1, 1, N, 7)

    objects: list[DetectedObject] = []
    img_area = h * w or 1

    for i in range(detections.shape[2]):
        conf = float(detections[0, 0, i, 2])
        if conf < conf_threshold:
            continue
        cls_idx = int(detections[0, 0, i, 1])
        label = VOC_LABELS[cls_idx] if cls_idx < len(VOC_LABELS) else str(cls_idx)

        x1 = max(0, int(detections[0, 0, i, 3] * w))
        y1 = max(0, int(detections[0, 0, i, 4] * h))
        x2 = min(w, int(detections[0, 0, i, 5] * w))
        y2 = min(h, int(detections[0, 0, i, 6] * h))
        if x2 <= x1 or y2 <= y1:
            continue

        bbox_w, bbox_h = x2 - x1, y2 - y1
        crop = image_bgr[y1:y2, x1:x2]

        objects.append(DetectedObject(
            label=label,
            confidence=conf,
            bbox=(x1, y1, bbox_w, bbox_h),
            dominant_colour=_dominant_colour(crop),
            size_class=_size_class(bbox_w * bbox_h, img_area),
        ))

    return objects


def _dominant_colour(crop_bgr: np.ndarray) -> str:
    if crop_bgr.size == 0:
        return "unknown"
    small = cv2.resize(crop_bgr, (32, 32))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    pixels = hsv.reshape(-1, 3)

    mask = (pixels[:, 1] > 40) & (pixels[:, 2] > 40)
    coloured = pixels[mask]
    if len(coloured) == 0:
        mean_v = pixels[:, 2].mean()
        return "white" if mean_v > 180 else "black"

    counts: dict[str, int] = {}
    for h_val in coloured[:, 0]:
        for name, (lo, hi) in _COLOUR_BUCKETS:
            if lo <= h_val <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    return max(counts, key=counts.get) if counts else "unknown"


def _size_class(area: int, img_area: int) -> str:
    ratio = area / img_area
    if ratio < 0.05:
        return "small"
    if ratio < 0.25:
        return "medium"
    return "large"
