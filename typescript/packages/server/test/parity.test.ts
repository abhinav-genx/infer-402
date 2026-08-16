import { describe, expect, it } from "vitest";

import { parseUsdToAtomic, X402OpenAIError } from "@infer402/core";
import { defineConfig, providersFromEnv } from "@infer402/server";
import type { PricingTable } from "@infer402/core";

const PRICING: PricingTable = {
  version: "t",
  models: {
    m: { inputUsdPerMillion: "1", cachedInputUsdPerMillion: "0.1", outputUsdPerMillion: "5" },
  },
};

function config(overrides: Record<string, unknown> = {}) {
  return defineConfig({
    openaiApiKey: "k",
    facilitatorUrl: "https://facilitator.example",
    network: "eip155:84532",
    payToAddress: "0x1111111111111111111111111111111111111111",
    idempotencyHmacSecret: "a".repeat(32),
    maximumPaymentUsd: "$1.00",
    pricing: PRICING,
    ...overrides,
  } as never);
}

describe("cross-SDK parity", () => {
  // parseUsdToAtomic strict form ^\d+(?:\.\d{1,6})?
  it("accepts plain decimals and rejects non-plain forms", () => {
    expect(parseUsdToAtomic("$1.25")).toBe(1_250_000n);
    expect(parseUsdToAtomic("1")).toBe(1_000_000n);
    for (const bad of ["1e2", ".5", "1.", "+1", "-1", "$1.0000001", "abc", ""]) {
      expect(() => parseUsdToAtomic(bad)).toThrow();
    }
  });

  // pay_to address: any valid 20-byte hex (matches Python eth_utils.is_address)
  it("accepts valid hex addresses and rejects malformed ones", () => {
    expect(() => config({ payToAddress: "0x4d678dbb85fe8c219e22714428bcd1592b48f2e6" })).not.toThrow();
    expect(() => config({ payToAddress: "0x4D678Dbb85fE8C219e22714428bCD1592b48f2E6" })).not.toThrow();
    expect(() => config({ payToAddress: "0x123" })).toThrow(X402OpenAIError);
    expect(() => config({ payToAddress: "not-an-address" })).toThrow(X402OpenAIError);
  });

  // PROVIDERS_JSON accepts snake_case and camelCase
  it("parses PROVIDERS_JSON in both casings", () => {
    const snake = providersFromEnv({
      PROVIDERS_JSON: '[{"id":"x","base_url":"https://a/v1","api":"chat","api_key":"k"}]',
    } as NodeJS.ProcessEnv);
    const camel = providersFromEnv({
      PROVIDERS_JSON: '[{"id":"y","baseUrl":"https://b/v1","api":"chat","apiKey":"k"}]',
    } as NodeJS.ProcessEnv);
    expect(snake[0]?.baseUrl).toBe("https://a/v1");
    expect(camel[0]?.baseUrl).toBe("https://b/v1");
    expect(camel[0]?.apiKey).toBe("k");
  });

  // config requires at least one provider and a configured default
  it("requires at least one provider and a configured default", () => {
    expect(() => config({ openaiApiKey: "", providers: [], defaultProvider: "openai" })).toThrow(
      /at least one provider/,
    );
    expect(() =>
      config({
        openaiApiKey: "k",
        providers: [],
        defaultProvider: "groq",
      }),
    ).toThrow(/no configured API key/);
  });
});
