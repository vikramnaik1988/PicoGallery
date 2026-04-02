"""
face_detector.py — Face detection + 128-d embedding.

Detection : OpenCV DNN  (res10_300x300_ssd_iter_140000.caffemodel)
Embedding : MobileFaceNet ONNX via cv2.dnn  (~4 MB model, ~30 MB resident)

Both models are downloaded on first use to vision/models/.
No onnxruntime required — cv2.dnn handles ONNX natively (armv7l safe).
RAM: detection ~20 MB, embedding ~30 MB (loaded once, kept resident).
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

MODELS_DIR = Path(__file__).parent / "models"

# ── Detection model (OpenCV DNN, Caffe) ──────────────────────────────────────
_DET_PROTOTXT_URL = (
    "https://raw.githubusercontent.com/opencv/opencv/master/"
    "samples/dnn/face_detector/deploy.prototxt"
)
_DET_CAFFEMODEL_URL = (
    "https://github.com/opencv/opencv_3rdparty/raw/dnn_samples_face_detector_20170830/"
    "res10_300x300_ssd_iter_140000.caffemodel"
)
_DET_PROTOTXT = MODELS_DIR / "deploy.prototxt"
_DET_MODEL = MODELS_DIR / "res10_300x300_ssd_iter_140000.caffemodel"

# ── Embedding model (MobileFaceNet ONNX — loaded via cv2.dnn) ─────────────────
_EMB_ONNX_URL = (
    "https://github.com/sirius-ai/MobileFaceNet_TF/raw/master/"
    "onnx/MobileFaceNet.onnx"
)
_EMB_ONNX = MODELS_DIR / "MobileFaceNet.onnx"

_det_net = None
_emb_net = None


_emb_warned = False

def _ensure_models() -> None:
    global _emb_warned
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if not _DET_PROTOTXT.exists():
        print("[vision] downloading face detector prototxt…")
        urllib.request.urlretrieve(_DET_PROTOTXT_URL, _DET_PROTOTXT)
    if not _DET_MODEL.exists():
        print("[vision] downloading face detector weights…")
        urllib.request.urlretrieve(_DET_CAFFEMODEL_URL, _DET_MODEL)
    if not _EMB_ONNX.exists() and not _emb_warned:
        print(f"[vision] MobileFaceNet.onnx not found — face embedding disabled.")
        _emb_warned = True


def _load_nets() -> None:
    global _det_net, _emb_net
    _ensure_models()
    if _det_net is None:
        _det_net = cv2.dnn.readNetFromCaffe(
            str(_DET_PROTOTXT), str(_DET_MODEL)
        )
    if _emb_net is None and _EMB_ONNX.exists():
        _emb_net = cv2.dnn.readNetFromONNX(str(_EMB_ONNX))


@dataclass
class DetectedFace:
    bbox: tuple[int, int, int, int]   # x, y, w, h  (pixel coords)
    confidence: float
    embedding: list[float] = field(default_factory=list)  # 128-d unit vector


def detect_and_embed(image_bgr: np.ndarray, conf_threshold: float = 0.6) -> list[DetectedFace]:
    """Return detected faces with 128-d embeddings."""
    _load_nets()

    h, w = image_bgr.shape[:2]
    blob = cv2.dnn.blobFromImage(
        cv2.resize(image_bgr, (300, 300)), 1.0,
        (300, 300), (104.0, 177.0, 123.0),
    )
    _det_net.setInput(blob)
    detections = _det_net.forward()  # shape (1,1,N,7)

    faces: list[DetectedFace] = []
    for i in range(detections.shape[2]):
        conf = float(detections[0, 0, i, 2])
        if conf < conf_threshold:
            continue
        x1 = int(detections[0, 0, i, 3] * w)
        y1 = int(detections[0, 0, i, 4] * h)
        x2 = int(detections[0, 0, i, 5] * w)
        y2 = int(detections[0, 0, i, 6] * h)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            continue

        crop = image_bgr[y1:y2, x1:x2]
        emb = _embed_face(crop) if _emb_net is not None else []
        faces.append(
            DetectedFace(
                bbox=(x1, y1, x2 - x1, y2 - y1),
                confidence=conf,
                embedding=emb,
            )
        )
    return faces


def _embed_face(face_bgr: np.ndarray) -> list[float]:
    """Return a 128-d unit-normalised embedding for a face crop."""
    resized = cv2.resize(face_bgr, (112, 112))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
    rgb = (rgb - 127.5) / 128.0          # MobileFaceNet normalisation
    inp = rgb.transpose(2, 0, 1)[None]   # NCHW

    _emb_net.setInput(inp)
    output = _emb_net.forward()[0]       # cv2.dnn forward, take first output

    # L2 normalise
    norm = np.linalg.norm(output)
    if norm > 0:
        output = output / norm
    return output.tolist()
