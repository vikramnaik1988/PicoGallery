"""
ocr.py — Text extraction from images using Tesseract.

Requires: tesseract binary on PATH  +  pytesseract Python wrapper.
On Raspberry Pi:  sudo apt install tesseract-ocr

RAM: ~60 MB (Tesseract process, not resident — spawned per call).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np


def _tesseract_available() -> bool:
    try:
        subprocess.run(
            ["tesseract", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


_AVAILABLE: bool | None = None


def extract_text(image_bgr: np.ndarray, lang: str = "eng") -> str:
    """
    Return all text found in the image, or "" if Tesseract is not installed
    or no text is found.
    """
    global _AVAILABLE
    if _AVAILABLE is None:
        _AVAILABLE = _tesseract_available()
        if not _AVAILABLE:
            print("[vision] Tesseract not found — OCR disabled. "
                  "Install with: sudo apt install tesseract-ocr")

    if not _AVAILABLE:
        return ""

    try:
        import pytesseract
    except ImportError:
        return ""

    # Pre-process: greyscale + mild sharpening improves OCR on photos
    grey = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    # Adaptive threshold helps with varied lighting
    thresh = cv2.adaptiveThreshold(
        grey, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 10,
    )

    text: str = pytesseract.image_to_string(thresh, lang=lang)
    # Strip noise: keep only lines with ≥2 real characters
    lines = [
        line.strip()
        for line in text.splitlines()
        if len(line.strip()) >= 2
    ]
    return " ".join(lines)
