import type OpenAI from "openai";
import type { ChatCompletionCreateParamsNonStreaming } from "openai/resources/chat/completions";
import type { ResponseCreateParamsNonStreaming } from "openai/resources/responses/responses";
import { z } from "zod";

import { X402OpenAIError } from "@infer402/core";
import type { TokenUsage } from "@infer402/core";

export type ProviderApi = "responses" | "chat";

export interface ProviderSpec {
  readonly baseUrl: string;
  readonly api: ProviderApi;
  readonly keyEnv: string;
}

// Major providers in the space. OpenAI is billed through its Responses API; every
// other provider here speaks the OpenAI-compatible Chat Completions API. Base URLs
// can be overridden per provider with <ID>_BASE_URL (e.g. GROQ_BASE_URL).
export const KNOWN_PROVIDERS = {
  openai: { baseUrl: "https://api.openai.com/v1", api: "responses", keyEnv: "OPENAI_API_KEY" },
  openrouter: { baseUrl: "https://openrouter.ai/api/v1", api: "chat", keyEnv: "OPENROUTER_API_KEY" },
  gemini: {
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/",
    api: "chat",
    keyEnv: "GEMINI_API_KEY",
  },
  groq: { baseUrl: "https://api.groq.com/openai/v1", api: "chat", keyEnv: "GROQ_API_KEY" },
  deepseek: { baseUrl: "https://api.deepseek.com/v1", api: "chat", keyEnv: "DEEPSEEK_API_KEY" },
  xai: { baseUrl: "https://api.x.ai/v1", api: "chat", keyEnv: "XAI_API_KEY" },
  mistral: { baseUrl: "https://api.mistral.ai/v1", api: "chat", keyEnv: "MISTRAL_API_KEY" },
  together: { baseUrl: "https://api.together.xyz/v1", api: "chat", keyEnv: "TOGETHER_API_KEY" },
  fireworks: {
    baseUrl: "https://api.fireworks.ai/inference/v1",
    api: "chat",
    keyEnv: "FIREWORKS_API_KEY",
  },
  perplexity: { baseUrl: "https://api.perplexity.ai", api: "chat", keyEnv: "PERPLEXITY_API_KEY" },
  cerebras: { baseUrl: "https://api.cerebras.ai/v1", api: "chat", keyEnv: "CEREBRAS_API_KEY" },
} satisfies Record<string, ProviderSpec>;

export const providerConfigSchema = z.object({
  id: z.string().min(1),
  baseUrl: z.string().url(),
  api: z.enum(["responses", "chat"]),
  apiKey: z.string().min(1),
});

export type ProviderConfig = z.infer<typeof providerConfigSchema>;

export function providersFromEnv(env: NodeJS.ProcessEnv): ProviderConfig[] {
  const providers: ProviderConfig[] = [];
  for (const [id, spec] of Object.entries(KNOWN_PROVIDERS)) {
    const apiKey = env[spec.keyEnv];
    if (!apiKey) continue;
    const baseUrl = env[`${id.toUpperCase()}_BASE_URL`] ?? spec.baseUrl;
    providers.push({ id, baseUrl, api: spec.api, apiKey });
  }

  const raw = (env.PROVIDERS_JSON ?? "").trim();
  if (raw) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new X402OpenAIError("CONFIGURATION_ERROR", "PROVIDERS_JSON must be valid JSON");
    }
    if (!Array.isArray(parsed)) {
      throw new X402OpenAIError("CONFIGURATION_ERROR", "PROVIDERS_JSON must be a JSON array");
    }
    // Accept snake_case (shared with the Python gateway) and camelCase keys.
    const normalized = parsed.map((entry) => {
      const item = entry as Record<string, unknown>;
      return {
        id: item.id,
        baseUrl: item.baseUrl ?? item.base_url,
        api: item.api,
        apiKey: item.apiKey ?? item.api_key,
      };
    });
    providers.push(...z.array(providerConfigSchema).parse(normalized));
  }

  return providers;
}

export function tokenCapError(
  api: ProviderApi,
  body: Record<string, unknown>,
  cap: number,
): string | undefined {
  const field = api === "responses" ? "max_output_tokens" : "max_tokens";
  const value =
    api === "responses" ? body.max_output_tokens : (body.max_tokens ?? body.max_completion_tokens);
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    return `${field} must be a positive integer`;
  }
  if (value > cap) return `${field} cannot exceed ${cap}`;
  return undefined;
}

function toCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function normalizeUsage(api: ProviderApi, usage: unknown): TokenUsage {
  const raw = (usage ?? {}) as Record<string, any>;
  if (api === "responses") {
    return {
      input_tokens: toCount(raw.input_tokens),
      output_tokens: toCount(raw.output_tokens),
      input_tokens_details: { cached_tokens: toCount(raw.input_tokens_details?.cached_tokens) },
    };
  }
  return {
    input_tokens: toCount(raw.prompt_tokens),
    output_tokens: toCount(raw.completion_tokens),
    input_tokens_details: { cached_tokens: toCount(raw.prompt_tokens_details?.cached_tokens) },
  };
}

export interface UpstreamResult {
  readonly data: unknown;
  readonly usage: TokenUsage;
  readonly requestId: string | undefined;
}

export async function createUpstream(
  client: OpenAI,
  api: ProviderApi,
  body: Record<string, unknown>,
  requestId: string,
): Promise<UpstreamResult> {
  const options = { headers: { "X-Client-Request-Id": requestId } };

  if (api === "responses") {
    const result = await client.responses
      .create(body as unknown as ResponseCreateParamsNonStreaming, options)
      .withResponse();
    if (!result.data.usage) {
      throw new X402OpenAIError("UPSTREAM_ERROR", "Upstream response did not contain usage", 502);
    }
    return {
      data: result.data,
      usage: normalizeUsage("responses", result.data.usage),
      requestId: result.response.headers.get("x-request-id") ?? undefined,
    };
  }

  const result = await client.chat.completions
    .create(body as unknown as ChatCompletionCreateParamsNonStreaming, options)
    .withResponse();
  if (!result.data.usage) {
    throw new X402OpenAIError("UPSTREAM_ERROR", "Upstream response did not contain usage", 502);
  }
  return {
    data: result.data,
    usage: normalizeUsage("chat", result.data.usage),
    requestId: result.response.headers.get("x-request-id") ?? undefined,
  };
}
