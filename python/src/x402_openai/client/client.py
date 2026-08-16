from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
import uuid
from typing import Any, cast

import httpx
import requests
from x402 import max_amount, x402Client, x402ClientSync
from x402.extensions.payment_identifier import append_payment_identifier_to_extensions
from x402.http.clients import x402_requests, x402HttpxClient
from x402.mechanisms.evm.exact import ExactEvmScheme
from x402.mechanisms.evm.upto import UptoEvmScheme

from x402_openai.core import (
    BASE_MAINNET,
    BASE_SEPOLIA,
    USDC_BY_NETWORK,
    ConfigurationError,
    PaymentReceipt,
    PaymentRejectedError,
    SupportedNetwork,
    X402OpenAIError,
    parse_usd_to_atomic,
)

from .types import ResponseBody, ResponseResult

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _validate_options(
    base_url: str,
    max_payment_usd: str,
    timeout: float,
    max_retries: int,
) -> None:
    parsed = httpx.URL(base_url)
    if not parsed.is_absolute_url:
        raise ConfigurationError("base_url must be an absolute URL")
    parse_usd_to_atomic(max_payment_usd)
    if timeout <= 0:
        raise ConfigurationError("timeout must be positive")
    if max_retries < 0 or max_retries > 10:
        raise ConfigurationError("max_retries must be between 0 and 10")


def _spend_policy(networks: tuple[SupportedNetwork, ...], maximum_atomic: int) -> Any:
    def policy(_version: int, requirements: list[Any]) -> list[Any]:
        allowed: list[Any] = []
        for requirement in requirements:
            network = str(requirement.network)
            if network not in networks:
                continue
            expected_asset = USDC_BY_NETWORK[network]
            if str(requirement.asset).lower() != expected_asset.lower():
                continue
            try:
                if int(requirement.amount) <= maximum_atomic:
                    allowed.append(requirement)
            except (TypeError, ValueError):
                continue
        return allowed

    return policy


def _attach_payment_id(payment_id: str) -> Any:
    def hook(context: Any) -> None:
        extensions = getattr(context.payment_required, "extensions", None)
        if extensions:
            append_payment_identifier_to_extensions(extensions, payment_id)

    return hook


def _async_payment_client(
    signer: Any,
    networks: tuple[SupportedNetwork, ...],
    maximum_atomic: int,
    payment_id: str,
) -> x402Client:
    client = x402Client()
    for network in networks:
        client.register(network, ExactEvmScheme(signer))
        client.register(network, UptoEvmScheme(signer))
    client.register_policy(max_amount(maximum_atomic))
    client.register_policy(_spend_policy(networks, maximum_atomic))
    client.on_before_payment_creation(_attach_payment_id(payment_id))
    return client


def _sync_payment_client(
    signer: Any,
    networks: tuple[SupportedNetwork, ...],
    maximum_atomic: int,
    payment_id: str,
) -> x402ClientSync:
    client = x402ClientSync()
    for network in networks:
        client.register(network, ExactEvmScheme(signer))
        client.register(network, UptoEvmScheme(signer))
    client.register_policy(max_amount(maximum_atomic))
    client.register_policy(_spend_policy(networks, maximum_atomic))
    client.on_before_payment_creation(_attach_payment_id(payment_id))
    return client


def _decode_receipt(value: str | None) -> PaymentReceipt | None:
    if not value:
        return None
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        return PaymentReceipt.model_validate_json(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise X402OpenAIError(
            "INVALID_RECEIPT",
            "Provider returned an invalid receipt",
            502,
        ) from exc


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), 30.0)
    ceiling = min(0.25 * (2**attempt), 5.0)
    return random.uniform(ceiling / 2, ceiling)  # noqa: S311 - jitter is not cryptographic


def _validate_body(body: ResponseBody) -> None:
    if body.get("stream") is True:
        raise X402OpenAIError(
            "VALIDATION_ERROR",
            "Streaming cannot be usage-settled before the complete response is available",
            400,
        )
    if not isinstance(body.get("model"), str) or not body["model"]:
        raise X402OpenAIError("VALIDATION_ERROR", "model is required", 400)


class _AsyncResponses:
    def __init__(self, owner: AsyncX402OpenAI) -> None:
        self._owner = owner

    async def create(
        self,
        *,
        idempotency_key: str | None = None,
        **body: Any,
    ) -> ResponseResult:
        return await self._owner._create_response(body, idempotency_key)


class AsyncX402OpenAI:
    def __init__(
        self,
        *,
        base_url: str,
        signer: Any,
        max_payment_usd: str = "$1.00",
        networks: list[SupportedNetwork] | tuple[SupportedNetwork, ...] | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        _validate_options(base_url, max_payment_usd, timeout, max_retries)
        self.base_url = base_url.rstrip("/")
        self.signer = signer
        self.networks = tuple(networks or (BASE_MAINNET, BASE_SEPOLIA))
        self.maximum_atomic = parse_usd_to_atomic(max_payment_usd)
        self.timeout = timeout
        self.max_retries = max_retries
        self.responses = _AsyncResponses(self)

    async def _create_response(
        self, body: ResponseBody, idempotency_key: str | None
    ) -> ResponseResult:
        _validate_body(body)
        key = idempotency_key or f"req_{uuid.uuid4().hex}"
        if _IDEMPOTENCY_KEY.fullmatch(key) is None:
            raise X402OpenAIError("VALIDATION_ERROR", "Invalid idempotency_key", 400)

        payment_client = _async_payment_client(self.signer, self.networks, self.maximum_atomic, key)
        last_error: Exception | None = None

        async with x402HttpxClient(payment_client, timeout=self.timeout) as http:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await http.post(
                        f"{self.base_url}/responses",
                        json=body,
                        headers={"Idempotency-Key": key, "X-Client-Request-Id": key},
                    )
                    if response.is_success:
                        payload = cast(dict[str, Any], response.json())
                        return ResponseResult(
                            data=payload,
                            idempotency_key=key,
                            payment_response=response.headers.get("payment-response"),
                            receipt=_decode_receipt(response.headers.get("x-x402-receipt")),
                        )
                    if response.status_code == 402:
                        raise PaymentRejectedError("Provider rejected the x402 payment")
                    if attempt >= self.max_retries or response.status_code not in RETRYABLE_STATUS:
                        raise X402OpenAIError(
                            "REQUEST_FAILED",
                            f"Provider returned HTTP {response.status_code}",
                            response.status_code,
                        )
                    await asyncio.sleep(_retry_delay(attempt, response.headers.get("retry-after")))
                except httpx.RequestError as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        raise X402OpenAIError(
                            "REQUEST_FAILED", "Provider request failed", 502
                        ) from exc
                    await asyncio.sleep(_retry_delay(attempt, None))

        raise X402OpenAIError(
            "REQUEST_FAILED",
            "Provider request failed after retries",
            502,
            {"reason": str(last_error) if last_error else "unknown"},
        )

    async def aclose(self) -> None:
        # A fresh payment-aware HTTP client is scoped to each logical operation.
        return None

    async def __aenter__(self) -> AsyncX402OpenAI:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()


class _SyncResponses:
    def __init__(self, owner: X402OpenAI) -> None:
        self._owner = owner

    def create(self, *, idempotency_key: str | None = None, **body: Any) -> ResponseResult:
        return self._owner._create_response(body, idempotency_key)


class X402OpenAI:
    def __init__(
        self,
        *,
        base_url: str,
        signer: Any,
        max_payment_usd: str = "$1.00",
        networks: list[SupportedNetwork] | tuple[SupportedNetwork, ...] | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        _validate_options(base_url, max_payment_usd, timeout, max_retries)
        self.base_url = base_url.rstrip("/")
        self.signer = signer
        self.networks = tuple(networks or (BASE_MAINNET, BASE_SEPOLIA))
        self.maximum_atomic = parse_usd_to_atomic(max_payment_usd)
        self.timeout = timeout
        self.max_retries = max_retries
        self.responses = _SyncResponses(self)

    def _create_response(self, body: ResponseBody, idempotency_key: str | None) -> ResponseResult:
        _validate_body(body)
        key = idempotency_key or f"req_{uuid.uuid4().hex}"
        if _IDEMPOTENCY_KEY.fullmatch(key) is None:
            raise X402OpenAIError("VALIDATION_ERROR", "Invalid idempotency_key", 400)

        payment_client = _sync_payment_client(self.signer, self.networks, self.maximum_atomic, key)
        session = x402_requests(payment_client)
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    response = session.post(
                        f"{self.base_url}/responses",
                        json=body,
                        headers={"Idempotency-Key": key, "X-Client-Request-Id": key},
                        timeout=self.timeout,
                    )
                    if response.ok:
                        return ResponseResult(
                            data=cast(dict[str, Any], response.json()),
                            idempotency_key=key,
                            payment_response=response.headers.get("payment-response"),
                            receipt=_decode_receipt(response.headers.get("x-x402-receipt")),
                        )
                    if response.status_code == 402:
                        raise PaymentRejectedError("Provider rejected the x402 payment")
                    if attempt >= self.max_retries or response.status_code not in RETRYABLE_STATUS:
                        raise X402OpenAIError(
                            "REQUEST_FAILED",
                            f"Provider returned HTTP {response.status_code}",
                            response.status_code,
                        )
                    time.sleep(_retry_delay(attempt, response.headers.get("retry-after")))
                except requests.RequestException as exc:
                    if attempt >= self.max_retries:
                        raise X402OpenAIError(
                            "REQUEST_FAILED", "Provider request failed", 502
                        ) from exc
                    time.sleep(_retry_delay(attempt, None))
        finally:
            session.close()

        raise X402OpenAIError("REQUEST_FAILED", "Provider request failed after retries", 502)

    def close(self) -> None:
        return None

    def __enter__(self) -> X402OpenAI:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
