from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelPricing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_usd_per_million: str = Field(alias="inputUsdPerMillion")
    cached_input_usd_per_million: str = Field(alias="cachedInputUsdPerMillion")
    output_usd_per_million: str = Field(alias="outputUsdPerMillion")
    provider_markup_bps: int = Field(default=0, alias="providerMarkupBps", ge=0, le=100_000)
    fixed_fee_usd: str = Field(default="0", alias="fixedFeeUsd")

    @field_validator(
        "input_usd_per_million",
        "cached_input_usd_per_million",
        "output_usd_per_million",
        "fixed_fee_usd",
    )
    @classmethod
    def validate_decimal(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"\d+(?:\.\d+)?", value):
            raise ValueError("must be a non-negative decimal string")
        return value


class PricingTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=128)
    models: dict[str, ModelPricing]

    @field_validator("models")
    @classmethod
    def require_models(cls, value: dict[str, ModelPricing]) -> dict[str, ModelPricing]:
        if not value:
            raise ValueError("at least one model must be priced")
        return value


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)


class PaymentReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    created_at: datetime = Field(alias="createdAt")
    network: str
    asset: str
    amount_atomic: str = Field(alias="amountAtomic", pattern=r"^\d+$")
    amount_usd: str = Field(alias="amountUsd")
    model: str
    pricing_version: str = Field(alias="pricingVersion")
    input_tokens: int = Field(alias="inputTokens", ge=0)
    cached_input_tokens: int = Field(alias="cachedInputTokens", ge=0)
    output_tokens: int = Field(alias="outputTokens", ge=0)
    openai_request_id: str | None = Field(default=None, alias="openaiRequestId")
