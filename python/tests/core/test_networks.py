import pytest

from x402_openai.core import parse_usd_to_atomic


def test_parse_usd_to_atomic() -> None:
    assert parse_usd_to_atomic("$1.25") == 1_250_000
    assert parse_usd_to_atomic("0.000001") == 1


def test_rejects_excess_precision() -> None:
    with pytest.raises(ValueError):
        parse_usd_to_atomic("$0.0000001")
