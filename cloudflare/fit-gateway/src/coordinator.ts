import { POLICY, Failure, dayOf, resetAt, type Reason } from "./config";
import {
  boundedText,
  qualifiedModel,
  strictJson,
  validatedResult,
  type CaseRequest,
  type ObjectValue,
} from "./contract";

export interface Reply {
  v: 1;
  reasons_version: number;
  status: "ok" | "error" | "pending" | "unknown";
  reason: Reason | null;
  attempt_id: string | null;
  data?: ObjectValue;
  neurons?: number | null;
  retry_after?: number;
}
type Attempt = {
  key: string;
  scope: string;
  id: string;
  day: number;
  started: number;
  state: string;
  expires: number;
  result: string | null;
  reason: Reason | null;
  charged: number;
  neurons: number | null;
  invalidated: number;
  version: string;
};
type Breaker = {
  name: string;
  until_ms: number;
  reason: Reason;
  version: string;
};
export const unavailable = (reason: Reason, retry_after?: number): Reply => ({
  v: 1,
  reasons_version: POLICY.reasonsVersion,
  status: "error",
  reason,
  attempt_id: null,
  ...(retry_after ? { retry_after } : {}),
});
export interface Upstream {
  url: string;
  token: string;
}

// All SQL/transactions are synchronous. No network I/O runs under a storage lock.
export class Coordinator {
  private sql: SqlStorage;
  constructor(
    private storage: DurableObjectStorage,
    private upstream: Upstream,
    // Unbound `fetch` throws "Illegal invocation" in workerd when called as a method.
    private send: typeof fetch = (input, init) => fetch(input, init),
    private now: () => number = Date.now,
  ) {
    this.sql = storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS attempts (
      key TEXT PRIMARY KEY, scope TEXT NOT NULL, id TEXT UNIQUE NOT NULL,
      day INTEGER NOT NULL, started INTEGER NOT NULL, state TEXT NOT NULL,
      expires INTEGER NOT NULL, result TEXT, reason TEXT, charged INTEGER NOT NULL,
      neurons REAL, invalidated INTEGER NOT NULL DEFAULT 0, version TEXT NOT NULL)`);
    this.sql.exec(
      "CREATE INDEX IF NOT EXISTS attempts_scope ON attempts(scope)",
    );
    this.sql.exec(
      "CREATE INDEX IF NOT EXISTS attempts_started ON attempts(started)",
    );
    this.sql.exec(
      "CREATE TABLE IF NOT EXISTS days (day INTEGER PRIMARY KEY, charged INTEGER NOT NULL)",
    );
    this.sql.exec(
      "CREATE TABLE IF NOT EXISTS breakers (name TEXT PRIMARY KEY, until_ms INTEGER NOT NULL, reason TEXT NOT NULL, version TEXT NOT NULL)",
    );
    this.sql.exec(`CREATE TABLE IF NOT EXISTS metrics (
      day INTEGER NOT NULL, category TEXT NOT NULL, calls INTEGER NOT NULL,
      input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
      neurons REAL NOT NULL DEFAULT 0, reserved INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(day, category))`);
  }
  private first<T extends Record<string, SqlStorageValue>>(
    query: string,
    ...args: SqlStorageValue[]
  ): T | undefined {
    return this.sql.exec<T>(query, ...args).toArray()[0];
  }
  private metric(
    category: string,
    input = 0,
    output = 0,
    neurons = 0,
    reserved = 0,
  ): void {
    this.sql.exec(
      `INSERT INTO metrics (day,category,calls,input_tokens,output_tokens,neurons,reserved)
      VALUES (?,?,1,?,?,?,?) ON CONFLICT(day,category) DO UPDATE SET calls=calls+1,
      input_tokens=input_tokens+excluded.input_tokens, output_tokens=output_tokens+excluded.output_tokens,
      neurons=neurons+excluded.neurons, reserved=reserved+excluded.reserved`,
      dayOf(this.now()),
      category,
      input,
      output,
      neurons,
      reserved,
    );
  }
  private breaker(name: string, reason: Reason, until: number): void {
    this.sql.exec(
      `INSERT INTO breakers VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET
      until_ms=max(until_ms,excluded.until_ms),reason=excluded.reason,version=excluded.version`,
      name,
      until,
      reason,
      POLICY.version,
    );
  }
  private sweep(now: number): void {
    // Unknown attempts survive through their reservation day. Successes may span midnight.
    this.sql.exec(
      "UPDATE attempts SET state='unknown', reason='timeout' WHERE state='pending' AND started+? <= ?",
      POLICY.upstreamDeadlineMs,
      now,
    );
    this.sql.exec(
      "UPDATE attempts SET result=NULL,state='unknown',reason='service_unavailable' WHERE state='ok' AND expires <= ?",
      now,
    );
    this.sql.exec(
      `DELETE FROM attempts WHERE started < ? AND
      ((state IN ('pending','unknown') AND day < ?) OR (state='ok' AND expires <= ?) OR state='failed')`,
      now - 60_000,
      dayOf(now),
      now,
    );
    this.sql.exec(
      "DELETE FROM days WHERE day < ?",
      dayOf(now) - POLICY.metricsDays,
    );
    this.sql.exec(
      "DELETE FROM metrics WHERE day < ?",
      dayOf(now) - POLICY.metricsDays + 1,
    );
    this.sql.exec(
      "DELETE FROM breakers WHERE until_ms <= ? OR version != ?",
      now,
      POLICY.version,
    );
  }
  private stored(row: Attempt, now: number): Reply {
    if (
      row.state === "ok" &&
      row.result &&
      !row.invalidated &&
      row.version === POLICY.version &&
      row.expires > now
    ) {
      this.metric("cache_hit");
      return {
        v: 1,
        reasons_version: POLICY.reasonsVersion,
        status: "ok",
        reason: null,
        attempt_id: row.id,
        data: JSON.parse(row.result) as ObjectValue,
        neurons: row.neurons,
      };
    }
    if (row.state === "failed")
      return {
        ...unavailable(
          row.reason ?? "service_unavailable",
          Math.max(1, Math.ceil((row.started + 60_000 - now) / 1000)),
        ),
        attempt_id: row.id,
      };
    return {
      v: 1,
      reasons_version: POLICY.reasonsVersion,
      status: row.state === "pending" ? "pending" : "unknown",
      reason: row.reason ?? "service_unavailable",
      attempt_id: row.id,
      retry_after:
        row.state === "pending"
          ? 1
          : Math.max(1, Math.ceil((resetAt(now) - now) / 1000)),
    };
  }
  private admit(req: CaseRequest): Attempt | Reply {
    const now = this.now();
    return this.storage.transactionSync(() => {
      this.sweep(now);
      const existing = this.first<Attempt>(
        "SELECT * FROM attempts WHERE key=?",
        req.key,
      );
      if (existing) {
        if (existing.scope !== req.scope) return unavailable("invalid_input");
        // An expired result may be recomputed only once its slot no longer counts for rate limits.
        if (
          existing.state !== "ok" ||
          (existing.expires > now &&
            !existing.invalidated &&
            existing.version === POLICY.version)
        )
          return this.stored(existing, now);
        if (existing.started >= now - 60_000)
          return unavailable(
            "service_unavailable",
            Math.max(1, Math.ceil((existing.started + 60_000 - now) / 1000)),
          );
        this.sql.exec("DELETE FROM attempts WHERE key=?", req.key);
      }
      const broken = this.first<Breaker>(
        "SELECT * FROM breakers ORDER BY until_ms DESC LIMIT 1",
      );
      if (broken)
        return unavailable(
          broken.reason,
          Math.max(1, Math.ceil((broken.until_ms - now) / 1000)),
        );
      const rate = this.first<{ n: number; oldest: number }>(
        "SELECT count(*) AS n,min(started) AS oldest FROM attempts WHERE started > ?",
        now - 60_000,
      )!;
      if (rate.n >= POLICY.attemptsPerMinute) {
        this.metric("rate_limited");
        return unavailable(
          "rate_limited",
          Math.max(1, Math.ceil((rate.oldest + 60_000 - now) / 1000)),
        );
      }
      const flight = this.first<{ n: number }>(
        "SELECT count(*) AS n FROM attempts WHERE state='pending'",
      )!.n;
      if (flight >= POLICY.inFlight) {
        this.metric("in_flight_limited");
        return unavailable("rate_limited", 1);
      }
      const day = dayOf(now);
      this.sql.exec("INSERT OR IGNORE INTO days VALUES (?,0)", day);
      const used = this.first<{ charged: number }>(
        "SELECT charged FROM days WHERE day=?",
        day,
      )!.charged;
      if (used + POLICY.reservationMilliNeurons > POLICY.dailyMilliNeurons) {
        this.metric("quota_exhausted");
        return unavailable(
          "quota_exhausted",
          Math.ceil((resetAt(now) - now) / 1000),
        );
      }
      const id = crypto.randomUUID();
      this.sql.exec(
        "UPDATE days SET charged=charged+? WHERE day=?",
        POLICY.reservationMilliNeurons,
        day,
      );
      this.sql.exec(
        `INSERT INTO attempts (key,scope,id,day,started,state,expires,charged,version)
        VALUES (?,?,?,?,?,'pending',?,?,?)`,
        req.key,
        req.scope,
        id,
        day,
        now,
        Math.min(req.expires_at, now + POLICY.cacheTtlMs),
        POLICY.reservationMilliNeurons,
        POLICY.version,
      );
      this.metric("admitted", 0, 0, 0, POLICY.reservationMilliNeurons);
      return this.first<Attempt>(
        "SELECT * FROM attempts WHERE key=?",
        req.key,
      )!;
    });
  }
  async score(req: CaseRequest): Promise<Reply> {
    const admission = this.admit(req);
    if ("status" in admission) return admission;
    const attempt = admission;
    // Explicitly flush the committed reservation before any external side effect.
    try {
      await this.scheduleCleanup();
      await this.storage.sync();
    } catch {
      return {
        ...unavailable("service_unavailable"),
        status: "unknown",
        attempt_id: attempt.id,
      };
    }
    if (this.now() >= attempt.started + POLICY.upstreamDeadlineMs)
      return this.settle(attempt, undefined, "timeout", null, true, 0);
    const controller = new AbortController();
    let timedOut = false;
    const timeout = setTimeout(
      () => {
        timedOut = true;
        controller.abort();
      },
      Math.max(1, attempt.started + POLICY.upstreamDeadlineMs - this.now()),
    );
    let data: ObjectValue | undefined;
    let reason: Reason | undefined;
    let neurons: number | null = null;
    let retryMs = 0;
    let uncertain = false;
    try {
      const response = await this.send(this.upstream.url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.upstream.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(req.body),
        redirect: "manual",
        signal: controller.signal,
      });
      const raw = response.headers.get("cf-ai-neurons") ?? "";
      if (/^[0-9]{1,12}(?:\.[0-9]{1,12})?$/.test(raw)) neurons = Number(raw);
      retryMs = retryAfter(response.headers.get("Retry-After"), this.now());
      if (
        response.status === 401 ||
        response.status === 403 ||
        (response.status >= 300 && response.status < 400)
      )
        reason = "configuration";
      else if (response.status === 429) {
        reason = "rate_limited";
        try {
          const error = strictJson(await boundedText(response, 4096)) as {
            errors?: { code?: unknown }[];
          };
          // Only the documented structured daily-limit code is authoritative.
          if (
            Array.isArray(error.errors) &&
            error.errors.some((e) => e.code === 3036)
          )
            reason = "quota_exhausted";
        } catch {
          /* An unidentified 429 is still only rate limited. */
        }
      } else if (response.status !== 200)
        reason =
          response.status >= 500 ? "service_unavailable" : "configuration";
      else
        data = validatedResult(strictJson(await boundedText(response, 16_384)));
      // Consume no arbitrary provider error body and never log it.
      if (reason && !response.bodyUsed) await response.body?.cancel();
    } catch (error) {
      reason = timedOut
        ? "timeout"
        : error instanceof Failure
          ? error.reason
          : "service_unavailable";
      uncertain = true;
    } finally {
      clearTimeout(timeout);
    }
    if (timedOut) {
      data = undefined;
      reason = "timeout";
      uncertain = true;
    }
    try {
      const result = this.settle(
        attempt,
        data,
        reason,
        neurons,
        uncertain,
        retryMs,
      );
      await this.scheduleCleanup();
      return result;
    } catch {
      return {
        ...unavailable("service_unavailable"),
        status: "unknown",
        attempt_id: attempt.id,
        retry_after: Math.ceil((resetAt(this.now()) - this.now()) / 1000),
      };
    }
  }
  // Exposed to offline fault-injection tests; never a Worker RPC operation.
  settle(
    attempt: Attempt,
    data: ObjectValue | undefined,
    reason: Reason | undefined,
    neurons: number | null,
    uncertain: boolean,
    retryMs: number,
  ): Reply {
    const now = this.now();
    return this.storage.transactionSync(() => {
      const row = this.first<Attempt>(
        "SELECT * FROM attempts WHERE id=?",
        attempt.id,
      );
      if (!row) return unavailable("service_unavailable");
      const measured =
        neurons === null
          ? null
          : Math.ceil(neurons * 1000) + POLICY.roundingSlackMilliNeurons;
      const charge =
        uncertain || measured === null
          ? Math.max(row.charged, measured ?? 0)
          : measured;
      // Always settle the original UTC day, including calls completing after midnight.
      this.sql.exec(
        "UPDATE days SET charged=charged+? WHERE day=?",
        charge - row.charged,
        row.day,
      );
      if (measured !== null && measured > POLICY.reservationMilliNeurons)
        this.breaker("pricing", "configuration", Number.MAX_SAFE_INTEGER);
      if (reason === "configuration")
        this.breaker("configuration", reason, Number.MAX_SAFE_INTEGER);
      else if (reason === "quota_exhausted")
        this.breaker("daily", reason, resetAt(attempt.started));
      else if (
        reason === "rate_limited" ||
        reason === "timeout" ||
        reason === "service_unavailable"
      )
        this.breaker(
          "cooldown",
          reason,
          now + Math.max(POLICY.cooldownMs, retryMs),
        );
      const invalidated = !!row.invalidated || row.expires <= now;
      const usable = data && !invalidated;
      const state = usable
        ? "ok"
        : uncertain || invalidated
          ? "unknown"
          : "failed";
      // Failures are short cooldowns only if billing is known. Otherwise retain a tombstone.
      const durableState = !usable && neurons === null ? "unknown" : state;
      this.sql.exec(
        "UPDATE attempts SET state=?,result=?,reason=?,charged=?,neurons=? WHERE id=?",
        durableState,
        usable ? JSON.stringify(data) : null,
        reason ?? null,
        charge,
        neurons,
        row.id,
      );
      const usage = data
        ? (
            data.result as {
              usage: { input_tokens: number; output_tokens: number };
            }
          ).usage
        : undefined;
      this.metric(
        `result_${usable ? "ok" : (reason ?? "invalidated")}_${now - row.started < 500 ? "lt500" : now - row.started < 1500 ? "lt1500" : "gte1500"}`,
        usage?.input_tokens,
        usage?.output_tokens,
        neurons ?? 0,
      );
      if (usable)
        return {
          v: 1,
          reasons_version: POLICY.reasonsVersion,
          status: "ok",
          reason: null,
          attempt_id: row.id,
          data,
          neurons,
        };
      return {
        v: 1,
        reasons_version: POLICY.reasonsVersion,
        status: durableState === "unknown" ? "unknown" : "error",
        reason: reason ?? "service_unavailable",
        attempt_id: row.id,
        retry_after:
          durableState === "unknown"
            ? Math.max(1, Math.ceil((resetAt(now) - now) / 1000))
            : Math.ceil(Math.max(POLICY.cooldownMs, retryMs) / 1000),
      };
    });
  }
  async scheduleCleanup(): Promise<void> {
    const expiry = this.first<{ expires: number | null }>(
      "SELECT min(expires) AS expires FROM attempts WHERE state='ok'",
    )?.expires;
    await this.storage.setAlarm(
      Math.max(
        this.now() + 1,
        Math.min(expiry ?? Infinity, resetAt(this.now())),
      ),
    );
  }
  async cleanup(): Promise<void> {
    this.storage.transactionSync(() => this.sweep(this.now()));
    await this.scheduleCleanup();
  }
  invalidate(scope: string, keys?: string[]): void {
    this.storage.transactionSync(() => {
      const rows = keys
        ? keys
            .map((key) =>
              this.first<Attempt>(
                "SELECT * FROM attempts WHERE scope=? AND key=?",
                scope,
                key,
              ),
            )
            .filter((r): r is Attempt => !!r)
        : this.sql
            .exec<Attempt>("SELECT * FROM attempts WHERE scope=?", scope)
            .toArray();
      for (const row of rows) {
        // Keep only non-replay tombstones, not personalized results. Quota is never refunded.
        this.sql.exec(
          "UPDATE attempts SET invalidated=1,result=NULL,state=CASE WHEN state='pending' THEN 'pending' ELSE 'unknown' END,reason='service_unavailable' WHERE id=?",
          row.id,
        );
      }
      this.metric("purge");
    });
  }
  metrics(): {
    model: string;
    policy: string;
    manifest: string;
    pricing: string;
    rows: Record<string, SqlStorageValue>[];
    charged: number;
    breakers: Record<string, SqlStorageValue>[];
  } {
    this.sweep(this.now());
    return {
      model: qualifiedModel,
      policy: POLICY.version,
      manifest: POLICY.manifest,
      pricing: POLICY.pricingVersion,
      rows: this.sql
        .exec("SELECT * FROM metrics ORDER BY day,category")
        .toArray(),
      charged:
        this.first<{ charged: number }>(
          "SELECT charged FROM days WHERE day=?",
          dayOf(this.now()),
        )?.charged ?? 0,
      breakers: this.sql
        .exec("SELECT name,until_ms,reason,version FROM breakers")
        .toArray(),
    };
  }
}
export function retryAfter(raw: string | null, now: number): number {
  if (!raw) return 0;
  if (/^[0-9]{1,10}$/.test(raw)) return Number(raw) * 1000;
  if (
    !/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(
      raw,
    )
  )
    return 0;
  const date = Date.parse(raw);
  return Number.isFinite(date) ? Math.max(0, date - now) : 0;
}
