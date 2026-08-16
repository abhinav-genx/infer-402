import { setTimeout as delay } from "node:timers/promises";

import { generatePaymentId } from "@x402/extensions/payment-identifier";

import { receiptSchema, X402OpenAIError } from "@infer402/core";

import { createPaymentFetch } from "./signer.js";
import type {
  CreateResponseBody,
  CreateResponseResult,
  ResponseRequestOptions,
  X402OpenAIOptions,
} from "./types.js";

const RETRYABLE_STATUS = new Set([408, 409, 425, 429, 500, 502, 503, 504]);
const IDEMPOTENCY_KEY = /^[A-Za-z0-9_-]{16,128}$/;

function requestSignal(timeoutMs: number, callerSignal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(timeoutMs);
  return callerSignal ? AbortSignal.any([timeout, callerSignal]) : timeout;
}

function retryDelay(attempt: number, retryAfter: string | null): number {
  if (retryAfter && /^\d+$/.test(retryAfter)) {
    return Math.min(Number(retryAfter) * 1_000, 30_000);
  }

  const ceiling = Math.min(250 * 2 ** attempt, 5_000);
  return Math.floor(ceiling / 2 + Math.random() * (ceiling / 2));
}

async function providerError(response: Response): Promise<X402OpenAIError> {
  let message = `Provider returned HTTP ${response.status}`;
  try {
    const payload = (await response.json()) as { error?: string | { message?: string } };
    if (typeof payload.error === "string") message = payload.error;
    if (typeof payload.error === "object" && payload.error.message) {
      message = payload.error.message;
    }
  } catch {
    // Do not expose arbitrary proxy bodies; they can contain internal information.
  }

  const requestId = response.headers.get("x-request-id");
  return new X402OpenAIError("REQUEST_FAILED", message, response.status, {
    ...(requestId ? { requestId } : {}),
  });
}

export class X402OpenAI {
  public readonly responses: {
    create: (
      body: CreateResponseBody,
      options?: ResponseRequestOptions,
    ) => Promise<CreateResponseResult>;
  };

  private readonly options: X402OpenAIOptions & {
    readonly baseURL: string;
    readonly timeoutMs: number;
    readonly maxRetries: number;
    readonly fetch: typeof globalThis.fetch;
  };

  constructor(options: X402OpenAIOptions) {
    if (!URL.canParse(options.baseURL)) {
      throw new X402OpenAIError("CONFIGURATION_ERROR", "baseURL must be an absolute URL");
    }
    if (!options.signer) {
      throw new X402OpenAIError("CONFIGURATION_ERROR", "A local payment signer is required");
    }
    const timeoutMs = options.timeoutMs ?? 120_000;
    const maxRetries = options.maxRetries ?? 2;
    if (timeoutMs <= 0) {
      throw new X402OpenAIError("CONFIGURATION_ERROR", "timeoutMs must be positive");
    }
    if (maxRetries < 0 || maxRetries > 10) {
      throw new X402OpenAIError("CONFIGURATION_ERROR", "maxRetries must be between 0 and 10");
    }

    this.options = {
      ...options,
      baseURL: options.baseURL.replace(/\/$/, ""),
      timeoutMs,
      maxRetries,
      fetch: options.fetch ?? globalThis.fetch,
    };
    this.responses = {
      create: (body, requestOptions) => this.createResponse(body, requestOptions),
    };
  }

  private async createResponse(
    body: CreateResponseBody,
    options: ResponseRequestOptions = {},
  ): Promise<CreateResponseResult> {
    if ((body as { stream?: boolean }).stream === true) {
      throw new X402OpenAIError(
        "VALIDATION_ERROR",
        "Streaming cannot be usage-settled before the complete response is available",
        400,
      );
    }
    if (typeof (body as { model?: unknown }).model !== "string" || !(body as { model: string }).model) {
      throw new X402OpenAIError("VALIDATION_ERROR", "model is required", 400);
    }

    const idempotencyKey = options.idempotencyKey ?? generatePaymentId("req_");
    if (!IDEMPOTENCY_KEY.test(idempotencyKey)) {
      throw new X402OpenAIError(
        "VALIDATION_ERROR",
        "idempotencyKey must contain 16-128 URL-safe characters",
        400,
      );
    }

    const paidFetch = createPaymentFetch(this.options, idempotencyKey);
    let lastError: unknown;

    for (let attempt = 0; attempt <= this.options.maxRetries; attempt += 1) {
      try {
        const response = await paidFetch(`${this.options.baseURL}/responses`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "idempotency-key": idempotencyKey,
            "x-client-request-id": idempotencyKey,
          },
          body: JSON.stringify(body),
          signal: requestSignal(this.options.timeoutMs, options.signal),
        });

        if (!response.ok) {
          if (response.status === 402) {
            throw new X402OpenAIError(
              "PAYMENT_REJECTED",
              "Provider rejected the x402 payment",
              402,
            );
          }
          const error = await providerError(response);
          if (attempt < this.options.maxRetries && RETRYABLE_STATUS.has(response.status)) {
            await delay(retryDelay(attempt, response.headers.get("retry-after")), undefined, {
              signal: options.signal,
            });
            continue;
          }
          throw error;
        }

        const data = (await response.json()) as CreateResponseResult["data"];
        const encodedReceipt = response.headers.get("x-x402-receipt");
        let receipt: CreateResponseResult["receipt"];
        if (encodedReceipt) {
          try {
            receipt = receiptSchema.parse(
              JSON.parse(Buffer.from(encodedReceipt, "base64url").toString("utf8")),
            );
          } catch {
            throw new X402OpenAIError(
              "INVALID_RECEIPT",
              "Provider returned an invalid receipt",
              502,
            );
          }
        }
        const paymentResponse = response.headers.get("payment-response") ?? undefined;

        return {
          data,
          idempotencyKey,
          ...(receipt ? { receipt } : {}),
          ...(paymentResponse ? { paymentResponse } : {}),
        };
      } catch (error) {
        lastError = error;
        const networkFailure =
          error instanceof TypeError ||
          (error instanceof Error &&
            (error.name === "AbortError" || error.name === "TimeoutError"));

        if (!networkFailure || attempt >= this.options.maxRetries || options.signal?.aborted) {
          throw error;
        }

        await delay(retryDelay(attempt, null), undefined, { signal: options.signal });
      }
    }

    throw new X402OpenAIError("REQUEST_FAILED", "Request failed after retries", 502, {
      reason: lastError instanceof Error ? lastError.message : "unknown",
    });
  }
}
