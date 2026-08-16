import { createServer } from "node:http";

import { createGateway, loadGatewayConfig } from "@infer402/server";

const config = loadGatewayConfig();
const { app, redis, logger } = createGateway(config);
const server = createServer(app);

server.listen(config.port, "0.0.0.0", () => {
  logger.info(
    { port: config.port, network: config.network, pricingVersion: config.pricing.version },
    "x402 OpenAI gateway listening",
  );
});

let shuttingDown = false;

function shutdown(signal: string): void {
  if (shuttingDown) return;
  shuttingDown = true;
  logger.info({ signal }, "Graceful shutdown started");

  const forcedExit = setTimeout(() => {
    logger.fatal("Graceful shutdown timed out");
    process.exit(1);
  }, 15_000).unref();

  server.closeIdleConnections();
  server.close((error) => {
    clearTimeout(forcedExit);
    void redis
      .quit()
      .catch((redisError: unknown) => logger.error({ err: redisError }, "Redis shutdown failed"))
      .finally(() => {
        if (error) {
          logger.error({ err: error }, "HTTP shutdown failed");
          process.exitCode = 1;
        }
      });
  });
}

process.once("SIGINT", () => shutdown("SIGINT"));
process.once("SIGTERM", () => shutdown("SIGTERM"));
process.on("unhandledRejection", (error) => {
  logger.fatal({ err: error }, "Unhandled promise rejection");
  shutdown("unhandledRejection");
});
process.on("uncaughtException", (error) => {
  logger.fatal({ err: error }, "Uncaught exception");
  shutdown("uncaughtException");
});
