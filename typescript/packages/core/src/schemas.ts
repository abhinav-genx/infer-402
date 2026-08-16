import { z } from "zod";

const decimalString = z.string().regex(/^\d+(?:\.\d+)?$/);

export const modelPricingSchema = z
  .object({
    inputUsdPerMillion: decimalString,
    cachedInputUsdPerMillion: decimalString,
    outputUsdPerMillion: decimalString,
    providerMarkupBps: z.number().int().min(0).max(100_000).default(0),
    fixedFeeUsd: decimalString.default("0"),
  })
  .strict();

export const pricingTableSchema = z
  .object({
    version: z.string().min(1).max(128),
    models: z.record(z.string().min(1), modelPricingSchema),
  })
  .strict()
  .refine((table) => Object.keys(table.models).length > 0, {
    message: "At least one model must be priced",
  });

export const usageSchema = z
  .object({
    input_tokens: z.number().int().nonnegative(),
    output_tokens: z.number().int().nonnegative(),
    total_tokens: z.number().int().nonnegative().optional(),
    input_tokens_details: z
      .object({ cached_tokens: z.number().int().nonnegative().optional() })
      .passthrough()
      .optional(),
  })
  .passthrough();

export const receiptSchema = z
  .object({
    id: z.string().min(1),
    createdAt: z.string().datetime(),
    network: z.string().min(1),
    asset: z.string().min(1),
    amountAtomic: z.string().regex(/^\d+$/),
    amountUsd: decimalString,
    model: z.string().min(1),
    pricingVersion: z.string().min(1),
    inputTokens: z.number().int().nonnegative(),
    cachedInputTokens: z.number().int().nonnegative(),
    outputTokens: z.number().int().nonnegative(),
    openaiRequestId: z.string().optional(),
  })
  .strict();

export type ModelPricing = z.infer<typeof modelPricingSchema>;
export type PricingTable = z.infer<typeof pricingTableSchema>;
export type TokenUsage = z.infer<typeof usageSchema>;
export type PaymentReceipt = z.infer<typeof receiptSchema>;
