import pytest
from pydantic import ValidationError

from x402_openai.core import PricingTable
from x402_openai.server import GatewayConfig


def test_rejects_test_facilitator_on_mainnet() -> None:
    with pytest.raises(ValidationError, match="testnet-only"):
        GatewayConfig(
            openai_api_key="test",
            facilitator_url="https://x402.org/facilitator",
            network="eip155:8453",
            pay_to_address="0x1111111111111111111111111111111111111111",
            idempotency_hmac_secret="a" * 32,
            pricing=PricingTable.model_validate(
                {
                    "version": "test",
                    "models": {
                        "example": {
                            "inputUsdPerMillion": "1",
                            "cachedInputUsdPerMillion": "0.1",
                            "outputUsdPerMillion": "5",
                            "providerMarkupBps": 0,
                            "fixedFeeUsd": "0",
                        }
                    },
                }
            ),
        )
