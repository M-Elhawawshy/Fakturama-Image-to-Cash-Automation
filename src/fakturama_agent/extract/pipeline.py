"""Image -> validated OrderSpec, or a list of reasons for manual review.

prepare -> LLM (verbatim values) -> OCR -> cross-check -> normalize -> validate.

Every stage after the LLM runs even when an earlier one found problems, so a
single review report lists everything at once. Operational failures (no API
key, API down, unreadable file) raise ExtractionError instead: those are not
a verdict on the order and the run can simply be repeated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fakturama_agent.extract.crosscheck import crosscheck
from fakturama_agent.extract.llm import VisionExtractor
from fakturama_agent.extract.normalize import normalize
from fakturama_agent.extract.ocr import OcrResult, run_ocr
from fakturama_agent.extract.preprocess import PreparedImage, prepare_image
from fakturama_agent.extract.validate import validate
from fakturama_agent.spec.models import FieldCheck, Issue, OrderSpec, Provenance, RawExtraction

OcrFn = Callable[[bytes], OcrResult]


@dataclass
class ExtractOutcome:
    image_path: Path
    image: PreparedImage
    model_id: str
    from_cache: bool
    ocr_engine: str
    raw: RawExtraction
    checks: list[FieldCheck]
    spec: OrderSpec | None
    issues: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.spec is not None and not self.issues


def run_extract(image_path: Path, extractor: VisionExtractor, ocr: OcrFn = run_ocr) -> ExtractOutcome:
    image = prepare_image(image_path)
    llm = extractor.extract(image.data, image.mime_type)
    ocr_result = ocr(image.data)

    checked = crosscheck(llm.raw, ocr_result)
    provenance = Provenance(
        image_sha256=image.source_sha256,
        model_id=llm.model_id,
        ocr_engine=ocr_result.engine,
        field_checks=checked.checks,
    )
    normalized = normalize(llm.raw, provenance)
    issues = [*checked.issues, *normalized.issues]
    if normalized.spec is not None:
        issues += validate(normalized.spec)

    return ExtractOutcome(
        image_path=image_path,
        image=image,
        model_id=llm.model_id,
        from_cache=llm.from_cache,
        ocr_engine=ocr_result.engine,
        raw=llm.raw,
        checks=checked.checks,
        spec=normalized.spec if not issues else None,
        issues=issues,
        warnings=checked.warnings,
    )
