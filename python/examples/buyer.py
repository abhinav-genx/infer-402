import asyncio
import os

from x402_openai import AsyncX402OpenAI, private_key_signer


async def main() -> None:
    client = AsyncX402OpenAI(
        base_url=os.environ.get("PROVIDER_URL", "http://localhost:4021/v1"),
        signer=private_key_signer(os.environ["EVM_PRIVATE_KEY"]),
        max_payment_usd="$0.50",
        networks=["eip155:84532"],
    )
    result = await client.responses.create(
        model=os.environ.get("MODEL", "your-enabled-model"),
        input="Explain x402 in one sentence.",
        max_output_tokens=200,
    )
    print(result.data["output_text"])
    print(result.receipt)


if __name__ == "__main__":
    asyncio.run(main())
