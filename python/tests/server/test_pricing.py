from x402_openai.core import PricingTable, TokenUsage
from x402_openai.server import calculate_charge


def pricing_table() -> PricingTable:
    return PricingTable.model_validate(
        {
            "version": "test",
            "models": {
                "example": {
                    "inputUsdPerMillion": "1",
                    "cachedInputUsdPerMillion": "0.1",
                    "outputUsdPerMillion": "5",
                    "providerMarkupBps": 1000,
                    "fixedFeeUsd": "0.001",
                }
            },
        }
    )


def test_decimal_pricing() -> None:
    result = calculate_charge(
        pricing_table(),
        "example",
        TokenUsage(input_tokens=1000, cached_input_tokens=400, output_tokens=500),
        1_000_000,
    )
    assert result.amount_atomic == 4454
    assert result.amount_usd == "0.004454"
    assert not result.capped


def test_authorization_cap() -> None:
    result = calculate_charge(
        pricing_table(),
        "example",
        TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        50_000,
    )
    assert result.amount_atomic == 50_000
    assert result.capped
