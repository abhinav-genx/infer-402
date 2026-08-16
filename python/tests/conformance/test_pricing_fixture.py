import json
from pathlib import Path

from x402_openai.core import PricingTable, TokenUsage
from x402_openai.server import calculate_charge


def test_shared_pricing_cases() -> None:
    fixture_path = Path(__file__).parents[3] / "specs" / "fixtures" / "pricing-cases.json"
    fixture = json.loads(fixture_path.read_text())
    table = PricingTable.model_validate(fixture["table"])

    for case in fixture["cases"]:
        result = calculate_charge(
            table,
            case["model"],
            TokenUsage(**case["usage"]),
            int(case["maximumAtomic"]),
        )
        assert str(result.amount_atomic) == case["expectedAtomic"], case["name"]
        assert result.amount_usd == case["expectedUsd"], case["name"]
        assert result.capped is case["capped"], case["name"]
