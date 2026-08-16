import { isAddress } from "viem";
import { z } from "zod";

import {
  BASE_MAINNET,
  BASE_SEPOLIA,
  parseUsdToAtomic,
  pricingTableSchema,
  X402OpenAIError,
} from "@infer402/core";
import type { PricingTable, SupportedNetwork } from "@infer402/core";

// Matches Python eth_utils.is_address: any valid 20-byte hex address, regardless
// of case or EIP-55 checksum. (strict:false disables viem's checksum requirement.)
function isValidAddress(value: string): boolean {
  return isAddress(value, { strict: false });
}

import { KNOWN_PROVIDERS, providerConfigSchema, providersFromEnv } from "./providers.js";
import type { ProviderConfig } from "./providers.js";

export interface GatewayConfigInput {
  readonly openaiApiKey?: string;
  readonly providers?: ProviderConfig[];
  readonly defaultProvider?: string;
  readonly facilitatorUrl: string;
  readonly network: SupportedNetwork;
  readonly payToAddress: `0x${string}`;
  readonly redisUrl?: string;
  readonly idempotencyHmacSecret: string;
  readonly maximumPaymentUsd?: `$${number}`;
  readonly pricing: PricingTable;
  readonly nodeEnv?: "development" | "test" | "production";
  readonly port?: number;
  readonly logLevel?: "fatal" | "error" | "warn" | "info" | "debug" | "trace" | "silent";
  readonly trustProxy?: boolean;
  readonly corsOrigins?: readonly string[];
  readonly openaiTimeoutMs?: number;
  readonly bodyLimit?: string;
  readonly maxOutputTokens?: number;
  readonly rateLimitWindowMs?: number;
  readonly rateLimitMax?: number;
  readonly idempotencyTtlSeconds?: number;
}

export interface GatewayConfig extends Required<GatewayConfigInput> {
  readonly maximumPaymentAtomic: bigint;
  readonly providerMap: Record<string, ProviderConfig>;
}

const configSchema = z.object({
  openaiApiKey: z.string().default(""),
  providers: z.array(providerConfigSchema).default([]),
  defaultProvider: z.string().default("openai"),
  facilitatorUrl: z.string().url(),
  network: z.enum([BASE_MAINNET, BASE_SEPOLIA]),
  payToAddress: z.string().refine(isValidAddress, "payToAddress must be an EVM address"),
  redisUrl: z.string().url().default("redis://localhost:6379"),
  idempotencyHmacSecret: z.string().min(32),
  maximumPaymentUsd: z.string().regex(/^\$\d+(?:\.\d{1,6})?$/).default("$1.00"),
  pricing: pricingTableSchema,
  nodeEnv: z.enum(["development", "test", "production"]).default("development"),
  port: z.number().int().min(1).max(65_535).default(4021),
  logLevel: z
    .enum(["fatal", "error", "warn", "info", "debug", "trace", "silent"])
    .default("info"),
  trustProxy: z.boolean().default(false),
  corsOrigins: z.array(z.string().url()).default([]),
  openaiTimeoutMs: z.number().int().min(1_000).max(600_000).default(120_000),
  bodyLimit: z.string().regex(/^\d+(?:kb|mb)$/i).default("256kb"),
  maxOutputTokens: z.number().int().positive().max(1_000_000).default(16_000),
  rateLimitWindowMs: z.number().int().min(1_000).default(60_000),
  rateLimitMax: z.number().int().positive().default(120),
  idempotencyTtlSeconds: z.number().int().min(60).max(604_800).default(86_400),
});

export function defineConfig(input: GatewayConfigInput): GatewayConfig {
  const parsed = configSchema.safeParse(input);
  if (!parsed.success) {
    throw new X402OpenAIError("CONFIGURATION_ERROR", "Invalid gateway configuration", 500, {
      issues: parsed.error.issues,
    });
  }

  const value = parsed.data;
  if (value.network === BASE_MAINNET && new URL(value.facilitatorUrl).hostname === "x402.org") {
    throw new X402OpenAIError(
      "CONFIGURATION_ERROR",
      "x402.org is testnet-only; configure a production facilitator for Base mainnet",
    );
  }

  const providerMap: Record<string, ProviderConfig> = {};
  for (const provider of value.providers) providerMap[provider.id] = provider;
  if (!providerMap.openai && value.openaiApiKey) {
    providerMap.openai = {
      id: "openai",
      baseUrl: KNOWN_PROVIDERS.openai.baseUrl,
      api: "responses",
      apiKey: value.openaiApiKey,
    };
  }
  if (Object.keys(providerMap).length === 0) {
    throw new X402OpenAIError(
      "CONFIGURATION_ERROR",
      "configure at least one provider (set OPENAI_API_KEY or another provider key)",
    );
  }
  if (!providerMap[value.defaultProvider]) {
    throw new X402OpenAIError(
      "CONFIGURATION_ERROR",
      `default_provider '${value.defaultProvider}' has no configured API key`,
    );
  }

  return {
    ...value,
    payToAddress: value.payToAddress as `0x${string}`,
    maximumPaymentUsd: value.maximumPaymentUsd as `$${number}`,
    maximumPaymentAtomic: parseUsdToAtomic(value.maximumPaymentUsd),
    providerMap,
  };
}

function integer(value: string | undefined, fallback: number): number {
  if (value === undefined || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`Invalid integer: ${value}`);
  return parsed;
}

export function loadGatewayConfig(env: NodeJS.ProcessEnv = process.env): GatewayConfig {
  let pricing: unknown;
  try {
    pricing = JSON.parse(env.PRICING_JSON ?? "");
  } catch {
    throw new X402OpenAIError("CONFIGURATION_ERROR", "PRICING_JSON must contain valid JSON");
  }

  try {
    return defineConfig({
      openaiApiKey: env.OPENAI_API_KEY ?? "",
      providers: providersFromEnv(env),
      defaultProvider: env.PROVIDER ?? "openai",
      facilitatorUrl: env.FACILITATOR_URL ?? "",
      network: (env.NETWORK ?? BASE_SEPOLIA) as SupportedNetwork,
      payToAddress: (env.PAY_TO_ADDRESS ?? "") as `0x${string}`,
      redisUrl: env.REDIS_URL ?? "redis://localhost:6379",
      idempotencyHmacSecret: env.IDEMPOTENCY_HMAC_SECRET ?? "",
      maximumPaymentUsd: (env.MAX_PAYMENT_USD ?? "$1.00") as `$${number}`,
      pricing: pricing as PricingTable,
      nodeEnv: (env.NODE_ENV ?? "development") as NonNullable<GatewayConfigInput["nodeEnv"]>,
      port: integer(env.PORT, 4021),
      logLevel: (env.LOG_LEVEL ?? "info") as NonNullable<GatewayConfigInput["logLevel"]>,
      trustProxy: env.TRUST_PROXY === "true",
      corsOrigins: (env.CORS_ORIGINS ?? "")
        .split(",")
        .map((origin) => origin.trim())
        .filter(Boolean),
      openaiTimeoutMs: integer(env.OPENAI_TIMEOUT_MS, 120_000),
      bodyLimit: env.BODY_LIMIT ?? "256kb",
      maxOutputTokens: integer(env.MAX_OUTPUT_TOKENS, 16_000),
      rateLimitWindowMs: integer(env.RATE_LIMIT_WINDOW_MS, 60_000),
      rateLimitMax: integer(env.RATE_LIMIT_MAX, 120),
      idempotencyTtlSeconds: integer(env.IDEMPOTENCY_TTL_SECONDS, 86_400),
    });
  } catch (error) {
    if (error instanceof X402OpenAIError) throw error;
    throw new X402OpenAIError("CONFIGURATION_ERROR", "Invalid environment configuration", 500, {
      reason: error instanceof Error ? error.message : "unknown",
    });
  }
}
