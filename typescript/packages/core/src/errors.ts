export type X402OpenAIErrorCode =
  | "CONFIGURATION_ERROR"
  | "VALIDATION_ERROR"
  | "PAYMENT_REJECTED"
  | "MODEL_NOT_ALLOWED"
  | "PROVIDER_NOT_ALLOWED"
  | "INVALID_RECEIPT"
  | "IDEMPOTENCY_CONFLICT"
  | "REQUEST_IN_PROGRESS"
  | "CACHE_UNAVAILABLE"
  | "NOT_FOUND"
  | "UPSTREAM_ERROR"
  | "REQUEST_FAILED";

export class X402OpenAIError extends Error {
  constructor(
    public readonly code: X402OpenAIErrorCode,
    message: string,
    public readonly status = 500,
    public readonly details?: Readonly<Record<string, unknown>>,
  ) {
    super(message);
    this.name = "X402OpenAIError";
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}
