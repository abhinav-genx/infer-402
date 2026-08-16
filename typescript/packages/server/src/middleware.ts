import { createHash, createHmac, timingSafeEqual } from "node:crypto";

import type { NextFunction, Request, RequestHandler, Response } from "express";
import type { Redis } from "ioredis";
import type { Logger } from "pino";

import { HTTPFacilitatorClient } from "@x402/core/server";
import { UptoEvmScheme } from "@x402/evm/upto/server";
import { paymentMiddleware, x402ResourceServer } from "@x402/express";
import {
  declarePaymentIdentifierExtension,
  PAYMENT_IDENTIFIER,
} from "@x402/extensions/payment-identifier";

import type { GatewayConfig } from "./types.js";

const IDEMPOTENCY_KEY = /^[A-Za-z0-9_-]{16,128}$/;
const REPLAY_HEADERS = [
  "content-type",
  "payment-response",
  "x-openai-request-id",
  "x-provider",
  "x-x402-receipt",
] as const;

interface CacheEntry {
  readonly fingerprint: string;
  readonly status: number;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: unknown;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => `${JSON.stringify(key)}:${canonicalJson(child)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function fingerprint(request: Request): string {
  return createHash("sha256")
    .update(request.method)
    .update("\0")
    .update(request.path)
    .update("\0")
    .update(canonicalJson(request.body))
    .digest("hex");
}

function secureKey(secret: string, publicKey: string): string {
  const digest = createHmac("sha256", secret).update(publicKey).digest("hex");
  return `x402-openai:idempotency:${digest}`;
}

function equalFingerprint(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function createIdempotencyMiddleware(options: {
  redis: Redis;
  secret: string;
  ttlSeconds: number;
  logger: Logger;
}): RequestHandler {
  return async (request: Request, response: Response, next: NextFunction): Promise<void> => {
    if (request.method !== "POST" || request.path !== "/v1/responses") {
      next();
      return;
    }

    const idempotencyKey = request.header("idempotency-key");
    if (!idempotencyKey || !IDEMPOTENCY_KEY.test(idempotencyKey)) {
      response.status(400).json({
        error: {
          code: "VALIDATION_ERROR",
          message: "Idempotency-Key must contain 16-128 URL-safe characters",
        },
      });
      return;
    }

    const requestFingerprint = fingerprint(request);
    const key = secureKey(options.secret, idempotencyKey);
    const lockKey = `${key}:lock`;
    let cachedJson: string | null;
    try {
      cachedJson = await options.redis.get(key);
    } catch (error) {
      options.logger.error({ err: error }, "Idempotency cache unavailable");
      response.status(503).json({
        error: { code: "CACHE_UNAVAILABLE", message: "Service unavailable" },
      });
      return;
    }

    if (cachedJson) {
      const cached = JSON.parse(cachedJson) as CacheEntry;
      if (!equalFingerprint(cached.fingerprint, requestFingerprint)) {
        response.status(409).json({
          error: {
            code: "IDEMPOTENCY_CONFLICT",
            message: "This idempotency key was already used for another request",
          },
        });
        return;
      }

      for (const [name, value] of Object.entries(cached.headers)) response.setHeader(name, value);
      response.setHeader("x-idempotency-replayed", "true");
      response.status(cached.status).json(cached.body);
      return;
    }

    // The first request only obtains the 402 quote. Lock the paid retry, not the quote request.
    if (!request.header("payment-signature") && !request.header("x-payment")) {
      next();
      return;
    }

    const lock = await options.redis.set(lockKey, requestFingerprint, "EX", 180, "NX");
    if (lock !== "OK") {
      response.setHeader("retry-after", "1");
      response.status(425).json({
        error: { code: "REQUEST_IN_PROGRESS", message: "This request is already processing" },
      });
      return;
    }

    let capturedBody: unknown;
    const originalJson = response.json.bind(response);
    response.json = ((body: unknown) => {
      capturedBody = body;
      return originalJson(body);
    }) as Response["json"];

    response.once("finish", () => {
      void (async () => {
        try {
          if (response.statusCode >= 200 && response.statusCode < 300 && capturedBody !== undefined) {
            const headers: Record<string, string> = {};
            for (const name of REPLAY_HEADERS) {
              const value = response.getHeader(name);
              if (typeof value === "string") headers[name] = value;
            }
            await options.redis.set(
              key,
              JSON.stringify({
                fingerprint: requestFingerprint,
                status: response.statusCode,
                headers,
                body: capturedBody,
              } satisfies CacheEntry),
              "EX",
              options.ttlSeconds,
            );
          }
        } catch (error) {
          options.logger.error({ err: error }, "Failed to store idempotent response");
        } finally {
          await options.redis.del(lockKey).catch((error: unknown) => {
            options.logger.error({ err: error }, "Failed to release idempotency lock");
          });
        }
      })();
    });

    next();
  };
}

export type FacilitatorAuthHeaders = () => Promise<{
  verify?: Record<string, string>;
  settle?: Record<string, string>;
  supported?: Record<string, string>;
  bazaar?: Record<string, string>;
}>;

export function createX402Middleware(
  config: GatewayConfig,
  facilitatorAuth?: FacilitatorAuthHeaders,
): RequestHandler {
  const facilitator = new HTTPFacilitatorClient({
    url: config.facilitatorUrl,
    ...(facilitatorAuth ? { createAuthHeaders: facilitatorAuth } : {}),
  });
  const server = new x402ResourceServer(facilitator).register(
    config.network,
    new UptoEvmScheme(),
  );

  return paymentMiddleware(
    {
      "POST /v1/responses": {
        accepts: {
          scheme: "upto",
          price: config.maximumPaymentUsd,
          network: config.network,
          payTo: config.payToAddress,
        },
        description: "OpenAI Responses API billed from final token usage",
        mimeType: "application/json",
        extensions: {
          [PAYMENT_IDENTIFIER]: declarePaymentIdentifierExtension(false),
        },
      },
    },
    server,
  );
}
