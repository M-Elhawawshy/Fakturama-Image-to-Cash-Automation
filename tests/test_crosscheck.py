from pathlib import Path

import pytest

from fakturama_agent.extract.crosscheck import check_value, crosscheck, is_critical
from fakturama_agent.extract.ocr import OcrBox, OcrResult
from fakturama_agent.spec.models import CheckStatus, RawExtraction

OCR_FIXTURE = Path(__file__).parent / "fixtures" / "ocr_order_001.json"


@pytest.fixture
def ocr() -> OcrResult:
    """Real RapidOCR output for samples/order_001.png, recorded once."""
    return OcrResult.load(OCR_FIXTURE)


def _boxes(*texts: str) -> tuple[OcrBox, ...]:
    return tuple(OcrBox(text=t, score=1.0, box=(0, 0, 1, 1)) for t in texts)


def _with_item(raw: RawExtraction, index: int, **changes) -> RawExtraction:
    items = list(raw.items)
    items[index] = items[index].model_copy(update=changes)
    return raw.model_copy(update={"items": items})


def _issues(result) -> list[tuple[str, str]]:
    return [(i.rule, i.field) for i in result.issues]


# --- check_value -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "texts"),
    [
        ("Northstar Office GmbH", ["NorthstarOfficeGmbH"]),  # OCR dropped spaces
        ("+49 30 5550 1420", ["+493055501420"]),
        ("EUR 570.00", ["EUR570.00"]),
        ("10117", ["10117Berlin"]),  # digit token glued to letters is still a token
        ("Berlin", ["10117 Berlin"]),
        ("2", ["Qty", "2"]),
    ],
)
def test_confirmed_ignoring_whitespace(value, texts):
    assert check_value(value, _boxes(*texts)) == (CheckStatus.CONFIRMED, None)


@pytest.mark.parametrize(
    ("value", "texts"),
    [
        ("2", ["2026-07-14"]),  # not a whole token
        ("40.00", ["140.00"]),
        ("PAID", ["UNPAID"]),
        ("10117", ["101170"]),
        ("10171", ["10117 Berlin"]),  # transposed digits are not look-alikes
    ],
)
def test_unconfirmed_respects_token_boundaries(value, texts):
    assert check_value(value, _boxes(*texts)) == (CheckStatus.UNCONFIRMED, None)


@pytest.mark.parametrize(
    ("value", "ocr_text"),
    [
        ("CHR-ERG-O1", "CHR-ERG-01"),  # LLM read O, OCR read 0
        ("CHR-ERG-01", "CHR-ERG-O1"),  # and the other way round
        ("CUST-1OO7", "CUST-1007"),
        ("WEB-2026-0714-A17", "WEB-2026-07l4-A17"),
        ("MAT-DESK-02", "MAT-DE5K-02"),
        ("marta.klein@example.test", "Marta.klein@example.test"),  # case-only difference
        ("WEB-2026-0714-A17", "WEB–2026-0714-A17"),  # en dash
    ],
)
def test_look_alikes_are_conflicts(value, ocr_text):
    assert check_value(value, _boxes("noise", ocr_text)) == (CheckStatus.CONFLICT, ocr_text)


def test_short_values_never_conflict():
    # '1' would otherwise "match" the '|' or 'l' in any box.
    assert check_value("1", _boxes("TJM Labs|Automation")) == (CheckStatus.UNCONFIRMED, None)


def test_match_stays_within_one_box():
    assert check_value("EUR 570.00", _boxes("EUR", "570.00"))[0] is CheckStatus.UNCONFIRMED


def test_criticality():
    assert is_critical("items[0].sku")
    assert is_critical("billing_address.zip")
    assert is_critical("totals.gross")
    assert not is_critical("items[0].description")
    assert not is_critical("delivery_address.name")


# --- crosscheck on the sample ----------------------------------------------


def test_sample_is_fully_confirmed(raw_fixture, ocr):
    result = crosscheck(raw_fixture, ocr)
    assert result.issues == []
    assert result.warnings == []
    assert all(c.status is CheckStatus.CONFIRMED for c in result.checks)
    assert len(result.checks) == 42  # 16 order/debtor/payment/totals + 10 address + 2 x 8 item values


def test_mangled_sku_is_caught(raw_fixture, ocr):
    """Partner of test_validate's mangled-SKU test: arithmetic passes it, OCR does not."""
    result = crosscheck(_with_item(raw_fixture, 0, sku="CHR-ERG-O1"), ocr)
    assert _issues(result) == [("ocr_conflict", "items[0].sku")]
    assert result.issues[0].observed == "CHR-ERG-01"


def test_wrong_reference_and_zip(raw_fixture, ocr):
    raw = raw_fixture.model_copy(update={"external_reference": "WEB-2026-O714-A17", "billing_zip": "10171"})
    assert _issues(crosscheck(raw, ocr)) == [
        ("ocr_conflict", "order.external_reference"),
        ("ocr_unconfirmed", "billing_address.zip"),
    ]


def test_wrong_number_is_unconfirmed(raw_fixture, ocr):
    assert _issues(crosscheck(_with_item(raw_fixture, 1, unit_net_price="48.00"), ocr)) == [
        ("ocr_unconfirmed", "items[1].unit_net_price")
    ]


def test_free_text_mismatch_is_only_a_warning(raw_fixture, ocr):
    result = crosscheck(_with_item(raw_fixture, 0, description="Ergonomic Desk Chair Pro"), ocr)
    assert result.issues == []
    assert [(w.rule, w.field) for w in result.warnings] == [("ocr_unconfirmed", "items[0].description")]


def test_null_values_are_skipped(raw_fixture, ocr):
    result = crosscheck(raw_fixture.model_copy(update={"customer_id": None}), ocr)
    assert "order.customer_id" not in [c.field for c in result.checks]
    assert result.issues == []


def test_ocr_result_json_round_trip(ocr):
    assert OcrResult.from_json(ocr.to_json()) == ocr
