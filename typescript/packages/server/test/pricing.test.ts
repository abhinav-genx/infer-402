import { describe, expect, it } from "vitest";

import type { PricingTable } from "@infer402/core";

import { calculateCharge } from "../src/pricing.js";

const table: PricingTable = {
  version: "test",
  models: {
    example: {
      inputUsdPerMillion: "1",
      cachedInputUsdPerMillion: "0.1",
      outputUsdPerMillion: "5",
      providerMarkupBps: 1000,
      fixedFeeUsd: "0.001",
    },
  },
};

describe("calculateCharge", () => {
  it("prices cached and uncached input with decimal-safe math", () => {
    const result = calculateCharge(
      table,
      "example",
      {
        input_tokens: 1_000,
        output_tokens: 500,
        input_tokens_details: { cached_tokens: 400 },
      },
      1_000_000n,
    );

    expect(result.amountAtomic).toBe(4_454n);
    expect(result.amountUsd).toBe("0.004454");
    expect(result.capped).toBe(false);
  });

  it("never exceeds the buyer authorization", () => {
    const result = calculateCharge(
      table,
      "example",
      { input_tokens: 1_000_000, output_tokens: 1_000_000 },
      50_000n,
    );

    expect(result.amountAtomic).toBe(50_000n);
    expect(result.capped).toBe(true);
  });
});
