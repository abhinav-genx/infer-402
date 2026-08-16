"""End-to-end payment flow: a real gateway (provider) and real buyer client.

This exercises the full x402 ``upto`` settlement path on Base Sepolia without a
real OpenAI key, a real facilitator, or a funded wallet:

- OpenAI is replaced by an injected fake that returns a raw Responses payload.
- Redis is replaced by an injected in-memory async fake.
- The facilitator is a local mock HTTP server that always verifies/settles.

Both the gateway and the mock facilitator run as real uvicorn servers on
localhost, so the buyer client talks to the provider over real HTTP exactly as
it would in production.
"""

from __future__ import annotations

import copy
import re
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from x402.schemas import SettleResponse, SupportedKind, SupportedResponse, VerifyResponse

from x402_openai import (
    USDC_BY_NETWORK,
    AsyncX402OpenAI,
    X402OpenAI,
    private_key_signer,
)
from x402_openai.core import PricingTable, TokenUsage
from x402_openai.server import (
    GatewayConfig,
    GatewayDependencies,
    ProviderConfig,
    calculate_charge,
    create_gateway,
)

# Well-known Hardhat/Anvil test account #0. Public test key, never holds funds.
TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
NETWORK = "eip155:84532"  # Base Sepolia
MODEL = "gpt-4o-mini-test"
ANSWER_TEXT = "x402 lets a client pay per API call in USDC over an HTTP 402 challenge."

# Raw Responses API payload the fake OpenAI returns (no real API call).
RAW_OPENAI_RESPONSE: dict[str, Any] = {
    "id": "resp_local_0001",
    "object": "response",
    "created_at": 1734300000,
    "model": MODEL,
    "status": "completed",
    "output": [
        {
            "id": "msg_local_0001",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": ANSWER_TEXT,
                    "annotations": [],
                }
            ],
        }
    ],
    "output_text": ANSWER_TEXT,
    "usage": {
        "input_tokens": 1200,
        "input_tokens_details": {"cached_tokens": 400},
        "output_tokens": 350,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 1550,
    },
}

PRICING = {
    "version": "local-test-v1",
    "models": {
        MODEL: {
            "inputUsdPerMillion": "0.15",
            "cachedInputUsdPerMillion": "0.075",
            "outputUsdPerMillion": "0.60",
            "providerMarkupBps": 1000,
            "fixedFeeUsd": "0.0001",
        }
    },
}

# OpenRouter-style provider using the OpenAI-compatible Chat Completions API.
CHAT_MODEL = "anthropic/claude-3.5-sonnet"
CHAT_COMPLETION: dict[str, Any] = {
    "id": "chatcmpl_local_0001",
    "object": "chat.completion",
    "created": 1734300000,
    "model": CHAT_MODEL,
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": ANSWER_TEXT},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 1200,
        "prompt_tokens_details": {"cached_tokens": 400},
        "completion_tokens": 350,
        "total_tokens": 1550,
    },
}
CHAT_PRICING = {
    "version": "local-test-v1",
    "models": {
        CHAT_MODEL: {
            "inputUsdPerMillion": "0.15",
            "cachedInputUsdPerMillion": "0.075",
            "outputUsdPerMillion": "0.60",
            "providerMarkupBps": 1000,
            "fixedFeeUsd": "0.0001",
        }
    },
}


# --------------------------------------------------------------------------- #
# Fakes for the gateway's external dependencies.
# --------------------------------------------------------------------------- #
class _FakeUsage:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(self._data)


class _FakeResponse:
    def __init__(self, body: dict[str, Any], request_id: str) -> None:
        self._body = body
        self.usage = _FakeUsage(body["usage"])
        self._request_id = request_id

    def model_dump(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(self._body)


class _FakeResponsesResource:
    def __init__(self, body: dict[str, Any], request_id: str) -> None:
        self._body = body
        self._request_id = request_id
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self._body, self._request_id)


class FakeAsyncOpenAI:
    """Minimal stand-in for ``openai.AsyncOpenAI`` used by the gateway."""

    def __init__(self, body: dict[str, Any], request_id: str = "resp_local_0001") -> None:
        self.responses = _FakeResponsesResource(body, request_id)


class _FakeChatCompletions:
    def __init__(self, body: dict[str, Any], request_id: str) -> None:
        self._body = body
        self._request_id = request_id
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self._body, self._request_id)


class _FakeChat:
    def __init__(self, body: dict[str, Any], request_id: str) -> None:
        self.completions = _FakeChatCompletions(body, request_id)


class FakeChatOpenAI:
    """Stand-in for an OpenAI-compatible chat provider (OpenRouter, Gemini, ...)."""

    def __init__(self, body: dict[str, Any], request_id: str = "chatcmpl_local_0001") -> None:
        self.chat = _FakeChat(body, request_id)
        self.calls = self.chat.completions.calls


class FakeRedis:
    """In-memory async subset of redis-py used by the idempotency middleware."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self._store.pop(key, None) is not None:
                removed += 1
        return removed

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Mock facilitator: always verifies and settles, no chain access.
# --------------------------------------------------------------------------- #
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_FACILITATOR_ADDRESS = "0x000000000000000000000000000000000000dEaD"


def _find_address(value: Any) -> str | None:
    if isinstance(value, str):
        return value if _ADDRESS_RE.match(value) else None
    if isinstance(value, dict):
        for item in value.values():
            found = _find_address(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_address(item)
            if found:
                return found
    return None


@dataclass
class FacilitatorState:
    supported: int = 0
    verify: int = 0
    settle: int = 0
    last_settle_body: dict[str, Any] | None = None


def make_facilitator_app(network: str) -> tuple[FastAPI, FacilitatorState]:
    state = FacilitatorState()
    app = FastAPI()

    @app.get("/supported")
    async def supported() -> dict[str, Any]:
        state.supported += 1
        return SupportedResponse(
            kinds=[
                SupportedKind(
                    x402_version=2,
                    scheme="upto",
                    network=network,
                    extra={"facilitatorAddress": _FACILITATOR_ADDRESS},
                )
            ]
        ).model_dump(by_alias=True)

    @app.post("/verify")
    async def verify(request: Request) -> dict[str, Any]:
        body = await request.json()
        state.verify += 1
        payer = _find_address(body.get("paymentPayload", {}))
        return VerifyResponse(is_valid=True, payer=payer).model_dump(by_alias=True)

    @app.post("/settle")
    async def settle(request: Request) -> dict[str, Any]:
        body = await request.json()
        state.settle += 1
        state.last_settle_body = body
        payer = _find_address(body.get("paymentPayload", {}))
        requirements = body.get("paymentRequirements", {})
        amount = requirements.get("maxAmountRequired") or requirements.get("amount")
        return SettleResponse(
            success=True,
            transaction="0x" + "11" * 32,
            network=network,
            payer=payer,
            amount=amount,
        ).model_dump(by_alias=True)

    return app, state


# --------------------------------------------------------------------------- #
# Run FastAPI apps as real localhost servers for a faithful HTTP round trip.
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class BackgroundServer:
    def __init__(self, app: FastAPI) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        self._thread.start()
        deadline = time.time() + timeout
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("uvicorn server did not start in time")
            time.sleep(0.02)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=15.0)


@dataclass
class Stack:
    gateway_url: str
    facilitator: FacilitatorState
    fake_openai: FakeAsyncOpenAI
    fake_redis: FakeRedis
    config: GatewayConfig
    expected_atomic: int
    expected_usd: str


@pytest.fixture
def stack() -> Any:
    fake_openai = FakeAsyncOpenAI(RAW_OPENAI_RESPONSE)
    fake_redis = FakeRedis()

    facilitator_app, facilitator_state = make_facilitator_app(NETWORK)
    facilitator_server = BackgroundServer(facilitator_app)
    facilitator_server.start()

    config = GatewayConfig(
        openai_api_key="fake-key-unused",
        facilitator_url=facilitator_server.base_url,
        network=NETWORK,
        pay_to_address="0x000000000000000000000000000000000000bEEF",
        redis_url="redis://localhost:6379",
        idempotency_hmac_secret="x" * 32,
        maximum_payment_usd="$1.00",
        pricing=PricingTable.model_validate(PRICING),
        node_env="test",
        max_output_tokens=16_000,
    )

    gateway_app = create_gateway(
        config,
        GatewayDependencies(redis=fake_redis, openai=fake_openai),  # type: ignore[arg-type]
    )
    gateway_server = BackgroundServer(gateway_app)
    gateway_server.start()

    charge = calculate_charge(
        config.pricing,
        MODEL,
        TokenUsage(input_tokens=1200, output_tokens=350, cached_input_tokens=400),
        config.maximum_payment_atomic,
    )

    try:
        yield Stack(
            gateway_url=gateway_server.base_url,
            facilitator=facilitator_state,
            fake_openai=fake_openai,
            fake_redis=fake_redis,
            config=config,
            expected_atomic=charge.amount_atomic,
            expected_usd=charge.amount_usd,
        )
    finally:
        gateway_server.stop()
        facilitator_server.stop()


def _assert_paid_response(
    stack: Stack, data: dict[str, Any], receipt: Any, payment_response: Any
) -> None:
    assert data["output_text"] == RAW_OPENAI_RESPONSE["output_text"]
    assert data["id"] == RAW_OPENAI_RESPONSE["id"]
    assert data["model"] == MODEL

    assert receipt is not None, "provider must return a signed receipt"
    assert receipt.network == NETWORK
    assert receipt.asset == USDC_BY_NETWORK[NETWORK]
    assert receipt.model == MODEL
    assert receipt.pricing_version == "local-test-v1"
    assert receipt.input_tokens == 1200
    assert receipt.cached_input_tokens == 400
    assert receipt.output_tokens == 350
    assert receipt.amount_atomic == str(stack.expected_atomic)
    assert receipt.amount_usd == stack.expected_usd

    assert payment_response is not None, "settlement response header must be present"
    assert stack.facilitator.supported >= 1
    assert stack.facilitator.verify >= 1
    assert stack.facilitator.settle >= 1


def test_sync_buyer_pays_provider_end_to_end(stack: Stack) -> None:
    client = X402OpenAI(
        base_url=f"{stack.gateway_url}/v1",
        signer=private_key_signer(TEST_PRIVATE_KEY),
        max_payment_usd="$2.00",
        networks=[NETWORK],
    )
    result = client.responses.create(
        model=MODEL,
        input="Explain x402 in one sentence.",
        max_output_tokens=512,
    )

    _assert_paid_response(stack, result.data, result.receipt, result.payment_response)

    # The provider actually invoked (the fake) OpenAI exactly once with our body.
    assert len(stack.fake_openai.responses.calls) == 1
    call = stack.fake_openai.responses.calls[0]
    assert call["model"] == MODEL
    assert call["max_output_tokens"] == 512

    # Expected charge for this fixed usage is deterministic.
    assert stack.expected_atomic == 496
    assert stack.expected_usd == "0.000496"


@pytest.mark.asyncio
async def test_async_buyer_pays_provider_end_to_end(stack: Stack) -> None:
    async with AsyncX402OpenAI(
        base_url=f"{stack.gateway_url}/v1",
        signer=private_key_signer(TEST_PRIVATE_KEY),
        max_payment_usd="$2.00",
        networks=[NETWORK],
    ) as client:
        result = await client.responses.create(
            model=MODEL,
            input="Explain x402 in one sentence.",
            max_output_tokens=512,
        )

    _assert_paid_response(stack, result.data, result.receipt, result.payment_response)


def test_idempotent_replay_does_not_resettle(stack: Stack) -> None:
    client = X402OpenAI(
        base_url=f"{stack.gateway_url}/v1",
        signer=private_key_signer(TEST_PRIVATE_KEY),
        max_payment_usd="$2.00",
        networks=[NETWORK],
    )
    key = "req_replay_0000000000000001"
    first = client.responses.create(
        model=MODEL, input="hello", max_output_tokens=256, idempotency_key=key
    )
    settles_after_first = stack.facilitator.settle
    openai_calls_after_first = len(stack.fake_openai.responses.calls)

    second = client.responses.create(
        model=MODEL, input="hello", max_output_tokens=256, idempotency_key=key
    )

    assert first.data == second.data
    # Replay is served from cache: no additional settlement or upstream call.
    assert stack.facilitator.settle == settles_after_first
    assert len(stack.fake_openai.responses.calls) == openai_calls_after_first


def test_health_and_payment_challenge(stack: Stack) -> None:
    live = httpx.get(f"{stack.gateway_url}/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}

    ready = httpx.get(f"{stack.gateway_url}/health/ready")
    assert ready.status_code == 200

    # Missing Idempotency-Key is rejected before any payment handling.
    missing_key = httpx.post(
        f"{stack.gateway_url}/v1/responses",
        json={"model": MODEL, "input": "hi", "max_output_tokens": 64},
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "VALIDATION_ERROR"

    # A valid key with no payment yields the 402 challenge with requirements.
    challenge = httpx.post(
        f"{stack.gateway_url}/v1/responses",
        json={"model": MODEL, "input": "hi", "max_output_tokens": 64},
        headers={"Idempotency-Key": "req_challenge_000000000001"},
    )
    assert challenge.status_code == 402
    assert any(name.lower() == "payment-required" for name in challenge.headers)


def test_chat_provider_end_to_end() -> None:
    """A chat-flavored provider (OpenRouter/Gemini/etc.) settles and bills correctly."""
    facilitator_app, facilitator_state = make_facilitator_app(NETWORK)
    facilitator_server = BackgroundServer(facilitator_app)
    facilitator_server.start()

    fake_chat = FakeChatOpenAI(CHAT_COMPLETION)
    fake_redis = FakeRedis()
    config = GatewayConfig(
        providers=[
            ProviderConfig(
                id="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api="chat",
                api_key="fake-key-unused",
            )
        ],
        default_provider="openrouter",
        facilitator_url=facilitator_server.base_url,
        network=NETWORK,
        pay_to_address="0x000000000000000000000000000000000000bEEF",
        redis_url="redis://localhost:6379",
        idempotency_hmac_secret="x" * 32,
        maximum_payment_usd="$1.00",
        pricing=PricingTable.model_validate(CHAT_PRICING),
        node_env="test",
        max_output_tokens=16_000,
    )
    gateway_app = create_gateway(
        config,
        GatewayDependencies(redis=fake_redis, clients={"openrouter": fake_chat}),  # type: ignore[arg-type]
    )
    gateway_server = BackgroundServer(gateway_app)
    gateway_server.start()

    expected = calculate_charge(
        config.pricing,
        CHAT_MODEL,
        TokenUsage(input_tokens=1200, output_tokens=350, cached_input_tokens=400),
        config.maximum_payment_atomic,
    )

    try:
        client = X402OpenAI(
            base_url=f"{gateway_server.base_url}/v1",
            signer=private_key_signer(TEST_PRIVATE_KEY),
            max_payment_usd="$2.00",
            networks=[NETWORK],
        )
        result = client.responses.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": "Explain x402 in one sentence."}],
            max_tokens=512,
        )
    finally:
        gateway_server.stop()
        facilitator_server.stop()

    assert result.data["object"] == "chat.completion"
    assert result.data["choices"][0]["message"]["content"] == ANSWER_TEXT

    receipt = result.receipt
    assert receipt is not None
    assert receipt.model == CHAT_MODEL
    assert receipt.network == NETWORK
    assert receipt.input_tokens == 1200
    assert receipt.cached_input_tokens == 400
    assert receipt.output_tokens == 350
    assert receipt.amount_atomic == str(expected.amount_atomic)
    assert result.payment_response is not None

    # The gateway routed to the chat provider and forwarded a chat body.
    assert len(fake_chat.calls) == 1
    call = fake_chat.calls[0]
    assert call["max_tokens"] == 512
    assert "messages" in call
    assert "provider" not in call  # the routing hint is stripped before forwarding
    assert facilitator_state.settle >= 1
