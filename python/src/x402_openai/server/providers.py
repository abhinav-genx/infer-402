from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict

from x402_openai.core import TokenUsage, X402OpenAIError

ProviderApi: TypeAlias = Literal["responses", "chat"]


class ProviderSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str
    api: ProviderApi
    key_env: str


# Major providers in the space. OpenAI is billed through its Responses API; every
# other provider here speaks the OpenAI-compatible Chat Completions API. Base URLs
# can be overridden per provider with <ID>_BASE_URL (e.g. GROQ_BASE_URL).
KNOWN_PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        base_url="https://api.openai.com/v1", api="responses", key_env="OPENAI_API_KEY"
    ),
    "openrouter": ProviderSpec(
        base_url="https://openrouter.ai/api/v1", api="chat", key_env="OPENROUTER_API_KEY"
    ),
    "gemini": ProviderSpec(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api="chat",
        key_env="GEMINI_API_KEY",
    ),
    "groq": ProviderSpec(
        base_url="https://api.groq.com/openai/v1", api="chat", key_env="GROQ_API_KEY"
    ),
    "deepseek": ProviderSpec(
        base_url="https://api.deepseek.com/v1", api="chat", key_env="DEEPSEEK_API_KEY"
    ),
    "xai": ProviderSpec(base_url="https://api.x.ai/v1", api="chat", key_env="XAI_API_KEY"),
    "mistral": ProviderSpec(
        base_url="https://api.mistral.ai/v1", api="chat", key_env="MISTRAL_API_KEY"
    ),
    "together": ProviderSpec(
        base_url="https://api.together.xyz/v1", api="chat", key_env="TOGETHER_API_KEY"
    ),
    "fireworks": ProviderSpec(
        base_url="https://api.fireworks.ai/inference/v1", api="chat", key_env="FIREWORKS_API_KEY"
    ),
    "perplexity": ProviderSpec(
        base_url="https://api.perplexity.ai", api="chat", key_env="PERPLEXITY_API_KEY"
    ),
    "cerebras": ProviderSpec(
        base_url="https://api.cerebras.ai/v1", api="chat", key_env="CEREBRAS_API_KEY"
    ),
}


class ProviderConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    base_url: str
    api: ProviderApi
    api_key: str


def providers_from_env(env: Mapping[str, str]) -> tuple[ProviderConfig, ...]:
    """Build the enabled provider set from environment variables.

    A known provider is enabled when its API key variable is present. Custom or
    self-hosted OpenAI-compatible providers can be added through PROVIDERS_JSON,
    a JSON array of {"id", "base_url", "api", "api_key"} objects.
    """
    providers: list[ProviderConfig] = []
    for provider_id, spec in KNOWN_PROVIDERS.items():
        api_key = env.get(spec.key_env)
        if not api_key:
            continue
        base_url = env.get(f"{provider_id.upper()}_BASE_URL", spec.base_url)
        providers.append(
            ProviderConfig(id=provider_id, base_url=base_url, api=spec.api, api_key=api_key)
        )

    raw = env.get("PROVIDERS_JSON", "").strip()
    if raw:
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("PROVIDERS_JSON must be valid JSON") from exc
        if not isinstance(entries, list):
            raise ValueError("PROVIDERS_JSON must be a JSON array")
        for entry in entries:
            if isinstance(entry, dict):
                # Accept snake_case (shared format) and camelCase (TS style) keys.
                entry = {
                    "id": entry.get("id"),
                    "base_url": entry.get("base_url", entry.get("baseUrl")),
                    "api": entry.get("api"),
                    "api_key": entry.get("api_key", entry.get("apiKey")),
                }
            providers.append(ProviderConfig.model_validate(entry))

    return tuple(providers)


def token_cap_error(api: ProviderApi, body: dict[str, Any], cap: int) -> str | None:
    """Validate the per-call output token limit for the provider's API flavor."""
    if api == "responses":
        field, value = "max_output_tokens", body.get("max_output_tokens")
    else:
        field = "max_tokens"
        value = body.get("max_tokens")
        if value is None:
            value = body.get("max_completion_tokens")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return f"{field} must be a positive integer"
    if value > cap:
        return f"{field} cannot exceed {cap}"
    return None


def extract_usage(api: ProviderApi, usage: dict[str, Any]) -> TokenUsage:
    """Normalize Responses and Chat Completions usage into billable token counts."""
    if api == "responses":
        details = usage.get("input_tokens_details") or {}
        return TokenUsage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached_input_tokens=details.get("cached_tokens", 0) or 0,
        )
    details = usage.get("prompt_tokens_details") or {}
    return TokenUsage(
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cached_input_tokens=details.get("cached_tokens", 0) or 0,
    )


async def create_upstream(
    client: Any,
    api: ProviderApi,
    body: dict[str, Any],
    request_id: str,
) -> tuple[dict[str, Any], TokenUsage, str | None]:
    """Call the upstream provider and return (response body, usage, request id)."""
    headers = {"X-Client-Request-Id": request_id}
    if api == "responses":
        result = await client.responses.create(**body, extra_headers=headers)
    else:
        result = await client.chat.completions.create(**body, extra_headers=headers)

    usage_object = getattr(result, "usage", None)
    if usage_object is None:
        raise X402OpenAIError("UPSTREAM_ERROR", "Upstream response did not contain usage", 502)

    usage = extract_usage(api, usage_object.model_dump())
    data = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    return data, usage, getattr(result, "_request_id", None)
