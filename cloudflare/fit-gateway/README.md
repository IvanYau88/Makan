# Clef fit gateway

The slice 3 coordinator is connected through the default-off slice 4 web strategy.
The gateway remains disabled by default, with no public routes configured.
Nothing here deploys, creates an account, performs inference, upgrades billing, or enables public fit scores.

From the repository root, with Node 24:

```sh
npm --prefix cloudflare/fit-gateway ci
npm --prefix cloudflare/fit-gateway run lint
npm --prefix cloudflare/fit-gateway test
```

Lint checks strict TypeScript, formatting, and the frozen contract hashes.
Tests use the local Workers runtime and real SQLite-backed Durable Object storage with synthetic upstream responses.
A global fetch guard rejects unexpected network calls.
The shared protocol fixture is synthetic and does not qualify the dual-question model contract.
The frozen questions and schemas are imported directly from `src/makan/evals/sets/clef/`, without copying or editing the archive.
The Cloudflare Vitest plugin is the maintained successor to the deprecated Workers test pool.
Its Vitest peer range and the package lock select compatible versions.

## Runtime configuration

All values below are placeholders.
Do not put real credentials in this repository, test fixtures, shell output, reports, prompts, traces, logs, browser configuration, or `VITE_*` variables.
The checked-in `wrangler.jsonc` top level is the disabled test configuration: gateway off, no workers.dev, no preview URLs, no automatic observability.
Its `preview` and `production` environments are the two deploy scopes, each with `GATEWAY_ENABLED=0`, its own Worker name and coordinator, workers.dev on, preview URLs, observability and Logpush off, and no account id.
`npm run check:deploy` bundles both scopes with `wrangler deploy --dry-run` and checks those settings, with credentials stripped from its environment, so it can never authenticate or upload; CI runs it.
There is no deploy script or CI deploy step.

| Setting                   | Location and placeholder                                                                              |
| ------------------------- | ----------------------------------------------------------------------------------------------------- |
| `GATEWAY_ENABLED`         | Gateway variable: `0` until separately authorized qualification and rollout                           |
| `GATEWAY_SCOPE`           | Gateway variable: `preview` or `production`                                                           |
| `MAKAN_FIT_GATEWAY_TOKEN` | Gateway secret and matching Makan server secret: `<independent random credential, 32-256 characters>` |
| `CLOUDFLARE_ACCOUNT_ID`   | Gateway secret only: `<dedicated free account id, 32 hex characters>`                                 |
| `CLOUDFLARE_API_TOKEN`    | Gateway secret only: `<account-restricted Workers AI token>`                                          |
| `MAKAN_FIT_GATEWAY_URL`   | Makan server setting only: `<HTTPS gateway origin>`                                                   |
| `MAKAN_FIT_HMAC_SECRET`   | Makan server secret only: `<independent random secret, at least 32 bytes>`                            |
| `MAKAN_FIT_GATEWAY_SCOPE` | Makan server setting: `preview` or `production`, matching the gateway                                 |
| `COORDINATOR`             | Worker Durable Object binding to the SQLite-backed `FitCoordinator` class                             |

The Makan web strategy reads these settings only for explicit online fit selection and routes all product inference through this gateway.
`MAKAN_FIT_ONLINE_ENABLED=0` keeps that route off until separately authorized qualification.
The pure `makan.providers.scoring.gateway.GatewayKeys` helper constructs the server-side protocol envelope, without any I/O.
The existing explicit native adapter remains an offline/testing building block; it must not be used as a production bypass around this coordinator.
A production Makan server holds only its gateway credential and HMAC secret, never the Cloudflare account id or Workers AI token.
Use distinct credentials, HMAC secrets, scopes, and dedicated free inference accounts for preview and production.
All servers sharing an inference account must use the same deployed gateway and its one `scorer-account-v1` Durable Object.
Do not create per-server or per-owner coordinators, deploy two gateways for one account, use gradual namespace replacement, or use this account for other inference outside the same quota pool.
Rotating secrets requires updating their consumers together; it is not a reason to erase quota storage.

Use a narrowly scoped Workers AI runtime token restricted to the one account and the minimum Workers AI permissions required by the REST API.
Billing, DNS, user administration, and deployment permissions do not belong on it.
Keep a separate deployment credential outside runtime.
Before any rollout, independently verify Workers Free without a billing upgrade, SQLite Durable Object availability/capacity, token permissions, and the account's applicable data terms.
If those facts cannot be established, keep fit disabled.
There is no process-local quota substitute and no Makan database migration.

## Deploy runbook

Enablement stopped at the slice 5 qualification gate on 2026-11-08, so this runbook is prepared but unused and the gateway is not deployed.
The control canary moved without a visible model identity change (Q3), reversing question order flipped a case across the acceptance threshold (Q6), production-shaped states rated fewer places than the local baseline (Q7), and draft-label accepted error was 21.4% (Q8); human diner labels (Q9) do not exist yet.
Online fit stays off (`MAKAN_FIT_ONLINE_ENABLED=0`).
Revisit only with richer soft inputs such as slice 6 owner evidence, captain-provided human diner labels, a new frozen canary and a passing fresh qualification; see `docs/DESIGN.md` and `tests/fixtures/clef/live/README.md`.
Any later deploy still needs its own explicit go-ahead.
Run every command from `cloudflare/fit-gateway`, typing secrets only into `wrangler secret put` prompts, never into arguments, files or shell history.

Preconditions, all recorded before deploying:

1. The inference account is confirmed on Workers Free with no payment method, in the dashboard (Billing, Subscriptions and Payment methods) or with a separate Billing Read token; the Workers AI token cannot show this.
2. The qualification gates in `tests/fixtures/clef/live/README.md` pass, or the captain explicitly accepts the failed ones.
3. The account's Workers AI and Durable Objects terms have been read for the planned data transfer.
4. One inference account means one deployed scope: deploy only `preview` or only `production` to it, never both, so two coordinators never reserve one allowance.
5. `npm ci`, `npm run lint`, `npm test` and `npm run check:deploy` pass.

Credentials the captain creates, each restricted to the one account:

| Credential         | Permissions                                                                                      | Used by                                                                                                |
| ------------------ | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| Deploy token       | Account: Workers Scripts Edit (which covers Durable Object migrations) and Account Settings Read | `wrangler deploy` and `wrangler secret put` only, from a terminal; never stored in the Worker or Makan |
| Runtime token      | Account: Workers AI Read and Workers AI Edit, nothing else                                       | Gateway secret `CLOUDFLARE_API_TOKEN`                                                                  |
| Gateway credential | A random value, for example `openssl rand -hex 32`                                               | Gateway secret and Makan server secret `MAKAN_FIT_GATEWAY_TOKEN`                                       |
| HMAC secret        | A separate random value of at least 32 bytes                                                     | Makan server secret `MAKAN_FIT_HMAC_SECRET` only                                                       |

The workers.dev route needs the account's workers.dev subdomain; register it in the dashboard first if it does not exist.

Deploy, disabled first (`<scope>` is `preview` or `production`):

```sh
export CLOUDFLARE_ACCOUNT_ID=<dedicated free account id>   # deploy-time only
export CLOUDFLARE_API_TOKEN=<deploy token>                  # deploy-time only
npx wrangler deploy --env <scope>                           # GATEWAY_ENABLED=0, applies migration v1
npx wrangler secret put MAKAN_FIT_GATEWAY_TOKEN --env <scope>
npx wrangler secret put CLOUDFLARE_ACCOUNT_ID --env <scope>
npx wrangler secret put CLOUDFLARE_API_TOKEN --env <scope>  # the runtime token, not the deploy token
unset CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID
```

Check the disabled Worker: an authenticated `POST /v1/metrics` with body `{}` returns 503 `configuration`, and an unauthenticated one returns 401.
Enabling is a separate step with its own go-ahead: `npx wrangler deploy --env <scope> --var GATEWAY_ENABLED:1`.
Then an authenticated `/v1/metrics` returns zeroed aggregates, and the Makan server for that scope receives `MAKAN_FIT_GATEWAY_URL` (the HTTPS workers.dev origin), `MAKAN_FIT_GATEWAY_TOKEN`, `MAKAN_FIT_HMAC_SECRET` and `MAKAN_FIT_GATEWAY_SCOPE`.
`MAKAN_FIT_ONLINE_ENABLED` stays `0` until its own go-ahead; when it is turned on, the default search path is unchanged, because each search still needs explicit guest consent.

Rollback, fastest first:

1. Set `MAKAN_FIT_ONLINE_ENABLED=0` on the Makan server and redeploy it: every plate returns to the local baseline, with no gateway call.
2. Redeploy the gateway without `--var GATEWAY_ENABLED:1`: every call returns `configuration` while the coordinator keeps its reservations, counters and tombstones.
3. `npx wrangler rollback --env <scope>` returns to the previous Worker version, with Durable Object storage untouched.
4. Revoke the runtime token in the dashboard to stop all inference at the provider.

Never delete the Worker, its Durable Object namespace or the coordinator to clear a breaker or quota state: that would erase accounting and could replay uncertain calls.

## Authenticated protocol v1

All operations require `Authorization: Bearer <MAKAN_FIT_GATEWAY_TOKEN>` and `POST` with JSON.
The gateway exposes no CORS permission and no browser interface.
Responses always have `Cache-Control: private, no-store`.
Coordinator/storage/free-limit failures return sanitized `service_unavailable` and never make an uncoordinated call.
No arbitrary endpoint, model, prompt, state schema, question set, or response body is forwarded.

`POST /v1/score` takes:

```json
{
  "v": 1,
  "namespace": "preview",
  "scope": "<64 lowercase hex HMAC>",
  "key": "<64 lowercase hex HMAC>",
  "manifest": "<frozen manifest content_sha256>",
  "expires_at": 1798027201000,
  "body": {
    "model": "clef-flash",
    "state": "<canonical JSON of exactly one allowlisted profile and candidate>",
    "questions": {
      "taste_fit": "<exact frozen question>",
      "fit_score": "<exact frozen question>"
    }
  }
}
```

The question placeholders above stand for objects with precisely the frozen `type`, `instructions`, and `criteria`, not strings in actual requests.
The complete native body is limited to 8 KiB; the metadata envelope is limited to 12 KiB.
The response body is read with a 16 KiB bound and validated before projection.
Duplicate JSON keys, extra request fields, unknown hard/identity fields, unsupported models, and mismatched manifests fail before admission.
The gateway validates both questions, probability distributions, ordinal legend, expectation, native confidence, and usage using the same rules as slice 2.
Native extra response fields are discarded.
Accepted semantic unknowns remain successful evidence and may be cached; this infrastructure does not apply display/acceptance policy or fabricate numbers.

The authenticated server computes the keys with `GatewayKeys`, using a secret unavailable to browsers or the gateway.
Domain-separated HMAC-SHA256 covers the preview/production namespace, opaque owner or unguessable guest tab nonce, canonical soft profile and fact body, profile/evidence revisions, provider-qualified candidate identity, stable fact expiry, frozen manifest, and gateway policy version.
Changing request wording, search center, or unrelated candidate ordering does not enter this envelope.
Never accept a browser-provided HMAC key/scope as authority; derive the scope from trusted server session state.
The gateway trusts the authenticated server's HMACs and does not have the secret with which to recompute them.
The server must reauthorize consent, ownership, current revisions, and eligibility before constructing or reusing an envelope.
The server-issued tab scope and product checks live in `makan.web.fit_sessions` and `makan.profile_fit`.

Replies contain `v=1`, `reasons_version=2`, `status`, `reason`, `cached`, and `attempt_id`.
`cached` is true only for an unexpired validated result read from durable storage; fresh successful results and infrastructure states return false.
A successful `ok` response adds `data`, a bounded native success projection that the slice-2 `ClefScorer` parser accepts, and `neurons`, which may be null.
`pending` means the original durable attempt is in flight, and `unknown` means it cannot safely be replayed.
Neither contains evidence.
`error` means admission refused the call or a known infrastructure failure occurred.
`retry_after`, when present, is also emitted as the `Retry-After` response header in seconds.
The persisted attempt id is returned for every repeat of that uncertain case, including a reconnect or lost reply; callers do not supply attempt ids or trigger retries by inventing them.

Reason vocabulary v2 adds only `quota_exhausted` to slice 2's `ClefReason` values, mapped to Python `ErrorKind.BUDGET`.
`pending` and `unknown` are coordination states with existing sanitized infrastructure reasons, not semantic taste answers.
The future gateway transport must unwrap `data` on success, map errors to `ClefError`, and preserve these coordination states rather than repeat inference.
This slice intentionally does not wire that transport into factories or the web app.

| Operation        | Body and behavior                                                                             |
| ---------------- | --------------------------------------------------------------------------------------------- |
| `/v1/invalidate` | `{"scope":"<owner HMAC>","keys":["<case HMAC>"]}`; at most 100 keys, restricted to that owner |
| `/v1/purge`      | `{"scope":"<owner HMAC>"}`; invalidates every case for an owner or tab                        |
| `/v1/metrics`    | `{}`; authenticated aggregate counts only                                                     |

Profile/history edits, forgotten facts, and consent withdrawal require scoped invalidation/purge, and new revisions produce new case keys.
Account deletion purges the owner scope; clearing or changing a guest identity purges the old scope and rotates the tab nonce.
Model, source, prompt, or policy changes enter a new cache namespace through the body, manifest, revisions, or version.
Purge removes all personalized result payloads immediately and suppresses late in-flight writes.
It deliberately retains only HMAC non-replay tombstones and accounting through the reservation day's UTC reset, so deletion cannot cause an uncertain billed call to run again.
Quota counters are never refunded by purge.
Alarms clear expired payloads and prune old metrics even without further requests; platform alarm delays may delay physical deletion, but expired results are never served.

## Accounting and recovery

`src/config.ts` versions the limits and pricing assumption.
Admission checks a valid same-version cache result first, then the durable unique case key, breakers, rolling-minute count, in-flight count, and daily reservation capacity.
One synchronous SQLite transaction reserves the random attempt id, rolling-minute slot, in-flight state, original UTC day, and 85 Neurons.
The transaction ends and storage flushes before network dispatch.
There are at most 240 attempts per rolling minute, four calls in flight, and 9,000 reserved/settled Neurons per UTC day.
The dedicated free account's own enforcement remains independent.

The pricing assumption is 3,455 Neurons per million input tokens over the full 24,576-token window, rounded up to an 85-Neuron reservation.
Measured `cf-ai-neurons` settles in integer milli-Neurons, rounding upward plus 0.01 Neuron slack.
Missing or invalid usage keeps the entire reservation, and timeouts/network uncertainty never refund it even if a partial header arrived.
Measured usage above the reserved ceiling opens a persistent configuration breaker.
Settlement updates the attempt's original day and never credits the new day at midnight.
If settlement fails, the committed pending row and original reservation remain, regardless of upstream completion.
A restarted coordinator treats stale pending rows as unknown tombstones until the original day's next UTC reset, at the cost of occasional unavailable evidence.
There is no claim of upstream exactly-once support.

The total upstream deadline is three seconds including dispatch wait and bounded body reading, using an actual AbortController signal.
There are no inline retries, redirects, alternate providers/models, background scoring, or frozen/local score fallbacks.
A known failed call can become eligible again only after its rolling-minute slot expires and its shared cooldown closes.
A failure with unknown billing instead retains a non-replay tombstone until reset.
Validated results expire at the earlier of fact expiry or 24 hours.

429s share a durable breaker honoring numeric or HTTP-date `Retry-After`, with a five-second minimum cooldown.
Only documented Workers AI error code `3036` identifies daily exhaustion and opens the breaker until the reservation day's UTC reset.
An unrecognized 429, including other numeric codes, means `rate_limited`.
Timeouts, network failures, and 5xx use sanitized `timeout` or `service_unavailable` with a shared cooldown and conservative accounting.
A refused or malformed response settles only its own case and tombstone, never the shared cooldown.
Auth/configuration/model mismatches open a persistent breaker until a reviewed gateway policy/configuration version changes.
A version change clears incompatible breakers but preserves every reservation and daily counter; never delete or replace the Durable Object namespace to clear a breaker.
Valid cache hits still work while admission is blocked.

## Privacy and observability

SQLite holds only HMAC keys/scope, random attempt ids, versions, bounded validated evidence, usage/accounting, states, and expiry.
It never persists request bodies, profiles, owner identifiers, candidate identities, prompts, tokens, or arbitrary provider error text.
Metrics are aggregate daily admission/cache/response categories, input/output token totals, measured Neurons, conservative reservations, latency buckets, breaker state, and model/manifest/pricing versions.
They expire after seven UTC days; no raw cache fingerprints are exposed in metrics.
Automatic Worker logs/traces are disabled, and this code has no console logging.
Keep provider content capture, AI Gateway logging/caching, and host request/header capture disabled during any future rollout.
Never expose these server operations or raw provider evidence as client trace events; future web integration must use bounded `trace_summary()` output.

The account's Free/cardless status, actual deployment capacity, cold-start latency, new-envelope quality and costs, model-alias stability, and data terms remain unverified rollout gates.
No live request or deployment is part of this slice.
