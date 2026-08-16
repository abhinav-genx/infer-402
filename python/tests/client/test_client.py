import pytest

from x402_openai import AsyncX402OpenAI, X402OpenAI
from x402_openai.core import X402OpenAIError


def test_rejects_relative_url() -> None:
    with pytest.raises(X402OpenAIError, match="absolute URL"):
        X402OpenAI(base_url="/v1", signer=object())


@pytest.mark.asyncio
async def test_rejects_streaming_before_network_call() -> None:
    client = AsyncX402OpenAI(base_url="https://provider.example/v1", signer=object())
    with pytest.raises(X402OpenAIError, match="Streaming"):
        await client.responses.create(model="example", input="hello", stream=True)
