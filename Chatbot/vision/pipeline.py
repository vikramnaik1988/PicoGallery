"""
pipeline.py — Main photo analysis pipeline.

Runs all analysis steps sequentially to keep peak RAM low on Pi 3 B+.
Each step loads its model once on first call; subsequent calls reuse it.

Usage:
    from vision.pipeline import VisionPipeline
    pipe = VisionPipeline()
    result = pipe.analyse("/path/to/photo.jpg")
    # result is a PhotoAnalysis dataclass — store in DB
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .face_detector import DetectedFace, detect_and_embed
from .metadata import ImageMetadata, extract as extract_metadata
from .object_detector import DetectedObject, detect as detect_objects
from .ocr import extract_text
from .scene_classifier import classify as classify_scene


@dataclass
class PhotoAnalysis:
    # Source
    asset_id: str = ""
    image_path: str = ""
    width: int = 0
    height: int = 0

    # Metadata
    date_taken: Optional[object] = None   # datetime | None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None

    # Vision
    faces: list[DetectedFace] = field(default_factory=list)
    objects: list[DetectedObject] = field(default_factory=list)
    scene: str = ""
    scene_confidence: float = 0.0
    ocr_text: str = ""

    # Derived tag sets (for fast DB querying)
    object_labels: list[str] = field(default_factory=list)     # ["hat", "dog"]
    colour_tags: list[str] = field(default_factory=list)       # ["red", "blue"]
    size_tags: list[str] = field(default_factory=list)         # ["large"]
    all_tags: list[str] = field(default_factory=list)          # union of all

    # Performance
    elapsed_ms: float = 0.0
    error: Optional[str] = None


class VisionPipeline:
    """
    Sequential photo analysis pipeline.

    All steps run in the same process.  Because we use ONNX Runtime
    (not PyTorch), peak RAM on Pi 3 B+ stays under ~265 MB for this
    module alone.
    """

    def __init__(
        self,
        run_face: bool = True,
        run_objects: bool = True,
        run_scene: bool = True,
        run_ocr: bool = True,
        face_conf: float = 0.6,
        object_conf: float = 0.4,
    ):
        self.run_face = run_face
        self.run_objects = run_objects
        self.run_scene = run_scene
        self.run_ocr = run_ocr
        self.face_conf = face_conf
        self.object_conf = object_conf

    def analyse(self, image_path: str | Path, asset_id: str = "") -> PhotoAnalysis:
        """
        Analyse a single image and return a PhotoAnalysis.
        Raises no exceptions — errors are captured in result.error.
        """
        t0 = time.monotonic()
        result = PhotoAnalysis(
            asset_id=asset_id,
            image_path=str(image_path),
        )

        try:
            path = Path(image_path)
            if not path.exists():
                result.error = f"file not found: {path}"
                return result

            # ── 1. EXIF / GPS metadata ──────────────────────────────────────
            meta: ImageMetadata = extract_metadata(path)
            result.date_taken = meta.date_taken
            result.gps_lat = meta.gps_lat
            result.gps_lon = meta.gps_lon
            result.camera_make = meta.camera_make
            result.camera_model = meta.camera_model
            result.width = meta.width
            result.height = meta.height

            # ── Load image (BGR) ────────────────────────────────────────────
            img_bgr = cv2.imread(str(path))
            if img_bgr is None:
                result.error = "cv2.imread returned None — unsupported format?"
                return result

            result.width = result.width or img_bgr.shape[1]
            result.height = result.height or img_bgr.shape[0]

            # ── 2. Face detection + embedding ───────────────────────────────
            if self.run_face:
                result.faces = detect_and_embed(img_bgr, self.face_conf)

            # ── 3. Object detection + attributes ───────────────────────────
            if self.run_objects:
                result.objects = detect_objects(img_bgr, self.object_conf)
                for obj in result.objects:
                    if obj.label not in result.object_labels:
                        result.object_labels.append(obj.label)
                    if obj.dominant_colour and obj.dominant_colour not in result.colour_tags:
                        result.colour_tags.append(obj.dominant_colour)
                    if obj.size_class and obj.size_class not in result.size_tags:
                        result.size_tags.append(obj.size_class)

            # ── 4. Scene classification ─────────────────────────────────────
            if self.run_scene:
                result.scene, result.scene_confidence = classify_scene(img_bgr)

            # ── 5. OCR ──────────────────────────────────────────────────────
            if self.run_ocr:
                result.ocr_text = extract_text(img_bgr)

            # ── 6. Build unified tag list ───────────────────────────────────
            tags = set(result.object_labels)
            tags.update(result.colour_tags)
            tags.update(result.size_tags)
            if result.scene:
                tags.add(result.scene)
            if result.camera_model:
                tags.add(result.camera_model.lower())
            result.all_tags = sorted(tags)

        except Exception as exc:
            result.error = str(exc)

        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result
