// Offline deploy-configuration check: bundles both scopes with `wrangler deploy --dry-run`,
// which uploads nothing, and asserts the committed config stays disabled and log-free.
// Credentials are removed from the child environment so this can never authenticate.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const env = { ...process.env, WRANGLER_SEND_METRICS: "false" };
delete env.CLOUDFLARE_API_TOKEN;
delete env.CLOUDFLARE_ACCOUNT_ID;

const config = JSON.parse(
  readFileSync("wrangler.jsonc", "utf8")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/,(\s*[}\]])/g, "$1"),
);
const scopes = { preview: "preview", production: "production" };
for (const [name, scope] of Object.entries(scopes)) {
  const item = config.env[name];
  const problems = [
    item.vars.GATEWAY_ENABLED !== "0" && "GATEWAY_ENABLED must stay 0",
    item.vars.GATEWAY_SCOPE !== scope && "GATEWAY_SCOPE must match the env",
    item.observability?.enabled !== false && "observability must stay off",
    item.logpush !== false && "logpush must stay off",
    item.preview_urls !== false && "preview URLs must stay off",
    "account_id" in item && "account_id must come from the environment",
    item.durable_objects.bindings.length !== 1 && "exactly one coordinator",
  ].filter(Boolean);
  if (problems.length) throw new Error(`${name}: ${problems.join("; ")}`);
  execFileSync(
    "npx",
    [
      "wrangler",
      "deploy",
      "--dry-run",
      "--env",
      name,
      "--outdir",
      `.wrangler/dry-run/${name}`,
    ],
    { env, stdio: ["ignore", "ignore", "inherit"] },
  );
}
if (config.vars.GATEWAY_ENABLED !== "0" || config.workers_dev !== false)
  throw new Error("top-level test configuration must stay disabled");
console.log(
  "deploy dry runs passed for preview and production; nothing uploaded",
);
