# @infer402/server

Provider-side Express gateway for usage-priced OpenAI Responses API calls settled in USDC through
x402 on Base or Base Sepolia.

```bash
npm install @infer402/server
```

```ts
import { createGateway, defineConfig } from "@infer402/server";

const config = defineConfig({
  openaiApiKey: process.env.OPENAI_API_KEY!,
  facilitatorUrl: "https://x402.org/facilitator",
  network: "eip155:84532",
  payToAddress: "0x1111111111111111111111111111111111111111",
  redisUrl: "redis://localhost:6379",
  idempotencyHmacSecret: process.env.IDEMPOTENCY_HMAC_SECRET!,
  maximumPaymentUsd: "$1.00",
  pricing: {
    version: "replace-me",
    models: {
      "your-enabled-model": {
        inputUsdPerMillion: "1.00",
        cachedInputUsdPerMillion: "0.10",
        outputUsdPerMillion: "5.00",
        providerMarkupBps: 1000,
        fixedFeeUsd: "0.001"
      }
    }
  }
});

const { app } = createGateway(config);
app.listen(4021);
```

The gateway disables OpenAI SDK retries, forwards `X-Client-Request-Id`, meters returned token
usage, caps settlement at the signed authorization, and caches completed calls by idempotency key.
Use TLS, managed Redis, secret storage, a reviewed pricing table, and a production facilitator on
Base mainnet.
