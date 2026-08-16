import type {
  Response as OpenAIResponse,
  ResponseCreateParamsNonStreaming,
} from "openai/resources/responses/responses";
import type { LocalAccount } from "viem";

import type { PaymentReceipt, SupportedNetwork } from "@infer402/core";

export type PaymentSigner = LocalAccount;

export interface X402OpenAIOptions {
  /** Provider URL including `/v1`, for example https://provider.example/v1. */
  readonly baseURL: string;
  /** Local viem account. The private key is never sent to the provider. */
  readonly signer: PaymentSigner;
  /** Maximum USDC authorization for one call. Defaults to $1.00. */
  readonly maxPaymentUsd?: `$${number}`;
  /** Allowed payment networks. Defaults to Base and Base Sepolia. */
  readonly networks?: readonly SupportedNetwork[];
  /** Timeout for the entire 402 challenge and paid retry. Defaults to 120 seconds. */
  readonly timeoutMs?: number;
  /** Retries for transient failures. Defaults to two. */
  readonly maxRetries?: number;
  /** Custom fetch implementation, primarily for testing or instrumentation. */
  readonly fetch?: typeof globalThis.fetch;
}

export interface ResponseRequestOptions {
  /** Reuse the same key when retrying one logical operation. */
  readonly idempotencyKey?: string;
  readonly signal?: AbortSignal;
}

export interface PaidResponse<T> {
  readonly data: T;
  readonly idempotencyKey: string;
  readonly paymentResponse?: string;
  readonly receipt?: PaymentReceipt;
}

export type CreateResponseBody = ResponseCreateParamsNonStreaming;
export type CreateResponseResult = PaidResponse<OpenAIResponse>;
