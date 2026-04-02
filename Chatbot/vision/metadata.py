"""
metadata.py — EXIF / GPS extraction.
No ML model needed — pure Pillow + exifread.
RAM: ~10 MB (Pillow already a dep).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ExifTags


@dataclass
class ImageMetadata:
    date_taken: Optional[datetime] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    width: int = 0
    height: int = 0
    raw: dict = field(default_factory=dict)


def _dms_to_decimal(dms, ref: str) -> float:
    """Convert DMS tuple → decimal degrees."""
    deg, mn, sec = dms
    # Each value may be an IFDRational
    deg = float(deg)
    mn = float(mn)
    sec = float(sec)
    decimal = deg + mn / 60 + sec / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def extract(image_path: str | Path) -> ImageMetadata:
    meta = ImageMetadata()
    path = Path(image_path)

    try:
        img = Image.open(path)
        meta.width, meta.height = img.size

        exif_raw = img._getexif()
        if not exif_raw:
            return meta

        # Map numeric tag IDs → names
        exif: dict = {
            ExifTags.TAGS.get(k, k): v
            for k, v in exif_raw.items()
        }
        meta.raw = {k: str(v) for k, v in exif.items()}

        # Date
        for date_tag in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
            if date_tag in exif:
                try:
                    meta.date_taken = datetime.strptime(
                        exif[date_tag], "%Y:%m:%d %H:%M:%S"
                    )
                    break
                except ValueError:
                    pass

        # Camera
        meta.camera_make = exif.get("Make")
        meta.camera_model = exif.get("Model")

        # GPS
        gps_info = exif.get("GPSInfo")
        if gps_info:
            gps: dict = {
                ExifTags.GPSTAGS.get(k, k): v
                for k, v in gps_info.items()
            }
            if "GPSLatitude" in gps and "GPSLatitudeRef" in gps:
                meta.gps_lat = _dms_to_decimal(
                    gps["GPSLatitude"], gps["GPSLatitudeRef"]
                )
            if "GPSLongitude" in gps and "GPSLongitudeRef" in gps:
                meta.gps_lon = _dms_to_decimal(
                    gps["GPSLongitude"], gps["GPSLongitudeRef"]
                )
    except Exception:
        pass  # Corrupt / no EXIF — return whatever we have

    return meta
