# vision — lightweight photo analysis pipeline for Pi 3 B+
# All inference uses ONNX Runtime (no PyTorch).
# Entry point: pipeline.py  →  worker.py (background queue)

from .pipeline import VisionPipeline

__all__ = ["VisionPipeline"]
