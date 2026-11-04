import { defineConfig } from "vitest/config";
import { cloudflareTest } from "@cloudflare/vitest-plugin";
import fixture from "./test/fixtures/protocol.json";

// The only upstream reachable from tests: the pinned Clef endpoint for the
// synthetic account, answering with the frozen protocol response. Anything else fails.
const pinned =
  "https://api.cloudflare.com/client/v4/accounts/00000000000000000000000000000000/ai/run/@cf/cloudflare/clef-flash";
async function offlineUpstream(request: Request): Promise<Response> {
  if (
    request.method === "POST" &&
    request.url === pinned &&
    request.headers.get("Authorization") ===
      "Bearer offline-synthetic-workers-ai-token" &&
    (JSON.parse(await request.text()) as { model?: unknown }).model ===
      "clef-flash"
  )
    return Response.json(fixture.response, {
      headers: { "cf-ai-neurons": "1" },
    });
  return new Response("offline network guard", { status: 599 });
}

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
        outboundService: offlineUpstream,
      },
    }),
  ],
  test: { include: ["test/**/*.test.ts"] },
});
