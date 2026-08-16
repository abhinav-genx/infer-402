"""Cross-SDK parity checks: these behaviors must match the TypeScript SDK exactly."""

from __future__ import annotations

import pytest

from x402_openai.core import PricingTable, parse_usd_to_atomic
from x402_openai.server import GatewayConfig, ProviderConfig
from x402_openai.server.providers import providers_from_env

VALID_PRICING = PricingTable.model_validate(
    {
        "version": "t",
        "models": {
            "m": {
                "inputUsdPerMillion": "1",
                "cachedInputUsdPerMillion": "0.1",
                "outputUsdPerMillion": "5",
            }
        },
    }
)


def _config(**overrides: object) -> GatewayConfig:
    base: dict[str, object] = {
        "openai_api_key": "k",
        "facilitator_url": "https://facilitator.example",
        "network": "eip155:84532",
        "pay_to_address": "0x1111111111111111111111111111111111111111",
        "idempotency_hmac_secret": "a" * 32,
        "pricing": VALID_PRICING,
    }
    base.update(overrides)
    return GatewayConfig.model_validate(base)


# --- parseUsdToAtomic: strict form, matching the TS regex ^\d+(?:\.\d{1,6})? --- #
def test_usd_strict_accepts_plain_decimals() -> None:
    assert parse_usd_to_atomic("$1.25") == 1_250_000
    assert parse_usd_to_atomic("1") == 1_000_000


@pytest.mark.parametrize("bad", ["1e2", ".5", "1.", "+1", "-1", "$1.0000001", "abc", ""])
def test_usd_strict_rejects_nonplain_forms(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_usd_to_atomic(bad)


# --- pay_to_address: any valid 20-byte hex, matching eth_utils.is_address --- #
def test_pay_to_accepts_lowercase_and_mixed_case() -> None:
    _config(pay_to_address="0x4d678dbb85fe8c219e22714428bcd1592b48f2e6")
    _config(pay_to_address="0x4D678Dbb85fE8C219e22714428bCD1592b48f2E6")


@pytest.mark.parametrize("bad", ["0x123", "not-an-address", ""])
def test_pay_to_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        _config(pay_to_address=bad)


# --- PROVIDERS_JSON accepts both snake_case and camelCase --- #
def test_providers_json_accepts_both_casings() -> None:
    snake = providers_from_env(
        {"PROVIDERS_JSON": '[{"id":"x","base_url":"https://a/v1","api":"chat","api_key":"k"}]'}
    )
    camel = providers_from_env(
        {"PROVIDERS_JSON": '[{"id":"y","baseUrl":"https://b/v1","api":"chat","apiKey":"k"}]'}
    )
    assert snake[0].base_url == "https://a/v1"
    assert camel[0].base_url == "https://b/v1"
    assert camel[0].api_key == "k"


# --- config requires at least one provider and a configured default --- #
def test_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        _config(openai_api_key="", providers=(), default_provider="openai")


def test_requires_configured_default_provider() -> None:
    with pytest.raises(ValueError, match="no configured API key"):
        _config(openai_api_key="k", providers=(), default_provider="groq")


def test_provider_config_snake_case_construction() -> None:
    p = ProviderConfig(id="z", base_url="https://c/v1", api="responses", api_key="k")
    assert p.api == "responses"
