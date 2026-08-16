# @infer402/client

Buyer-side TypeScript client for OpenAI-compatible Responses endpoints protected by x402.

```bash
npm install @infer402/client
```

```ts
import { X402OpenAI, privateKeySigner } from "@infer402/client";

const client = new X402OpenAI({
  baseURL: "https://provider.example/v1",
  signer: privateKeySigner(process.env.EVM_PRIVATE_KEY as `0x${string}`),
  maxPaymentUsd: "$0.50",
  networks: ["eip155:84532"],
});

const result = await client.responses.create({
  model: "your-enabled-model",
  input: "Hello",
});
```

Use the same `idempotencyKey` when retrying one logical request. Never ship an EVM private key in
browser code; use this SDK in a trusted buyer runtime or provide a hardened external signer.
