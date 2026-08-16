import { X402OpenAI, privateKeySigner } from "@infer402/client";

const privateKey = process.env.EVM_PRIVATE_KEY as `0x${string}` | undefined;
if (!privateKey) throw new Error("EVM_PRIVATE_KEY is required");

const client = new X402OpenAI({
  baseURL: process.env.PROVIDER_URL ?? "http://localhost:4021/v1",
  signer: privateKeySigner(privateKey),
  maxPaymentUsd: "$0.50",
  networks: ["eip155:84532"],
});

const result = await client.responses.create({
  model: process.env.MODEL ?? "your-enabled-model",
  input: "Explain HTTP 402 in one sentence.",
  max_output_tokens: 200,
});

console.log(result.data.output_text);
console.log(result.receipt);
