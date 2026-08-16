# Stable error codes

| Code | Meaning |
| --- | --- |
| `CONFIGURATION_ERROR` | Invalid deployment or client configuration |
| `VALIDATION_ERROR` | Invalid request or idempotency key |
| `PAYMENT_REJECTED` | No acceptable x402 payment was produced or verified |
| `MODEL_NOT_ALLOWED` | Model is not in the provider pricing allowlist |
| `PROVIDER_NOT_ALLOWED` | Requested upstream provider is not enabled |
| `IDEMPOTENCY_CONFLICT` | Key was reused for a different request |
| `UPSTREAM_ERROR` | Upstream provider failed or omitted billable usage |
| `REQUEST_FAILED` | Provider request failed |
