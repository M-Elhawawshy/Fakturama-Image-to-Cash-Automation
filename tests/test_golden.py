from fakturama_agent.cli import main
from fakturama_agent.extract.normalize import normalize
from fakturama_agent.extract.validate import validate

from .conftest import GOLDEN, RAW_FIXTURE


def test_normalize_raw_fixture_equals_golden(raw_fixture, golden_dict, golden_spec):
    result = normalize(raw_fixture)
    assert result.issues == []
    assert result.spec == golden_spec
    assert result.spec.model_dump(mode="json") == golden_dict


def test_golden_validates_clean(raw_fixture):
    assert validate(normalize(raw_fixture).spec) == []


def test_cli_normalize_writes_golden_bytes(tmp_path):
    out = tmp_path / "order.json"
    assert main(["normalize", str(RAW_FIXTURE), "-o", str(out)]) == 0
    assert out.read_bytes() == GOLDEN.read_bytes()


def test_cli_normalize_exits_2_on_issues(tmp_path, capsys):
    raw = tmp_path / "raw.json"
    raw.write_text(RAW_FIXTURE.read_text(encoding="utf-8").replace("Bank Transfer", "PayPal"), encoding="utf-8")
    out = tmp_path / "order.json"
    assert main(["normalize", str(raw), "-o", str(out)]) == 2
    assert not out.exists()
    assert "map_payment_method" in capsys.readouterr().err
