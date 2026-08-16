import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Resolve workspace packages to their TypeScript source so tests run without a
// prior build (CI runs `vitest` directly; dist/ may not exist yet).
const src = (path: string) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      "@infer402/core": src("./packages/core/src/index.ts"),
      "@infer402/client": src("./packages/client/src/index.ts"),
      "@infer402/server": src("./packages/server/src/index.ts"),
    },
  },
});
