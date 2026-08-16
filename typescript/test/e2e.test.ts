/**
 * End-to-end payment flow: a real gateway (provider) and real buyer client.
 *
 * Mirrors the Python integration test. Exercises the full x402 `upto`
 * settlement path on Base Sepolia without a real OpenAI key, a real
 * facilitator, or a funded wallet:
 *
 * - OpenAI is replaced by an injected fake that returns a raw Responses payload.
 * - Redis is replaced by an injected in-memory ioredis-compatible fake.
 * - The facilitator is a local Express server that always verifies/settles.
 *
 * The gateway and the mock facilitator run as real HTTP servers on localhost,
 * so the buyer client talks to the provider over real HTTP as in production.
 */
import type { AddressInfo } from "node:net";

import express from "express";
import type { Express } from "express";
import { afterEach, describe, expect, it } from "vitest";

import { privateKeySigner, X402OpenAI } from "@infer402/client";
import { BASE_SEPOLIA, USDC_BY_NETWORK } from "@infer402/core";
import type { PricingTable } from "@infer402/core";
import {
  calculateCharge,
  createGateway,
  defineConfig,
} from "@infer402/server";
import type { GatewayConfig, GatewayDependencies } from "@infer402/server";

// Well-known Hardhat/Anvil test account #0. Public test key, never holds funds.
const TEST_PRIVATE_KEY =
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80" as const;
const NETWORK = BASE_SEPOLIA; // eip155:84532
const MODEL = "gpt-4o-mini-test";
const ANSWER_TEXT = "x402 lets a client pay per API call in USDC over an HTTP 402 challenge.";
const FACILITATOR_ADDRESS = "0x000000000000000000000000000000000000dead";

const RAW_OPENAI_RESPONSE = {
  id: "resp_local_0001",
  object: "response",
  created_at: 1734300000,
  model: MODEL,
  status: "completed",
  output: [
    {
      id: "msg_local_0001",
      type: "message",
      role: "assistant",
      content: [{ type: "output_text", text: ANSWER_TEXT, annotations: [] }],
    },
  ],
  output_text: ANSWER_TEXT,
  usage: {
    input_tokens: 1200,
    input_tokens_details: { cached_tokens: 400 },
    output_tokens: 350,
    output_tokens_details: { reasoning_tokens: 0 },
    total_tokens: 1550,
  },
} as const;

const PRICING: PricingTable = {
  version: "local-test-v1",
  models: {
    [MODEL]: {
      inputUsdPerMillion: "0.15",
      cachedInputUsdPerMillion: "0.075",
      outputUsdPerMillion: "0.60",
      providerMarkupBps: 1000,
      fixedFeeUsd: "0.0001",
    },
  },
};

// OpenRouter-style provider using the OpenAI-compatible Chat Completions API.
const CHAT_MODEL = "anthropic/claude-3.5-sonnet";
const CHAT_COMPLETION = {
  id: "chatcmpl_local_0001",
  object: "chat.completion",
  created: 1734300000,
  model: CHAT_MODEL,
  choices: [
    {
      index: 0,
      message: { role: "assistant", content: ANSWER_TEXT },
      finish_reason: "stop",
    },
  ],
  usage: {
    prompt_tokens: 1200,
    prompt_tokens_details: { cached_tokens: 400 },
    completion_tokens: 350,
    total_tokens: 1550,
  },
} as const;
const CHAT_PRICING: PricingTable = {
  version: "local-test-v1",
  models: {
    [CHAT_MODEL]: {
      inputUsdPerMillion: "0.15",
      cachedInputUsdPerMillion: "0.075",
      outputUsdPerMillion: "0.60",
      providerMarkupBps: 1000,
      fixedFeeUsd: "0.0001",
    },
  },
};

// --------------------------------------------------------------------------- //
// Fakes for the gateway's external dependencies.
// --------------------------------------------------------------------------- //
function createFakeOpenAI(): { openai: GatewayDependencies["openai"]; calls: unknown[] } {
  const calls: unknown[] = [];
  const openai = {
    responses: {
      create(body: unknown, options: unknown) {
        calls.push({ body, options });
        return {
          async withResponse() {
            return {
              data: structuredClone(RAW_OPENAI_RESPONSE),
              response: { headers: new Headers({ "x-request-id": RAW_OPENAI_RESPONSE.id }) },
            };
          },
        };
      },
    },
  };
  return { openai: openai as unknown as GatewayDependencies["openai"], calls };
}

function createFakeChat(): { client: unknown; calls: unknown[] } {
  const calls: unknown[] = [];
  const client = {
    chat: {
      completions: {
        create(body: unknown, options: unknown) {
          calls.push({ body, options });
          return {
            async withResponse() {
              return {
                data: structuredClone(CHAT_COMPLETION),
                response: { headers: new Headers({ "x-request-id": CHAT_COMPLETION.id }) },
              };
            },
          };
        },
      },
    },
  };
  return { client, calls };
}

class FakeRedis {
  readonly store = new Map<string, string>();
  status = "ready";

  async get(key: string): Promise<string | null> {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }

  async set(key: string, value: string, ...args: unknown[]): Promise<"OK" | null> {
    const nx = args.some((arg) => String(arg).toUpperCase() === "NX");
    if (nx && this.store.has(key)) return null;
    this.store.set(key, value);
    return "OK";
  }

  async del(...keys: string[]): Promise<number> {
    let removed = 0;
    for (const key of keys) if (this.store.delete(key)) removed += 1;
    return removed;
  }

  async ping(): Promise<string> {
    return "PONG";
  }

  async connect(): Promise<void> {
    this.status = "ready";
  }

  async quit(): Promise<"OK"> {
    return "OK";
  }

  disconnect(): void {}
}

// --------------------------------------------------------------------------- //
// Mock facilitator: always verifies and settles, no chain access.
// --------------------------------------------------------------------------- //
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";

function findAddress(value: unknown): string | undefined {
  if (typeof value === "string") return ADDRESS_RE.test(value) ? value : undefined;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findAddress(item);
      if (found) return found;
    }
  } else if (value && typeof value === "object") {
    for (const item of Object.values(value)) {
      const found = findAddress(item);
      if (found) return found;
    }
  }
  return undefined;
}

interface FacilitatorState {
  supported: number;
  verify: number;
  settle: number;
}

function makeFacilitatorApp(network: string): { app: Express; state: FacilitatorState } {
  const state: FacilitatorState = { supported: 0, verify: 0, settle: 0 };
  const app = express();
  app.use(express.json());

  app.get("/supported", (_request, response) => {
    state.supported += 1;
    response.json({
      kinds: [
        {
          x402Version: 2,
          scheme: "upto",
          network,
          extra: { facilitatorAddress: FACILITATOR_ADDRESS },
        },
      ],
      extensions: [],
      signers: {},
    });
  });

  app.post("/verify", (request, response) => {
    state.verify += 1;
    response.json({
      isValid: true,
      payer: findAddress(request.body?.paymentPayload) ?? ZERO_ADDRESS,
    });
  });

  app.post("/settle", (request, response) => {
    state.settle += 1;
    const requirements = request.body?.paymentRequirements ?? {};
    response.json({
      success: true,
      transaction: `0x${"11".repeat(32)}`,
      network,
      payer: findAddress(request.body?.paymentPayload) ?? ZERO_ADDRESS,
      amount: requirements.maxAmountRequired ?? requirements.amount ?? null,
    });
  });

  return { app, state };
}

// --------------------------------------------------------------------------- //
// Run Express apps as real localhost servers for a faithful HTTP round trip.
// --------------------------------------------------------------------------- //
function listen(app: Express): Promise<{ url: string; close: () => Promise<void> }> {
  return new Promise((resolve) => {
    const server = app.listen(0, "127.0.0.1", () => {
      const port = (server.address() as AddressInfo).port;
      resolve({
        url: `http://127.0.0.1:${port}`,
        close: () =>
          new Promise<void>((done) => {
            server.close(() => done());
          }),
      });
    });
  });
}

interface Stack {
  gatewayUrl: string;
  facilitator: FacilitatorState;
  calls: unknown[];
  redis: FakeRedis;
  config: GatewayConfig;
  expectedAtomic: string;
  expectedUsd: string;
}

const cleanups: Array<() => Promise<void>> = [];

afterEach(async () => {
  while (cleanups.length) await cleanups.pop()?.();
});

async function createStack(): Promise<Stack> {
  const { app: facilitatorApp, state: facilitator } = makeFacilitatorApp(NETWORK);
  const facilitatorServer = await listen(facilitatorApp);
  cleanups.push(facilitatorServer.close);

  const { openai, calls } = createFakeOpenAI();
  const redis = new FakeRedis();

  const config = defineConfig({
    openaiApiKey: "fake-key-unused",
    facilitatorUrl: facilitatorServer.url,
    network: NETWORK,
    payToAddress: "0x000000000000000000000000000000000000beef",
    redisUrl: "redis://localhost:6379",
    idempotencyHmacSecret: "x".repeat(32),
    maximumPaymentUsd: "$1.00",
    pricing: PRICING,
    nodeEnv: "test",
    logLevel: "silent",
    maxOutputTokens: 16_000,
  });

  const dependencies: GatewayDependencies = {
    redis: redis as unknown as GatewayDependencies["redis"],
    openai,
  };
  const { app } = createGateway(config, dependencies);
  const gatewayServer = await listen(app);
  cleanups.push(gatewayServer.close);

  const charge = calculateCharge(
    config.pricing,
    MODEL,
    {
      input_tokens: 1200,
      output_tokens: 350,
      input_tokens_details: { cached_tokens: 400 },
    },
    config.maximumPaymentAtomic,
  );

  return {
    gatewayUrl: gatewayServer.url,
    facilitator,
    calls,
    redis,
    config,
    expectedAtomic: charge.amountAtomic.toString(),
    expectedUsd: charge.amountUsd,
  };
}

function assertPaidResponse(stack: Stack, result: Awaited<ReturnType<X402OpenAI["responses"]["create"]>>): void {
  const data = result.data as unknown as typeof RAW_OPENAI_RESPONSE;
  expect(data.output_text).toBe(ANSWER_TEXT);
  expect(data.id).toBe(RAW_OPENAI_RESPONSE.id);
  expect(data.model).toBe(MODEL);

  const receipt = result.receipt;
  expect(receipt).toBeDefined();
  expect(receipt?.network).toBe(NETWORK);
  expect(receipt?.asset).toBe(USDC_BY_NETWORK[NETWORK]);
  expect(receipt?.model).toBe(MODEL);
  expect(receipt?.pricingVersion).toBe("local-test-v1");
  expect(receipt?.inputTokens).toBe(1200);
  expect(receipt?.cachedInputTokens).toBe(400);
  expect(receipt?.outputTokens).toBe(350);
  expect(receipt?.amountAtomic).toBe(stack.expectedAtomic);
  expect(receipt?.amountUsd).toBe(stack.expectedUsd);

  expect(result.paymentResponse).toBeDefined();
  expect(stack.facilitator.supported).toBeGreaterThanOrEqual(1);
  expect(stack.facilitator.verify).toBeGreaterThanOrEqual(1);
  expect(stack.facilitator.settle).toBeGreaterThanOrEqual(1);
}

describe("end-to-end buyer pays provider", () => {
  it("settles a paid Responses call and returns a receipt", async () => {
    const stack = await createStack();
    const client = new X402OpenAI({
      baseURL: `${stack.gatewayUrl}/v1`,
      signer: privateKeySigner(TEST_PRIVATE_KEY),
      maxPaymentUsd: "$2.00",
      networks: [NETWORK],
    });

    const result = await client.responses.create({
      model: MODEL,
      input: "Explain x402 in one sentence.",
      max_output_tokens: 512,
    });

    assertPaidResponse(stack, result);

    // The provider actually invoked (the fake) OpenAI exactly once.
    expect(stack.calls).toHaveLength(1);

    // Expected charge for this fixed usage is deterministic.
    expect(stack.expectedAtomic).toBe("496");
    expect(stack.expectedUsd).toBe("0.000496");
  });

  it("replays an idempotent call from cache without re-settling", async () => {
    const stack = await createStack();
    const client = new X402OpenAI({
      baseURL: `${stack.gatewayUrl}/v1`,
      signer: privateKeySigner(TEST_PRIVATE_KEY),
      maxPaymentUsd: "$2.00",
      networks: [NETWORK],
    });
    const idempotencyKey = "req_replay_0000000000000001";

    const first = await client.responses.create(
      { model: MODEL, input: "hello", max_output_tokens: 256 },
      { idempotencyKey },
    );

    // The cache is written in a response "finish" handler; wait for it.
    await waitForCacheEntry(stack.redis);
    const settlesAfterFirst = stack.facilitator.settle;
    const callsAfterFirst = stack.calls.length;

    const second = await client.responses.create(
      { model: MODEL, input: "hello", max_output_tokens: 256 },
      { idempotencyKey },
    );

    expect(second.data).toEqual(first.data);
    expect(stack.facilitator.settle).toBe(settlesAfterFirst);
    expect(stack.calls.length).toBe(callsAfterFirst);
  });

  it("serves health and issues the 402 challenge", async () => {
    const stack = await createStack();

    const live = await fetch(`${stack.gatewayUrl}/health/live`);
    expect(live.status).toBe(200);
    expect(await live.json()).toEqual({ status: "ok" });

    const ready = await fetch(`${stack.gatewayUrl}/health/ready`);
    expect(ready.status).toBe(200);

    // Missing Idempotency-Key is rejected before any payment handling.
    const missingKey = await fetch(`${stack.gatewayUrl}/v1/responses`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: MODEL, input: "hi", max_output_tokens: 64 }),
    });
    expect(missingKey.status).toBe(400);
    expect((await missingKey.json()).error.code).toBe("VALIDATION_ERROR");

    // A valid key with no payment yields the 402 challenge with requirements.
    const challenge = await fetch(`${stack.gatewayUrl}/v1/responses`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "idempotency-key": "req_challenge_000000000001",
      },
      body: JSON.stringify({ model: MODEL, input: "hi", max_output_tokens: 64 }),
    });
    expect(challenge.status).toBe(402);
    expect(challenge.headers.has("payment-required")).toBe(true);
  });

  it("routes to a chat provider (OpenRouter-style) and bills normalized usage", async () => {
    const { app: facilitatorApp, state: facilitator } = makeFacilitatorApp(NETWORK);
    const facilitatorServer = await listen(facilitatorApp);
    cleanups.push(facilitatorServer.close);

    const { client: fakeChat, calls } = createFakeChat();
    const redis = new FakeRedis();
    const config = defineConfig({
      providers: [
        {
          id: "openrouter",
          baseUrl: "https://openrouter.ai/api/v1",
          api: "chat",
          apiKey: "fake-key-unused",
        },
      ],
      defaultProvider: "openrouter",
      facilitatorUrl: facilitatorServer.url,
      network: NETWORK,
      payToAddress: "0x000000000000000000000000000000000000beef",
      redisUrl: "redis://localhost:6379",
      idempotencyHmacSecret: "x".repeat(32),
      maximumPaymentUsd: "$1.00",
      pricing: CHAT_PRICING,
      nodeEnv: "test",
      logLevel: "silent",
      maxOutputTokens: 16_000,
    });
    const dependencies: GatewayDependencies = {
      redis: redis as unknown as GatewayDependencies["redis"],
      clients: { openrouter: fakeChat } as unknown as GatewayDependencies["clients"],
    };
    const { app } = createGateway(config, dependencies);
    const gatewayServer = await listen(app);
    cleanups.push(gatewayServer.close);

    const charge = calculateCharge(
      config.pricing,
      CHAT_MODEL,
      { input_tokens: 1200, output_tokens: 350, input_tokens_details: { cached_tokens: 400 } },
      config.maximumPaymentAtomic,
    );

    const client = new X402OpenAI({
      baseURL: `${gatewayServer.url}/v1`,
      signer: privateKeySigner(TEST_PRIVATE_KEY),
      maxPaymentUsd: "$2.00",
      networks: [NETWORK],
    });
    // The buyer sends a Chat Completions body; the gateway routes it to the chat provider.
    const result = await client.responses.create({
      model: CHAT_MODEL,
      messages: [{ role: "user", content: "Explain x402 in one sentence." }],
      max_tokens: 512,
    } as never);

    const data = result.data as unknown as typeof CHAT_COMPLETION;
    expect(data.object).toBe("chat.completion");
    expect(data.choices[0]?.message.content).toBe(ANSWER_TEXT);

    expect(result.receipt?.model).toBe(CHAT_MODEL);
    expect(result.receipt?.network).toBe(NETWORK);
    expect(result.receipt?.inputTokens).toBe(1200);
    expect(result.receipt?.cachedInputTokens).toBe(400);
    expect(result.receipt?.outputTokens).toBe(350);
    expect(result.receipt?.amountAtomic).toBe(charge.amountAtomic.toString());
    expect(result.paymentResponse).toBeDefined();

    expect(calls).toHaveLength(1);
    const forwarded = (calls[0] as { body: Record<string, unknown> }).body;
    expect(forwarded.max_tokens).toBe(512);
    expect(forwarded.messages).toBeDefined();
    expect(forwarded.provider).toBeUndefined();
    expect(facilitator.settle).toBeGreaterThanOrEqual(1);
  });
});

async function waitForCacheEntry(redis: FakeRedis, timeoutMs = 5_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const key of redis.store.keys()) {
      if (key.startsWith("x402-openai:idempotency:") && !key.endsWith(":lock")) return;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("idempotency cache entry was not written in time");
}
