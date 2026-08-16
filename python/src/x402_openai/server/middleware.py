from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from redis.asyncio import Redis
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from x402.extensions.payment_identifier import (
    PAYMENT_IDENTIFIER,
    declare_payment_identifier_extension,
)
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.upto import UptoEvmServerScheme
from x402.server import x402ResourceServer

from .types import GatewayConfig

KEY_PATTERN = __import__("re").compile(r"^[A-Za-z0-9_-]{16,128}$")
REPLAY_HEADERS = {
    b"content-type",
    b"payment-response",
    b"x-openai-request-id",
    b"x-provider",
    b"x-x402-receipt",
}


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _canonical_body(body: bytes) -> bytes:
    try:
        value = json.loads(body)
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body


async def _send_json(send: Send, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, redis: Redis, secret: str, ttl_seconds: int) -> None:
        self.app = app
        self.redis = redis
        self.secret = secret.encode()
        self.ttl_seconds = ttl_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/v1/responses":
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers", []))
        public_key = _header(headers, b"idempotency-key")
        if public_key is None or KEY_PATTERN.fullmatch(public_key) is None:
            await _send_json(
                send,
                400,
                {"error": {"code": "VALIDATION_ERROR", "message": "Invalid Idempotency-Key"}},
            )
            return

        messages: list[Message] = []
        body = b""
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return

        fingerprint = hashlib.sha256(
            scope["method"].encode()
            + b"\0"
            + scope["path"].encode()
            + b"\0"
            + _canonical_body(body)
        ).hexdigest()
        digest = hmac.new(self.secret, public_key.encode(), hashlib.sha256).hexdigest()
        key = f"x402-openai:idempotency:{digest}"
        lock_key = f"{key}:lock"

        try:
            cached_json = await self.redis.get(key)
        except Exception:
            await _send_json(
                send,
                503,
                {
                    "error": {
                        "code": "CACHE_UNAVAILABLE",
                        "message": "Service unavailable",
                    }
                },
            )
            return

        if cached_json:
            cached = json.loads(cached_json)
            if not hmac.compare_digest(cached["fingerprint"], fingerprint):
                await _send_json(
                    send,
                    409,
                    {
                        "error": {
                            "code": "IDEMPOTENCY_CONFLICT",
                            "message": "Key reused for another request",
                        }
                    },
                )
                return
            replay_headers = [
                (name.encode("latin-1"), value.encode("latin-1"))
                for name, value in cached["headers"]
            ]
            replay_headers.append((b"x-idempotency-replayed", b"true"))
            cached_body = base64.b64decode(cached["body"])
            await send(
                {
                    "type": "http.response.start",
                    "status": cached["status"],
                    "headers": replay_headers,
                }
            )
            await send({"type": "http.response.body", "body": cached_body, "more_body": False})
            return

        paid = _header(headers, b"payment-signature") or _header(headers, b"x-payment")
        if not paid:
            await self.app(scope, _replay_receive(messages), send)
            return

        lock = await self.redis.set(lock_key, fingerprint, ex=180, nx=True)
        if not lock:
            await _send_json(
                send,
                425,
                {
                    "error": {
                        "code": "REQUEST_IN_PROGRESS",
                        "message": "Request is processing",
                    }
                },
            )
            return

        captured: list[Message] = []

        async def capture(message: Message) -> None:
            captured.append(message)

        try:
            await self.app(scope, _replay_receive(messages), capture)
            start = next(
                (message for message in captured if message["type"] == "http.response.start"),
                None,
            )
            status = int(start["status"]) if start else 500
            if 200 <= status < 300 and start:
                response_headers = [
                    (name.decode("latin-1"), value.decode("latin-1"))
                    for name, value in start.get("headers", [])
                    if name.lower() in REPLAY_HEADERS
                ]
                response_body = b"".join(
                    message.get("body", b"")
                    for message in captured
                    if message["type"] == "http.response.body"
                )
                entry = {
                    "fingerprint": fingerprint,
                    "status": status,
                    "headers": response_headers,
                    "body": base64.b64encode(response_body).decode(),
                }
                await self.redis.set(key, json.dumps(entry), ex=self.ttl_seconds)
            for message in captured:
                await send(message)
        finally:
            await self.redis.delete(lock_key)


def _replay_receive(messages: list[Message]) -> Receive:
    remaining = list(messages)

    async def receive() -> Message:
        if remaining:
            return remaining.pop(0)
        return {"type": "http.disconnect"}

    return receive


def install_x402_middleware(
    app: Any, config: GatewayConfig, facilitator_auth: Any = None
) -> None:
    facilitator = HTTPFacilitatorClient(
        FacilitatorConfig(url=config.facilitator_url, auth_provider=facilitator_auth)
    )
    server = x402ResourceServer(facilitator)
    server.register(config.network, UptoEvmServerScheme())  # type: ignore[no-untyped-call]
    routes = {
        "POST /v1/responses": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="upto",
                    pay_to=config.pay_to_address,
                    price=config.maximum_payment_usd,
                    network=config.network,
                )
            ],
            mime_type="application/json",
            description="OpenAI Responses API billed from final token usage",
            extensions={PAYMENT_IDENTIFIER: declare_payment_identifier_extension(False)},
        )
    }
    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
