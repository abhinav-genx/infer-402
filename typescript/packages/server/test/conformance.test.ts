import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { PricingTable } from "@infer402/core";

import { calculateCharge } from "../src/pricing.js";

interface Fixture {
  table: PricingTable;
  cases: Array<{
    name: string;
    model: string;
    usage: {
      input_tokens: number;
      cached_input_tokens: number;
      output_tokens: number;
    };
    maximumAtomic: string;
    expectedAtomic: string;
    expectedUsd: string;
    capped: boolean;
  }>;
}

describe("shared pricing conformance", () => {
  it("matches every language-neutral fixture", () => {
    const path = fileURLToPath(
      new URL("../../../../specs/fixtures/pricing-cases.json", import.meta.url),
    );
    const fixture = JSON.parse(readFileSync(path, "utf8")) as Fixture;

    for (const testCase of fixture.cases) {
      const result = calculateCharge(
        fixture.table,
        testCase.model,
        {
          input_tokens: testCase.usage.input_tokens,
          output_tokens: testCase.usage.output_tokens,
          input_tokens_details: { cached_tokens: testCase.usage.cached_input_tokens },
        },
        BigInt(testCase.maximumAtomic),
      );
      expect(result.amountAtomic.toString(), testCase.name).toBe(testCase.expectedAtomic);
      expect(result.amountUsd, testCase.name).toBe(testCase.expectedUsd);
      expect(result.capped, testCase.name).toBe(testCase.capped);
    }
  });
});
