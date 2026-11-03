import { defineConfig } from "vitest/config";
import { cloudflareTest } from "@cloudflare/vitest-plugin";
export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc" },
      miniflare: {
        bindings: {
          GATEWAY_ENABLED: "1",
          GATEWAY_SCOPE: "preview",
          MAKAN_FIT_GATEWAY_TOKEN: "offline-synthetic-server-credential-only",
          CLOUDFLARE_ACCOUNT_ID: "00000000000000000000000000000000",
          CLOUDFLARE_API_TOKEN: "offline-synthetic-workers-ai-token",
        },
      },
    }),
  ],
  test: { include: ["test/**/*.test.ts"] },
});
