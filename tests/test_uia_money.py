from decimal import Decimal

import pytest

from fakturama_agent.uia.money import MoneyFormat, MoneyFormatError, workspace_currency

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


# --- the workspace currency ------------------------------------------------------------------------




@pytest.mark.parametrize("locale, example, expected", [
    ("United States", "$-1,234.57", "USD"),
    ("Germany", "-1.234,57 €", "EUR"),
    ("Austria", "€ -1.234,57", "EUR"),
    ("Germany", "-1.234,57 EUR", "EUR"),  # ISO code shown instead of the symbol
    ("Switzerland", "CHF-1'234.57", "CHF"),
    ("United Kingdom", "-£1,234.57", "GBP"),
    ("Germany", "-1.234,57", "EUR"),  # no symbol: the locale decides
])
def test_workspace_currency(locale, example, expected):
    code, how = workspace_currency(locale, MoneyFormat.from_example(example, 2))
    assert code == expected and repr(locale) in how


@pytest.mark.parametrize("locale, example, why", [
    ("Germany", "$-1,234.57", "does not fit EUR"),  # symbol and locale contradict
    ("Germany", "-1.234,57 USD", "disagree"),
    ("Atlantis", "¤-1,234.57", "not known"),
])
def test_workspace_currency_unknown(locale, example, why):
    code, how = workspace_currency(locale, MoneyFormat.from_example(example, 2))
    assert code is None and why in how


def test_workspace_currency_without_format():
    assert workspace_currency("Germany", None) == (None, "the currency format was not read")


def test_typed_uses_the_workspace_decimal_mark():
    germany = MoneyFormat.from_example("-1.234,57 €", 2)
    assert germany.typed(Decimal("297.5")) == "297,50"  # '297.50' would be 29.750,00 €
    assert germany.typed(Decimal("678.30")) == "678,30" and germany.typed(Decimal("1234.5")) == "1234,50"
    assert US.typed(Decimal("297.5")) == "297.50" and US.typed(Decimal("0")) == "0.00"
    assert germany.parse(f"{germany.typed(Decimal('678.3'))} €") == Decimal("678.30")
