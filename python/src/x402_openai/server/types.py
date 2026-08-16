from __future__ import annotations

import json
import os
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from x402_openai.core import (
    BASE_MAINNET,
    BASE_SEPOLIA,
    PricingTable,
    SupportedNetwork,
    parse_usd_to_atomic,
)

from .providers import KNOWN_PROVIDERS, ProviderConfig, providers_from_env


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    openai_api_key: str = ""
    providers: tuple[ProviderConfig, ...] = ()
    default_provider: str = "openai"
    facilitator_url: str
    network: SupportedNetwork = BASE_SEPOLIA
    pay_to_address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    redis_url: str = "redis://localhost:6379"
    idempotency_hmac_secret: str = Field(min_length=32)
    maximum_payment_usd: str = "$1.00"
    pricing: PricingTable
    node_env: Literal["development", "test", "production"] = "development"
    port: int = Field(default=4021, ge=1, le=65_535)
    openai_timeout_seconds: float = Field(default=120.0, ge=1, le=600)
    max_output_tokens: int = Field(default=16_000, ge=1, le=1_000_000)
    idempotency_ttl_seconds: int = Field(default=86_400, ge=60, le=604_800)
    cors_origins: tuple[str, ...] = ()

    @field_validator("facilitator_url", "redis_url")
    @classmethod
    def require_absolute_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("must be an absolute URL")
        return value

    @field_validator("pay_to_address")
    @classmethod
    def validate_checksum_address(cls, value: str) -> str:
        from eth_utils.address import is_address

        if not is_address(value):
            raise ValueError("pay_to_address must be a valid EVM address")
        return value

    @field_validator("maximum_payment_usd")
    @classmethod
    def validate_payment(cls, value: str) -> str:
        parse_usd_to_atomic(value)
        return value

    @model_validator(mode="after")
    def reject_test_facilitator_on_mainnet(self) -> GatewayConfig:
        if self.network == BASE_MAINNET and urlparse(self.facilitator_url).hostname == "x402.org":
            raise ValueError("x402.org is testnet-only; configure a production facilitator")
        return self

    @model_validator(mode="after")
    def require_configured_default_provider(self) -> GatewayConfig:
        providers = self.provider_map
        if not providers:
            raise ValueError(
                "configure at least one provider (set OPENAI_API_KEY or another provider key)"
            )
        if self.default_provider not in providers:
            raise ValueError(
                f"default_provider '{self.default_provider}' has no configured API key"
            )
        return self

    @property
    def maximum_payment_atomic(self) -> int:
        return parse_usd_to_atomic(self.maximum_payment_usd)

    @property
    def provider_map(self) -> dict[str, ProviderConfig]:
        providers = {provider.id: provider for provider in self.providers}
        if "openai" not in providers and self.openai_api_key:
            providers["openai"] = ProviderConfig(
                id="openai",
                base_url=KNOWN_PROVIDERS["openai"].base_url,
                api="responses",
                api_key=self.openai_api_key,
            )
        return providers


def define_config(**values: object) -> GatewayConfig:
    return GatewayConfig.model_validate(values)


def load_gateway_config() -> GatewayConfig:
    pricing_json = os.environ.get("PRICING_JSON", "")
    try:
        pricing = PricingTable.model_validate(json.loads(pricing_json))
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("PRICING_JSON must contain a valid pricing table") from exc

    return GatewayConfig(
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        providers=providers_from_env(os.environ),
        default_provider=os.environ.get("PROVIDER", "openai"),
        facilitator_url=os.environ.get("FACILITATOR_URL", ""),
        network=os.environ.get("NETWORK", BASE_SEPOLIA),  # type: ignore[arg-type]
        pay_to_address=os.environ.get("PAY_TO_ADDRESS", ""),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        idempotency_hmac_secret=os.environ.get("IDEMPOTENCY_HMAC_SECRET", ""),
        maximum_payment_usd=os.environ.get("MAX_PAYMENT_USD", "$1.00"),
        pricing=pricing,
        node_env=os.environ.get("NODE_ENV", "development"),  # type: ignore[arg-type]
        port=int(os.environ.get("PORT", "4021")),
        openai_timeout_seconds=float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "120")),
        max_output_tokens=int(os.environ.get("MAX_OUTPUT_TOKENS", "16000")),
        idempotency_ttl_seconds=int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "86400")),
        cors_origins=tuple(
            origin.strip()
            for origin in os.environ.get("CORS_ORIGINS", "").split(",")
            if origin.strip()
        ),
    )
