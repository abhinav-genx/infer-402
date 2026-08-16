import { createGateway, defineConfig } from "@infer402/server";

const config = defineConfig({
  openaiApiKey: process.env.OPENAI_API_KEY ?? "",
  facilitatorUrl: "https://x402.org/facilitator",
  network: "eip155:84532",
  payToAddress: (process.env.PAY_TO_ADDRESS ?? "") as `0x${string}`,
  redisUrl: process.env.REDIS_URL ?? "redis://localhost:6379",
  idempotencyHmacSecret: process.env.IDEMPOTENCY_HMAC_SECRET ?? "",
  maximumPaymentUsd: "$1.00",
  pricing: {
    version: "replace-before-deploying",
    models: {
      "your-enabled-model": {
        inputUsdPerMillion: "1.00",
        cachedInputUsdPerMillion: "0.10",
        outputUsdPerMillion: "5.00",
        providerMarkupBps: 1000,
        fixedFeeUsd: "0.001",
      },
    },
  },
});

const { app } = createGateway(config);
app.listen(config.port, () => console.log(`Gateway listening on :${config.port}`));
