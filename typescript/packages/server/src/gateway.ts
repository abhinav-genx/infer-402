import { randomUUID } from "node:crypto";

import cors from "cors";
import express from "express";
import type { NextFunction, Request, Response } from "express";
import rateLimit from "express-rate-limit";
import helmet from "helmet";
import { Redis } from "ioredis";
import OpenAI from "openai";
import pino from "pino";
import { pinoHttp } from "pino-http";
import { z } from "zod";

import { setSettlementOverrides } from "@x402/express";

import { errorMessage, USDC_BY_NETWORK, X402OpenAIError } from "@infer402/core";

import { createIdempotencyMiddleware, createX402Middleware } from "./middleware.js";
import type { FacilitatorAuthHeaders } from "./middleware.js";
import { calculateCharge, createReceipt } from "./pricing.js";
import { createUpstream, tokenCapError } from "./providers.js";
import type { GatewayConfig } from "./types.js";

const responseRequestSchema = z
  .object({
    model: z.string().min(1).max(256),
    stream: z.literal(false).optional(),
    max_output_tokens: z.number().int().positive().optional(),
  })
  .passthrough();

export interface GatewayDependencies {
  readonly redis?: Redis;
  readonly openai?: OpenAI;
  readonly clients?: Record<string, OpenAI>;
  readonly facilitatorAuth?: FacilitatorAuthHeaders;
}

function resolveProvider(
  body: Record<string, unknown>,
  request: Request,
  config: GatewayConfig,
): string {
  const explicit = body.provider;
  delete body.provider;
  if (typeof explicit === "string" && explicit) return explicit;
  const header = request.header("x-provider");
  if (header) return header;
  return config.defaultProvider;
}

export function createGateway(config: GatewayConfig, dependencies: GatewayDependencies = {}) {
  const logger = pino({
    level: config.logLevel,
    redact: {
      paths: [
        "req.headers.authorization",
        "req.headers.payment-signature",
        "req.headers.x-payment",
        "openaiApiKey",
      ],
      censor: "[REDACTED]",
    },
  });
  const redis =
    dependencies.redis ??
    new Redis(config.redisUrl, { lazyConnect: true, maxRetriesPerRequest: 2 });
  const clients: Record<string, OpenAI> = {};
  for (const [id, provider] of Object.entries(config.providerMap)) {
    clients[id] = new OpenAI({
      apiKey: provider.apiKey,
      baseURL: provider.baseUrl,
      maxRetries: 0,
      timeout: config.openaiTimeoutMs,
    });
  }
  if (dependencies.clients) Object.assign(clients, dependencies.clients);
  if (dependencies.openai) clients[config.defaultProvider] = dependencies.openai;
  const app = express();

  app.disable("x-powered-by");
  app.set("trust proxy", config.trustProxy);
  app.use(helmet());
  app.use(
    cors({
      origin:
        config.corsOrigins.length === 0
          ? false
          : (origin, callback) =>
              callback(null, !origin || config.corsOrigins.includes(origin)),
      allowedHeaders: [
        "content-type",
        "idempotency-key",
        "payment-signature",
        "x-client-request-id",
      ],
      exposedHeaders: [
        "payment-required",
        "payment-response",
        "x-idempotency-replayed",
        "x-openai-request-id",
        "x-request-id",
        "x-x402-receipt",
      ],
      methods: ["GET", "POST", "OPTIONS"],
      maxAge: 86_400,
    }),
  );
  app.use(
    pinoHttp({
      logger,
      genReqId(request, response) {
        const supplied = request.headers["x-client-request-id"];
        const id = typeof supplied === "string" ? supplied : randomUUID();
        response.setHeader("x-request-id", id);
        return id;
      },
    }),
  );
  app.use(express.json({ limit: config.bodyLimit, strict: true }));
  app.use(
    rateLimit({
      windowMs: config.rateLimitWindowMs,
      limit: config.rateLimitMax,
      standardHeaders: "draft-8",
      legacyHeaders: false,
    }),
  );

  app.get("/health/live", (_request, response) => response.status(200).json({ status: "ok" }));
  app.get("/health/ready", async (_request, response) => {
    try {
      if (redis.status === "wait") await redis.connect();
      await redis.ping();
      response.status(200).json({ status: "ready" });
    } catch (error) {
      logger.error({ err: error }, "Readiness check failed");
      response.status(503).json({ status: "not_ready" });
    }
  });

  app.use(
    createIdempotencyMiddleware({
      redis,
      secret: config.idempotencyHmacSecret,
      ttlSeconds: config.idempotencyTtlSeconds,
      logger,
    }),
  );
  app.use(createX402Middleware(config, dependencies.facilitatorAuth));

  app.post("/v1/responses", async (request: Request, response: Response, next: NextFunction) => {
    const parsed = responseRequestSchema.safeParse(request.body);
    if (!parsed.success) {
      setSettlementOverrides(response, { amount: "0" });
      next(
        new X402OpenAIError("VALIDATION_ERROR", "Invalid Responses API request", 400, {
          issues: parsed.error.issues,
        }),
      );
      return;
    }

    const body = parsed.data as Record<string, unknown>;
    const model = parsed.data.model;
    if (!config.pricing.models[model]) {
      setSettlementOverrides(response, { amount: "0" });
      next(new X402OpenAIError("MODEL_NOT_ALLOWED", `Model ${model} is not enabled`, 400));
      return;
    }

    const providerId = resolveProvider(body, request, config);
    const provider = config.providerMap[providerId];
    const client = clients[providerId];
    if (!provider || !client) {
      setSettlementOverrides(response, { amount: "0" });
      next(
        new X402OpenAIError("PROVIDER_NOT_ALLOWED", `Provider ${providerId} is not enabled`, 400),
      );
      return;
    }
    const capError = tokenCapError(provider.api, body, config.maxOutputTokens);
    if (capError) {
      setSettlementOverrides(response, { amount: "0" });
      next(new X402OpenAIError("VALIDATION_ERROR", capError, 400));
      return;
    }

    const clientRequestId = request.header("x-client-request-id") ?? randomUUID();

    try {
      const upstream = await createUpstream(client, provider.api, body, clientRequestId);
      const charge = calculateCharge(
        config.pricing,
        model,
        upstream.usage,
        config.maximumPaymentAtomic,
      );
      const openaiRequestId = upstream.requestId;
      const receipt = createReceipt({
        id: request.header("idempotency-key") ?? clientRequestId,
        network: config.network,
        asset: USDC_BY_NETWORK[config.network],
        model,
        pricingVersion: config.pricing.version,
        calculation: charge,
        ...(openaiRequestId ? { openaiRequestId } : {}),
      });

      if (charge.capped) {
        logger.error(
          {
            model,
            authorizedAtomic: charge.amountAtomic.toString(),
            calculatedAtomic: charge.uncappedAmountAtomic.toString(),
            openaiRequestId,
          },
          "Calculated cost exceeded authorization; provider absorbed the difference",
        );
      }

      setSettlementOverrides(response, { amount: charge.amountAtomic.toString() });
      response.setHeader("x-openai-request-id", openaiRequestId ?? clientRequestId);
      response.setHeader("x-provider", providerId);
      response.setHeader(
        "x-x402-receipt",
        Buffer.from(JSON.stringify(receipt)).toString("base64url"),
      );
      response.status(200).json(upstream.data);
    } catch (error) {
      setSettlementOverrides(response, { amount: "0" });
      logger.error(
        {
          err: error,
          clientRequestId,
          openaiRequestId: error instanceof OpenAI.APIError ? error.requestID : undefined,
        },
        "OpenAI request failed",
      );
      next(
        error instanceof X402OpenAIError
          ? error
          : new X402OpenAIError("UPSTREAM_ERROR", "Upstream model request failed", 502, {
              reason: errorMessage(error),
            }),
      );
    }
  });

  app.use((_request, response) => {
    response.status(404).json({ error: { code: "NOT_FOUND", message: "Route not found" } });
  });
  app.use(
    (error: unknown, _request: Request, response: Response, _next: NextFunction) => {
      const normalized =
        error instanceof X402OpenAIError
          ? error
          : new X402OpenAIError("REQUEST_FAILED", "Internal server error", 500);
      if (normalized.status >= 500) logger.error({ err: error }, normalized.message);
      response.status(normalized.status).json({
        error: {
          code: normalized.code,
          message: normalized.message,
          ...(config.nodeEnv !== "production" && normalized.details
            ? { details: normalized.details }
            : {}),
        },
      });
    },
  );

  return { app, redis, logger };
}
