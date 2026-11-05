from decimal import Decimal

import pytest

from fakturama_agent.uia.money import MoneyFormat, MoneyFormatError

US = MoneyFormat.from_example("$-1,234.57", 2)


def test_united_states_example_recorded_live():
    assert US == MoneyFormat(symbol="$", symbol_first=True, group=",", decimal=".", decimals=2)
    assert US.describe() == "$1,234.55"


@pytest.mark.parametrize(
    "text, value",
    [
        ("$678.30", "678.30"),
        ("$1,234.50", "1234.50"),
        ("678.30", "678.30"),
        ("$-1,234.57", "-1234.57"),
        ("-$12.00", "-12.00"),
        (" $0.00 ", "0.00"),
        ("$1234.50", "1234.50"),  # ungrouped is still unambiguous
    ],
)
def test_parse_us(text, value):
    assert US.parse(text) == Decimal(value)


@pytest.mark.parametrize("text", ["$1.234,50", "$1,23,4.50", "€678.30", "678,30", "$ 6 78.30", "", "$", "1e3"])
def test_parse_rejects_other_formats(text):
    with pytest.raises(MoneyFormatError):
        US.parse(text)


@pytest.mark.parametrize(
    "example, decimals, fmt, text, value",
    [
        ("-1.234,57 €", 2, MoneyFormat("€", False, ".", ",", 2), "1.234,50 €", "1234.50"),
        ("-1 234,57 €", 2, MoneyFormat("€", False, " ", ",", 2), "678,30 €", "678.30"),
        ("CHF-1'234.57", 2, MoneyFormat("CHF", True, "'", ".", 2), "CHF1'234.50", "1234.50"),
        ("-1234.57", 2, MoneyFormat("", True, "", ".", 2), "678.30", "678.30"),
        ("EUR -1.234,57", 2, MoneyFormat("EUR", True, ".", ",", 2), "EUR 678,30", "678.30"),
        ("$-1,234.568", 3, MoneyFormat("$", True, ",", ".", 3), "$1.500", "1.500"),
    ],
)
def test_other_locales(example, decimals, fmt, text, value):
    parsed = MoneyFormat.from_example(example, decimals)
    assert parsed == fmt
    assert parsed.parse(text) == Decimal(value)


@pytest.mark.parametrize(
    "example, decimals",
    [
        ("$1,234.57", 2),  # no minus: not the -1234.57 example
        ("$-1,234.57", 3),  # decimals disagree
        ("$-1,234.67", 2),  # not .57
        ("$-1,235", 0),  # no decimals: unsupported
        ("$-1.234.57", 2),  # group == decimal
        ("$-1,234.57 USD", 2),  # text on both sides
        ("garbage", 2),
        ("", 2),
    ],
)
def test_unsupported_examples(example, decimals):
    with pytest.raises(MoneyFormatError):
        MoneyFormat.from_example(example, decimals)
