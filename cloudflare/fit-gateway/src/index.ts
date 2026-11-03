import { DurableObject } from "cloudflare:workers";
import { Failure } from "./config";
import contract from "../../../src/makan/evals/sets/clef/contract.json";
import { Coordinator, unavailable, type Reply } from "./coordinator";
import {
  boundedText,
  isHmac,
  object,
  strictJson,
  validateRequest,
} from "./contract";

export interface Env {
  COORDINATOR: DurableObjectNamespace<FitCoordinator>;
  GATEWAY_SCOPE: "preview" | "production";
  GATEWAY_ENABLED: string;
  MAKAN_FIT_GATEWAY_TOKEN: string;
  CLOUDFLARE_ACCOUNT_ID: string;
  CLOUDFLARE_API_TOKEN: string;
}
function reply(value: Reply | object, status = 200): Response {
  const retry = "retry_after" in value ? value.retry_after : undefined;
  return Response.json(value, {
    status,
    headers: {
      "Cache-Control": "private, no-store",
      ...(typeof retry === "number" ? { "Retry-After": String(retry) } : {}),
    },
  });
}
async function authorized(request: Request, env: Env): Promise<boolean> {
  if (
    !env.MAKAN_FIT_GATEWAY_TOKEN ||
    env.MAKAN_FIT_GATEWAY_TOKEN.length < 32 ||
    env.MAKAN_FIT_GATEWAY_TOKEN.length > 256
  )
    return false;
  const expected = new TextEncoder().encode(
    `Bearer ${env.MAKAN_FIT_GATEWAY_TOKEN}`,
  );
  const provided = new TextEncoder().encode(
    request.headers.get("Authorization") ?? "",
  );
  if (provided.length !== expected.length) return false;
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", expected),
    crypto.subtle.digest("SHA-256", provided),
  ]);
  const left = new Uint8Array(a),
    right = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left[i] ^ right[i];
  return diff === 0;
}
function configured(env: Env): boolean {
  return (
    env.GATEWAY_ENABLED === "1" &&
    ["preview", "production"].includes(env.GATEWAY_SCOPE) &&
    /^[a-fA-F0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID ?? "") &&
    /^[!-~]+$/.test(env.CLOUDFLARE_API_TOKEN ?? "")
  );
}

export class FitCoordinator extends DurableObject<Env> {
  readonly coordinator: Coordinator;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    // Fixed account endpoint and model; no caller-supplied URL, selector or token.
    this.coordinator = new Coordinator(ctx.storage, {
      url: `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/ai/run/${contract.model}`,
      token: env.CLOUDFLARE_API_TOKEN,
    });
  }
  async alarm(): Promise<void> {
    await this.coordinator.cleanup();
  }
  async fetch(request: Request): Promise<Response> {
    if (!(await authorized(request, this.env)))
      return reply(unavailable("configuration"), 401);
    if (!configured(this.env)) return reply(unavailable("configuration"), 503);
    try {
      const path = new URL(request.url).pathname;
      if (request.method !== "POST")
        return reply(unavailable("invalid_input"), 405);
      const raw = strictJson(await boundedText(request, 12_288));
      if (path === "/v1/score")
        return reply(
          await this.coordinator.score(
            validateRequest(raw, this.env.GATEWAY_SCOPE, Date.now()),
          ),
        );
      if (path === "/v1/purge" || path === "/v1/invalidate") {
        const data = object(
          raw,
          path === "/v1/purge" ? ["scope"] : ["scope", "keys"],
        );
        if (
          !isHmac(data.scope) ||
          (path === "/v1/invalidate" &&
            (!Array.isArray(data.keys) ||
              data.keys.length > 100 ||
              !data.keys.every(isHmac)))
        )
          return reply(unavailable("invalid_input"), 400);
        this.coordinator.invalidate(
          data.scope,
          data.keys as string[] | undefined,
        );
        return reply({ v: 1, status: "ok" });
      }
      if (path === "/v1/metrics") {
        object(raw, []);
        return reply(this.coordinator.metrics());
      }
      return reply(unavailable("invalid_input"), 404);
    } catch (error) {
      return reply(
        unavailable(
          error instanceof Failure ? error.reason : "service_unavailable",
        ),
        error instanceof Failure ? 400 : 503,
      );
    }
  }
}
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (!(await authorized(request, env)))
      return reply(unavailable("configuration"), 401);
    if (!configured(env)) return reply(unavailable("configuration"), 503);
    try {
      // Exactly one object per account. Never partition quota by diner/session/server.
      return await env.COORDINATOR.get(
        env.COORDINATOR.idFromName("scorer-account-v1"),
      ).fetch(request);
    } catch {
      return reply(unavailable("service_unavailable"), 503);
    }
  },
} satisfies ExportedHandler<Env>;
