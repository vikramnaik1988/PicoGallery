"""
indexer.py — Batch-index all photos in PicoGallery storage.

Runs face_detector, object_detector, scene_classifier on every photo
and stores results in vision_metadata.db via store.py.

Usage:
    python3 -m vision.indexer              # index all new photos
    python3 -m vision.indexer --reindex    # reindex everything
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running as script from Chatbot/
sys.path.insert(0, str(Path(__file__).parent.parent))

from vision.pipeline import VisionPipeline
from vision import store

# PicoGallery originals directory
ORIGINALS_DIR = os.environ.get(
    "PICO_ORIGINALS",
    "/home/admin/PicoGallery/data/storage/originals",
)

SUPPORTED = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
HEIC_EXTS = {".heic", ".heif"}


def heic_to_jpeg(heic_path: Path) -> Path | None:
    """Convert a HEIC file to a temp JPEG using heif-convert. Returns JPEG path or None."""
    import subprocess, tempfile
    tmp = Path(tempfile.mktemp(suffix=".jpg"))
    try:
        result = subprocess.run(
            ["heif-convert", str(heic_path), str(tmp)],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0 and tmp.exists():
            return tmp
    except Exception:
        pass
    return None


def find_photos(root: str) -> list[Path]:
    photos = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if Path(f).suffix.lower() in SUPPORTED:
                photos.append(Path(dirpath) / f)
    return sorted(photos)


def run(reindex: bool = False) -> None:
    photos = find_photos(ORIGINALS_DIR)
    if not photos:
        print(f"[indexer] No photos found in {ORIGINALS_DIR}")
        return

    pipe = VisionPipeline()
    total = len(photos)
    done = 0
    skipped = 0

    print(f"[indexer] Found {total} photos.")

    for photo in photos:
        path_str = str(photo)

        if not reindex and store.already_indexed(path_str):
            skipped += 1
            continue

        # Convert HEIC to JPEG for cv2
        tmp_jpeg = None
        analyse_path = path_str
        if photo.suffix.lower() in HEIC_EXTS:
            tmp_jpeg = heic_to_jpeg(photo)
            if tmp_jpeg:
                analyse_path = str(tmp_jpeg)
            else:
                print(f"[indexer] [{done+1}/{total-skipped}] {photo.name} — skipped (HEIC conversion failed)")
                done += 1
                store.upsert(path=path_str, asset_id=photo.stem, tags=[])
                continue

        r = pipe.analyse(analyse_path, asset_id=photo.stem)

        # Clean up temp JPEG
        if tmp_jpeg and tmp_jpeg.exists():
            tmp_jpeg.unlink()

        store.upsert(
            path=path_str,
            asset_id=photo.stem,
            date_taken=r.date_taken,
            gps_lat=r.gps_lat,
            gps_lon=r.gps_lon,
            camera_model=r.camera_model,
            faces=len(r.faces),
            scene=r.scene,
            ocr_text=r.ocr_text,
            tags=r.all_tags,
        )

        done += 1
        print(
            f"[indexer] [{done}/{total - skipped}] {photo.name} — "
            f"faces={len(r.faces)} objects={r.object_labels} "
            f"scene={r.scene} ({round(r.elapsed_ms)}ms)"
            + (f" ⚠ {r.error}" if r.error else "")
        )

    print(f"[indexer] Done. Indexed: {done}, Skipped (already done): {skipped}")
    print(f"[indexer] Total in DB: {store.count()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reindex", action="store_true", help="Reindex all photos")
    args = parser.parse_args()
    run(reindex=args.reindex)
