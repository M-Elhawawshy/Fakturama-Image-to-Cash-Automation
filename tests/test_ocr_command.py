"""The `ocr` command, with recorded OCR output (offline, no engine run)."""

from __future__ import annotations

import json

from PIL import Image

from fakturama_agent.cli import cmd_ocr
from fakturama_agent.extract.ocr import OcrBox, OcrResult
from fakturama_agent.extract.ocr_report import reading_order

from .conftest import RAW_FIXTURE
from .test_pipeline import SAMPLE, recorded_ocr


def test_lists_boxes_in_reading_order(capsys):
    assert cmd_ocr(SAMPLE, ocr=recorded_ocr) == 0
    out = capsys.readouterr().out
    assert "78 text boxes" in out
    lines = out.splitlines()
    ref = next(i for i, line in enumerate(lines) if line.endswith("WEB-2026-0714-A17"))
    date = next(i for i, line in enumerate(lines) if line.endswith("2026-07-14"))
    assert ref < date  # same visual line, left to right


def test_reading_order_groups_lines():
    boxes = (
        OcrBox("right", 1.0, (500, 103, 600, 120)),
        OcrBox("below", 1.0, (10, 200, 60, 220)),
        OcrBox("left", 1.0, (10, 100, 60, 120)),
    )
    assert [b.text for b in reading_order(boxes)] == ["left", "right", "below"]


def test_check_ok(capsys):
    assert cmd_ocr(SAMPLE, check=RAW_FIXTURE, ocr=recorded_ocr) == 0
    assert "42/42 values confirmed; 0 issue(s)" in capsys.readouterr().out


def test_check_reports_conflict_with_exit_2(tmp_path, capsys):
    raw = json.loads(RAW_FIXTURE.read_text(encoding="utf-8"))
    raw["items"][0]["sku"] = "CHR-ERG-O1"
    bad = tmp_path / "raw.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")

    assert cmd_ocr(SAMPLE, check=bad, ocr=recorded_ocr) == 2
    out = capsys.readouterr().out
    assert "CONFLICT   CHR-ERG-O1  [CHR-ERG-01]" in out
    assert "issue:   [ocr_conflict] items[0].sku" in out


def test_overlay_and_json(tmp_path):
    overlay, json_out = tmp_path / "overlay.png", tmp_path / "ocr.json"
    assert cmd_ocr(SAMPLE, overlay=overlay, json_out=json_out, ocr=recorded_ocr) == 0
    assert Image.open(overlay).size == Image.open(SAMPLE).size
    assert OcrResult.load(json_out) == recorded_ocr(b"")


def test_unreadable_image(tmp_path, capsys):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"nope")
    assert cmd_ocr(bad, ocr=recorded_ocr) == 1
    assert "OCR failed" in capsys.readouterr().err
