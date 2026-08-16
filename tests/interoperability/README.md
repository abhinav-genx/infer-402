# Interoperability tests

The deterministic pricing fixtures are executed by both language test suites. For a live payment
test, start Redis and either gateway, then point the other language's buyer example at it:

1. TypeScript buyer -> Python gateway.
2. Python buyer -> TypeScript gateway.
3. Reuse the idempotency key and assert the response is replayed.
4. Reuse the key with a changed body and assert `409`.
5. Assert the receipt fields match `specs/schemas/receipt.schema.json`.

Live tests require a funded Base Sepolia wallet, OpenAI key, receiving wallet, and test facilitator,
so CI does not spend funds automatically.
