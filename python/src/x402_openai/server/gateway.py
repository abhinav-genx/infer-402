from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import APIError, AsyncOpenAI
from redis.asyncio import Redis
from x402.http.middleware.fastapi import set_settlement_overrides

from x402_openai.core import USDC_BY_NETWORK, X402OpenAIError

from .middleware import IdempotencyMiddleware, install_x402_middleware
from .pricing import calculate_charge, create_receipt
from .providers import create_upstream, token_cap_error
from .types import GatewayConfig

logger = logging.getLogger("x402_openai.server")


@dataclass(slots=True)
class GatewayDependencies:
    redis: Redis | None = None
    openai: AsyncOpenAI | None = None
    clients: dict[str, AsyncOpenAI] | None = None
    facilitator_auth: Any = None


def _error(status: int, code: str, message: str) -> JSONResponse:
    response = JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )
    set_settlement_overrides(response, {"amount": "0"})
    return response


def _resolve_provider(body: dict[str, Any], request: Request, config: GatewayConfig) -> str:
    explicit = body.pop("provider", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    header = request.headers.get("x-provider")
    if header:
        return header
    return config.default_provider


def create_gateway(
    config: GatewayConfig,
    dependencies: GatewayDependencies | None = None,
) -> FastAPI:
    dependencies = dependencies or GatewayDependencies()
    owns_redis = dependencies.redis is None
    redis = dependencies.redis or Redis.from_url(
        config.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    openai = dependencies.openai
    clients: dict[str, AsyncOpenAI] = {
        provider_id: AsyncOpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            max_retries=0,
            timeout=config.openai_timeout_seconds,
        )
        for provider_id, provider in config.provider_map.items()
    }
    if dependencies.clients:
        clients.update(dependencies.clients)
    if openai is not None:
        clients[config.default_provider] = openai

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_redis:
            await redis.aclose()

    app = FastAPI(
        title="x402 OpenAI Gateway",
        version="0.1.0",
        docs_url=None if config.node_env == "production" else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(404)
    async def not_found(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Route not found"}},
        )

    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "content-type",
                "idempotency-key",
                "payment-signature",
                "x-client-request-id",
            ],
            expose_headers=[
                "payment-required",
                "payment-response",
                "x-idempotency-replayed",
                "x-openai-request-id",
                "x-request-id",
                "x-x402-receipt",
            ],
        )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        try:
            await redis.ping()
            return JSONResponse({"status": "ready"})
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)

    @app.post("/v1/responses")
    async def responses(request: Request) -> JSONResponse:
        client_request_id = request.headers.get("x-client-request-id") or str(uuid.uuid4())
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error(400, "VALIDATION_ERROR", "Request body must be valid JSON")

        if not isinstance(body, dict):
            return _error(400, "VALIDATION_ERROR", "Request body must be an object")
        model = body.get("model")
        if not isinstance(model, str) or not model:
            return _error(400, "VALIDATION_ERROR", "model is required")
        if body.get("stream") is True:
            return _error(400, "VALIDATION_ERROR", "Streaming is not supported")
        if model not in config.pricing.models:
            return _error(400, "MODEL_NOT_ALLOWED", f"Model {model} is not enabled")

        provider_id = _resolve_provider(body, request, config)
        provider = config.provider_map.get(provider_id)
        if provider is None:
            return _error(400, "PROVIDER_NOT_ALLOWED", f"Provider {provider_id} is not enabled")
        cap_error = token_cap_error(provider.api, body, config.max_output_tokens)
        if cap_error is not None:
            return _error(400, "VALIDATION_ERROR", cap_error)

        try:
            data, usage, openai_request_id = await create_upstream(
                clients[provider_id], provider.api, body, client_request_id
            )
            calculation = calculate_charge(
                config.pricing,
                model,
                usage,
                config.maximum_payment_atomic,
            )
            receipt = create_receipt(
                identifier=request.headers.get("idempotency-key") or client_request_id,
                network=config.network,
                asset=USDC_BY_NETWORK[config.network],
                model=model,
                pricing_version=config.pricing.version,
                calculation=calculation,
                openai_request_id=openai_request_id,
            )

            if calculation.capped:
                logger.error(
                    "calculated charge exceeded authorization",
                    extra={
                        "model": model,
                        "authorized_atomic": calculation.amount_atomic,
                        "calculated_atomic": calculation.uncapped_amount_atomic,
                        "openai_request_id": openai_request_id,
                    },
                )

            response = JSONResponse(data)
            set_settlement_overrides(response, {"amount": str(calculation.amount_atomic)})
            response.headers["x-request-id"] = client_request_id
            response.headers["x-openai-request-id"] = openai_request_id or client_request_id
            response.headers["x-provider"] = provider_id
            receipt_json = receipt.model_dump_json(by_alias=True, exclude_none=True).encode()
            response.headers["x-x402-receipt"] = (
                base64.urlsafe_b64encode(receipt_json).decode().rstrip("=")
            )
            return response
        except X402OpenAIError as exc:
            logger.exception("gateway calculation failed")
            return _error(exc.status, exc.code, str(exc))
        except APIError as exc:
            logger.error(
                "OpenAI request failed",
                extra={"request_id": getattr(exc, "request_id", None)},
            )
            return _error(502, "UPSTREAM_ERROR", "Upstream model request failed")
        except Exception:
            logger.exception("Unexpected gateway failure")
            return _error(502, "UPSTREAM_ERROR", "Upstream model request failed")

    # Starlette applies the last-added middleware first. Idempotency must run before payment
    # verification so a completed paid request can be replayed without another settlement.
    install_x402_middleware(app, config, dependencies.facilitator_auth)
    app.add_middleware(
        IdempotencyMiddleware,
        redis=redis,
        secret=config.idempotency_hmac_secret,
        ttl_seconds=config.idempotency_ttl_seconds,
    )
    return app
