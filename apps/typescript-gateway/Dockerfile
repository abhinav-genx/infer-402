FROM node:22-bookworm-slim AS build

WORKDIR /app
COPY typescript/package.json typescript/package-lock.json typescript/tsconfig.base.json ./
COPY typescript/packages/core/package.json packages/core/package.json
COPY typescript/packages/client/package.json packages/client/package.json
COPY typescript/packages/server/package.json packages/server/package.json
COPY typescript/apps/gateway/package.json apps/gateway/package.json
RUN npm ci

COPY typescript/packages ./packages
COPY typescript/apps/gateway ./apps/gateway
RUN npm run build && npm prune --omit=dev

FROM node:22-bookworm-slim AS runtime

ENV NODE_ENV=production
WORKDIR /app
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app app

COPY --from=build --chown=app:app /app/package.json /app/package-lock.json ./
COPY --from=build --chown=app:app /app/node_modules ./node_modules
COPY --from=build --chown=app:app /app/packages/core/package.json ./packages/core/package.json
COPY --from=build --chown=app:app /app/packages/core/dist ./packages/core/dist
COPY --from=build --chown=app:app /app/packages/server/package.json ./packages/server/package.json
COPY --from=build --chown=app:app /app/packages/server/dist ./packages/server/dist
COPY --from=build --chown=app:app /app/apps/gateway/package.json ./apps/gateway/package.json
COPY --from=build --chown=app:app /app/apps/gateway/dist ./apps/gateway/dist

USER app
EXPOSE 4021
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:4021/health/live').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

CMD ["node", "apps/gateway/dist/index.js"]
