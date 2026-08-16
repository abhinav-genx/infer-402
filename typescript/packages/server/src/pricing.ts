import { Decimal } from "decimal.js";

import { X402OpenAIError } from "@infer402/core";
import type { PaymentReceipt, PricingTable, TokenUsage } from "@infer402/core";

const MILLION = new Decimal(1_000_000);
const USDC_SCALE = new Decimal(1_000_000);
const BASIS_POINTS = new Decimal(10_000);

export interface ChargeCalculation {
  readonly amountAtomic: bigint;
  readonly uncappedAmountAtomic: bigint;
  readonly amountUsd: string;
  readonly capped: boolean;
  readonly inputTokens: number;
  readonly cachedInputTokens: number;
  readonly outputTokens: number;
}

export function calculateCharge(
  table: PricingTable,
  model: string,
  usage: TokenUsage,
  maximumAtomic: bigint,
): ChargeCalculation {
  const price = table.models[model];
  if (!price) {
    throw new X402OpenAIError("MODEL_NOT_ALLOWED", `Model ${model} is not priced`, 400);
  }

  const cachedInputTokens = Math.min(
    usage.input_tokens_details?.cached_tokens ?? 0,
    usage.input_tokens,
  );
  const uncachedInputTokens = usage.input_tokens - cachedInputTokens;
  const input = new Decimal(uncachedInputTokens).mul(price.inputUsdPerMillion).div(MILLION);
  const cached = new Decimal(cachedInputTokens)
    .mul(price.cachedInputUsdPerMillion)
    .div(MILLION);
  const output = new Decimal(usage.output_tokens).mul(price.outputUsdPerMillion).div(MILLION);
  const markup = new Decimal(1).plus(new Decimal(price.providerMarkupBps).div(BASIS_POINTS));
  const total = input.plus(cached).plus(output).mul(markup).plus(price.fixedFeeUsd);
  const uncappedAmountAtomic = BigInt(total.mul(USDC_SCALE).ceil().toFixed(0));
  const amountAtomic =
    uncappedAmountAtomic > maximumAtomic ? maximumAtomic : uncappedAmountAtomic;

  return {
    amountAtomic,
    uncappedAmountAtomic,
    amountUsd: new Decimal(amountAtomic.toString()).div(USDC_SCALE).toFixed(6),
    capped: uncappedAmountAtomic > maximumAtomic,
    inputTokens: usage.input_tokens,
    cachedInputTokens,
    outputTokens: usage.output_tokens,
  };
}

export function createReceipt(input: {
  id: string;
  network: string;
  asset: string;
  model: string;
  pricingVersion: string;
  calculation: ChargeCalculation;
  openaiRequestId?: string;
}): PaymentReceipt {
  return {
    id: input.id,
    createdAt: new Date().toISOString(),
    network: input.network,
    asset: input.asset,
    amountAtomic: input.calculation.amountAtomic.toString(),
    amountUsd: input.calculation.amountUsd,
    model: input.model,
    pricingVersion: input.pricingVersion,
    inputTokens: input.calculation.inputTokens,
    cachedInputTokens: input.calculation.cachedInputTokens,
    outputTokens: input.calculation.outputTokens,
    ...(input.openaiRequestId ? { openaiRequestId: input.openaiRequestId } : {}),
  };
}
