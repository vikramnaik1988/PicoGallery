"""
worker.py — Background queue worker for photo analysis.

Reads job paths from a queue file (one absolute path per line).
Processes photos one at a time to keep RAM flat on Pi 3 B+.
Results are written back to the PicoGallery database via a simple
HTTP POST to the internal picogallery API (localhost:3456).

Run as a long-lived process:
    python -m vision.worker

Or import and drive manually:
    from vision.worker import Worker
    w = Worker()
    w.process_one("/path/to/photo.jpg", asset_id="ast_abc123")
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from .pipeline import VisionPipeline, PhotoAnalysis

# ── Configuration (override via environment variables) ───────────────────────
QUEUE_FILE = Path(os.environ.get("VISION_QUEUE", "/tmp/vision_queue.txt"))
API_BASE = os.environ.get("PICO_API", "http://localhost:3456/api/v1")
API_KEY = os.environ.get("PICO_API_KEY", "")          # set in .env
POLL_INTERVAL = float(os.environ.get("VISION_POLL", "5"))   # seconds


class Worker:
    def __init__(self):
        self.pipeline = VisionPipeline()

    def process_one(self, image_path: str, asset_id: str = "") -> PhotoAnalysis:
        """Analyse one image and POST tags/embeddings to PicoGallery."""
        result = self.pipeline.analyse(image_path, asset_id=asset_id)

        if result.error:
            print(f"[vision] error processing {image_path}: {result.error}")
            return result

        print(
            f"[vision] {Path(image_path).name} → "
            f"{len(result.faces)} face(s), "
            f"{len(result.objects)} object(s), "
            f"scene={result.scene}, "
            f"{result.elapsed_ms:.0f} ms"
        )

        self._post_tags(result)
        return result

    def _post_tags(self, result: PhotoAnalysis) -> None:
        """POST analysis results to PicoGallery internal API."""
        if not result.asset_id:
            return

        payload = json.dumps({
            "asset_id": result.asset_id,
            "tags": result.all_tags,
            "scene": result.scene,
            "scene_confidence": result.scene_confidence,
            "ocr_text": result.ocr_text,
            "faces": [
                {
                    "bbox": list(f.bbox),
                    "confidence": f.confidence,
                    "embedding": f.embedding,
                }
                for f in result.faces
            ],
            "objects": [
                {
                    "label": o.label,
                    "confidence": o.confidence,
                    "dominant_colour": o.dominant_colour,
                    "size_class": o.size_class,
                }
                for o in result.objects
            ],
            "gps_lat": result.gps_lat,
            "gps_lon": result.gps_lon,
            "date_taken": result.date_taken.isoformat() if result.date_taken else None,
            "camera_make": result.camera_make,
            "camera_model": result.camera_model,
        }).encode()

        url = f"{API_BASE}/assets/{result.asset_id}/vision-tags"
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 204):
                    print(f"[vision] API returned {resp.status} for {result.asset_id}")
        except Exception as e:
            print(f"[vision] failed to POST tags for {result.asset_id}: {e}")

    def run_forever(self) -> None:
        """Poll QUEUE_FILE for new jobs. One path per line, consumed on read."""
        print(f"[vision] worker started, queue={QUEUE_FILE}")
        while True:
            jobs = self._drain_queue()
            for line in jobs:
                parts = line.strip().split("|", 1)
                path = parts[0]
                asset_id = parts[1] if len(parts) > 1 else ""
                if path:
                    self.process_one(path, asset_id=asset_id)
            if not jobs:
                time.sleep(POLL_INTERVAL)

    def _drain_queue(self) -> list[str]:
        """Atomically read and clear the queue file."""
        if not QUEUE_FILE.exists():
            return []
        try:
            lines = QUEUE_FILE.read_text().splitlines()
            QUEUE_FILE.write_text("")   # clear
            return [l for l in lines if l.strip()]
        except Exception:
            return []


if __name__ == "__main__":
    Worker().run_forever()
