"""Local OCR pass (RapidOCR): image bytes -> text boxes.

No layout logic is built on this. The boxes only serve as an independent
witness for the characters the LLM returned (see crosscheck.py).

``rapidocr-onnxruntime`` ships its models inside the wheel, so OCR never needs
the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.metadata import version
from pathlib import Path
from typing import Any

ENGINE_PACKAGE = "rapidocr-onnxruntime"


@dataclass(frozen=True)
class OcrBox:
    text: str
    score: float
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 (axis-aligned bounds of the detected quad)


@dataclass(frozen=True)
class OcrResult:
    engine: str
    boxes: tuple[OcrBox, ...]

    def to_json(self) -> str:
        data = {"engine": self.engine, "boxes": [{"text": b.text, "score": b.score, "box": list(b.box)} for b in self.boxes]}
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> OcrResult:
        data = json.loads(text)
        boxes = tuple(OcrBox(text=b["text"], score=b["score"], box=tuple(b["box"])) for b in data["boxes"])
        return cls(engine=data["engine"], boxes=boxes)

    @classmethod
    def load(cls, path: Path) -> OcrResult:
        return cls.from_json(path.read_text(encoding="utf-8"))


def engine_name() -> str:
    return f"{ENGINE_PACKAGE} {version(ENGINE_PACKAGE)}"


@cache
def _engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _bounds(quad: Any) -> tuple[int, int, int, int]:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def run_ocr(image: bytes) -> OcrResult:
    detections, _elapsed = _engine()(image)
    boxes = tuple(
        OcrBox(text=str(text), score=round(float(score), 4), box=_bounds(quad)) for quad, text, score in detections or []
    )
    return OcrResult(engine=engine_name(), boxes=boxes)
