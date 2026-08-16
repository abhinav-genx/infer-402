# x402-openai for Python

Install the buyer client:

```bash
pip install x402-openai
```

Install buyer and provider components:

```bash
pip install "x402-openai[server]"
```

```python
from x402_openai import AsyncX402OpenAI, private_key_signer

client = AsyncX402OpenAI(
    base_url="https://provider.example/v1",
    signer=private_key_signer("0x..."),
    max_payment_usd="$0.50",
    networks=["eip155:84532"],
)

result = await client.responses.create(
    model="your-enabled-model",
    input="Explain x402 in one sentence.",
    max_output_tokens=200,
)
print(result.data["output_text"])
await client.aclose()
```

The synchronous `X402OpenAI` class exposes the same `responses.create(...)` interface. Do not put
wallet private keys in browser or untrusted application code.
