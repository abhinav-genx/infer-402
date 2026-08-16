# Payment flow

1. The buyer sends `POST /v1/responses` with an idempotency key.
2. The provider returns x402 `402 Payment Required` with an `upto` authorization ceiling.
3. The buyer validates the network, canonical USDC contract, and maximum atomic amount.
4. The buyer signs locally and retries with the same request and payment identifier.
5. The provider verifies the authorization and calls the OpenAI Responses API.
6. Returned token usage is priced using the provider's versioned table.
7. The provider settles the calculated amount, never more than the authorization.
8. The response includes the OpenAI result, payment settlement data, and receipt.

Validation or upstream failure settles zero. Duplicate idempotency keys with the same request return
the stored response; the same key with a different body returns a conflict.
