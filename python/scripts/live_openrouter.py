"""Live integration check: real OpenRouter upstream, simulated on-chain settlement.

Runs the real buyer client and provider gateway over HTTP. The LLM call, token
usage, and charge calculation are REAL (OpenRouter with your key); only the x402
on-chain settlement is simulated by a local mock facilitator, so no funds move.

Usage:
    OPENROUTER_API_KEY=... EVM_PRIVATE_KEY=0x... python scripts/live_openrouter.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import socket
import threading
import time
from typing import Any

import uvicorn
from eth_account import Account
from fastapi import FastAPI, Request
from nacl.signing import SigningKey
from x402.http import CreateHeadersAuthProvider
from x402.schemas import SettleResponse, SupportedKind, SupportedResponse, VerifyResponse

from x402_openai import AsyncX402OpenAI, private_key_signer
from x402_openai.core import PricingTable
from x402_openai.server import (
    GatewayConfig,
    GatewayDependencies,
    ProviderConfig,
    create_gateway,
)

NETWORK = os.environ.get("NETWORK", "eip155:8453")  # Base mainnet by default
MODEL = os.environ.get("MODEL", "openai/gpt-4o-mini")
KEY = os.environ["OPENROUTER_API_KEY"]
PRIV = os.environ["EVM_PRIVATE_KEY"]
# Set FACILITATOR_URL to a real mainnet facilitator for an on-chain settlement.
# Leave unset to simulate settlement locally (no funds move).
FACILITATOR_URL = os.environ.get("FACILITATOR_URL")
# Self-pay by default so the on-chain settlement moves no net funds out of the wallet.
PAY_TO_ADDRESS = os.environ.get("PAY_TO_ADDRESS") or Account.from_key(PRIV).address
FACILITATOR_ADDRESS = "0x000000000000000000000000000000000000dEaD"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# CDP x402 facilitator (real Base mainnet settlement) is used when these are set.
CDP_KEY_ID = os.environ.get("CDP_API_KEY_ID")
CDP_KEY_SECRET = os.environ.get("CDP_API_KEY_SECRET")
CDP_FACILITATOR_URL = "https://api.cdp.coinbase.com/platform/v2/x402"

PRICING = {
    "version": "live-test",
    "models": {
        MODEL: {
            "inputUsdPerMillion": "0.15",
            "cachedInputUsdPerMillion": "0.075",
            "outputUsdPerMillion": "0.60",
            "providerMarkupBps": 1000,
            "fixedFeeUsd": "0.01",
        }
    },
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _cdp_jwt(method: str, host: str, path: str) -> str:
    seed = base64.b64decode(CDP_KEY_SECRET or "")
    signing_key = SigningKey(seed[:32])
    now = int(time.time())
    header = {"typ": "JWT", "alg": "EdDSA", "kid": CDP_KEY_ID, "nonce": secrets.token_hex(16)}
    payload = {
        "sub": CDP_KEY_ID,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "uris": [f"{method} {host}{path}"],
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    return f"{signing_input}.{_b64url(signing_key.sign(signing_input.encode()).signature)}"


def _cdp_create_headers() -> dict[str, dict[str, str]]:
    host, base = "api.cdp.coinbase.com", "/platform/v2/x402"

    def bearer(method: str, path: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {_cdp_jwt(method, host, path)}"}

    return {
        "verify": bearer("POST", f"{base}/verify"),
        "settle": bearer("POST", f"{base}/settle"),
        "supported": bearer("GET", f"{base}/supported"),
    }


class FakeRedis:
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


def _find_address(value: Any) -> str | None:
    if isinstance(value, str):
        return value if ADDRESS_RE.match(value) else None
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


def make_facilitator_app() -> tuple[FastAPI, dict[str, int]]:
    state = {"supported": 0, "verify": 0, "settle": 0}
    app = FastAPI()

    @app.get("/supported")
    async def supported() -> dict[str, Any]:
        state["supported"] += 1
        return SupportedResponse(
            kinds=[
                SupportedKind(
                    x402_version=2,
                    scheme="upto",
                    network=NETWORK,
                    extra={"facilitatorAddress": FACILITATOR_ADDRESS},
                )
            ]
        ).model_dump(by_alias=True)

    @app.post("/verify")
    async def verify(request: Request) -> dict[str, Any]:
        body = await request.json()
        state["verify"] += 1
        return VerifyResponse(
            is_valid=True, payer=_find_address(body.get("paymentPayload", {}))
        ).model_dump(by_alias=True)

    @app.post("/settle")
    async def settle(request: Request) -> dict[str, Any]:
        body = await request.json()
        state["settle"] += 1
        requirements = body.get("paymentRequirements", {})
        return SettleResponse(
            success=True,
            transaction="0x" + "11" * 32,
            network=NETWORK,
            payer=_find_address(body.get("paymentPayload", {})),
            amount=requirements.get("maxAmountRequired") or requirements.get("amount"),
        ).model_dump(by_alias=True)

    return app, state


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class BackgroundServer:
    def __init__(self, app: FastAPI) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        while not self._server.started:
            time.sleep(0.02)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


async def main() -> None:
    mock: BackgroundServer | None = None
    facilitator_auth: Any = None
    facilitator_state: dict[str, int] | None = None
    if CDP_KEY_ID and CDP_KEY_SECRET:
        facilitator_url = CDP_FACILITATOR_URL
        facilitator_auth = CreateHeadersAuthProvider(_cdp_create_headers)
        mode = "REAL CDP facilitator - on-chain settlement on Base mainnet"
    elif FACILITATOR_URL:
        facilitator_url = FACILITATOR_URL
        mode = f"REAL facilitator ({FACILITATOR_URL}) - on-chain settlement"
    else:
        mock_app, facilitator_state = make_facilitator_app()
        mock = BackgroundServer(mock_app)
        mock.start()
        facilitator_url = mock.base_url
        mode = "MOCK facilitator - settlement simulated, no funds move"

    config = GatewayConfig(
        providers=[
            ProviderConfig(
                id="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api="chat",
                api_key=KEY,
            )
        ],
        default_provider="openrouter",
        facilitator_url=facilitator_url,
        network=NETWORK,  # type: ignore[arg-type]
        pay_to_address=PAY_TO_ADDRESS,
        redis_url="redis://localhost:6379",
        idempotency_hmac_secret="x" * 32,
        maximum_payment_usd="$0.50",
        pricing=PricingTable.model_validate(PRICING),
        node_env="test",
        max_output_tokens=1024,
    )
    gateway = BackgroundServer(
        create_gateway(
            config,
            GatewayDependencies(redis=FakeRedis(), facilitator_auth=facilitator_auth),  # type: ignore[arg-type]
        )
    )
    gateway.start()

    print(f"Network: {NETWORK} | {mode}")
    print(f"pay_to: {PAY_TO_ADDRESS}")
    print(f"Provider gateway: {gateway.base_url}  (upstream: OpenRouter, model: {MODEL})")
    try:
        async with AsyncX402OpenAI(
            base_url=f"{gateway.base_url}/v1",
            signer=private_key_signer(PRIV),
            max_payment_usd="$0.50",
            networks=[NETWORK],  # type: ignore[list-item]
        ) as client:
            result = await client.responses.create(
                model=MODEL,
                messages=[
                    {"role": "user", "content": "Reply with exactly: x402 works over OpenRouter."}
                ],
                max_tokens=64,
            )
    finally:
        gateway.stop()
        if mock is not None:
            mock.stop()

    data = result.data
    print("\n=== REAL OpenRouter response ===")
    print("model     :", data.get("model"))
    print("output    :", data["choices"][0]["message"]["content"])
    print("real usage:", data.get("usage"))
    receipt = result.receipt
    assert receipt is not None
    print("\n=== x402 receipt (charge from real usage) ===")
    print(f"model={receipt.model} amountUsd={receipt.amount_usd} atomic={receipt.amount_atomic}")
    print(
        f"tokens in={receipt.input_tokens} cached={receipt.cached_input_tokens} "
        f"out={receipt.output_tokens}"
    )
    print("payment-response header present:", bool(result.payment_response))
    if facilitator_state is not None:
        print("facilitator calls (mocked):", facilitator_state)
    elif result.payment_response:
        raw = result.payment_response
        settle = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
        print("settlement: REAL on-chain")
        print("  success:", settle.get("success"), "| network:", settle.get("network"))
        tx = settle.get("transaction")
        if tx:
            print(f"  tx: https://basescan.org/tx/{tx}")


if __name__ == "__main__":
    asyncio.run(main())
