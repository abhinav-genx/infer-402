from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from x402_openai.core import PaymentReceipt, PricingTable, TokenUsage, X402OpenAIError

MILLION = Decimal(1_000_000)
USDC_SCALE = Decimal(1_000_000)
BASIS_POINTS = Decimal(10_000)


@dataclass(frozen=True, slots=True)
class ChargeCalculation:
    amount_atomic: int
    uncapped_amount_atomic: int
    amount_usd: str
    capped: bool
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


def calculate_charge(
    table: PricingTable,
    model: str,
    usage: TokenUsage,
    maximum_atomic: int,
) -> ChargeCalculation:
    price = table.models.get(model)
    if price is None:
        raise X402OpenAIError("MODEL_NOT_ALLOWED", f"Model {model} is not priced", 400)

    cached_tokens = min(usage.cached_input_tokens, usage.input_tokens)
    uncached_tokens = usage.input_tokens - cached_tokens
    input_cost = Decimal(uncached_tokens) * Decimal(price.input_usd_per_million) / MILLION
    cached_cost = Decimal(cached_tokens) * Decimal(price.cached_input_usd_per_million) / MILLION
    output_cost = Decimal(usage.output_tokens) * Decimal(price.output_usd_per_million) / MILLION
    markup = Decimal(1) + Decimal(price.provider_markup_bps) / BASIS_POINTS
    total = (input_cost + cached_cost + output_cost) * markup + Decimal(price.fixed_fee_usd)
    uncapped = int((total * USDC_SCALE).to_integral_value(rounding=ROUND_CEILING))
    amount = min(uncapped, maximum_atomic)

    return ChargeCalculation(
        amount_atomic=amount,
        uncapped_amount_atomic=uncapped,
        amount_usd=f"{Decimal(amount) / USDC_SCALE:.6f}",
        capped=uncapped > maximum_atomic,
        input_tokens=usage.input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=usage.output_tokens,
    )


def create_receipt(
    *,
    identifier: str,
    network: str,
    asset: str,
    model: str,
    pricing_version: str,
    calculation: ChargeCalculation,
    openai_request_id: str | None = None,
) -> PaymentReceipt:
    return PaymentReceipt(
        id=identifier,
        createdAt=datetime.now(UTC),
        network=network,
        asset=asset,
        amountAtomic=str(calculation.amount_atomic),
        amountUsd=calculation.amount_usd,
        model=model,
        pricingVersion=pricing_version,
        inputTokens=calculation.input_tokens,
        cachedInputTokens=calculation.cached_input_tokens,
        outputTokens=calculation.output_tokens,
        openaiRequestId=openai_request_id,
    )
