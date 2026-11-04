import contract from "../../../src/makan/evals/sets/clef/contract.json";
import schema from "../../../src/makan/evals/sets/clef/state.schema.json";
import { Failure, POLICY } from "./config";

export const qualifiedModel = contract.model;

export type ObjectValue = Record<string, unknown>;
export function object(value: unknown, keys?: string[]): ObjectValue {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Failure("malformed_response");
  const obj = value as ObjectValue;
  if (
    keys &&
    (Object.keys(obj).length !== keys.length ||
      keys.some((key) => !Object.hasOwn(obj, key)))
  )
    throw new Failure("malformed_response");
  return obj;
}
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
// Scan JSON structure before parsing, rejecting duplicate keys, NaN and deep nesting.
export function strictJson(text: string): unknown {
  let pos = 0;
  const ws = () => {
    while (/\s/.test(text[pos] ?? "") && pos < text.length) pos++;
  };
  function string(): string {
    const start = pos++;
    while (pos < text.length) {
      const ch = text[pos++];
      if (ch === "\\") pos++;
      else if (ch === '"') return JSON.parse(text.slice(start, pos)) as string;
    }
    throw new Failure("malformed_response");
  }
  function value(depth: number): void {
    if (depth > 24) throw new Failure("malformed_response");
    ws();
    if (text[pos] === '"') {
      string();
      return;
    }
    const open = text[pos];
    if (open === "{" || open === "[") {
      pos++;
      ws();
      const close = open === "{" ? "}" : "]";
      const keys = new Set<string>();
      if (text[pos] === close) {
        pos++;
        return;
      }
      while (pos < text.length) {
        if (open === "{") {
          ws();
          if (text[pos] !== '"') throw new Failure("malformed_response");
          const key = string();
          if (keys.has(key)) throw new Failure("malformed_response");
          keys.add(key);
          ws();
          if (text[pos++] !== ":") throw new Failure("malformed_response");
        }
        value(depth + 1);
        ws();
        if (text[pos] === close) {
          pos++;
          return;
        }
        if (text[pos++] !== ",") throw new Failure("malformed_response");
      }
      throw new Failure("malformed_response");
    }
    const token =
      /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(
        text.slice(pos),
      );
    if (!token) throw new Failure("malformed_response");
    pos += token[0].length;
  }
  value(0);
  ws();
  if (pos !== text.length) throw new Failure("malformed_response");
  return JSON.parse(text) as unknown;
}
export async function boundedText(
  response: Request | Response,
  limit: number,
): Promise<string> {
  if (!response.body) throw new Failure("malformed_response");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > limit) throw new Failure("malformed_response");
      chunks.push(part.value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const part of chunks) {
    bytes.set(part, offset);
    offset += part.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}
export const questions = Object.fromEntries(
  Object.entries(contract.questions).map(([key, q]) => [
    key,
    { type: q.type, instructions: q.instructions, criteria: q.criteria },
  ]),
);
export interface CaseRequest {
  v: 1;
  namespace: "preview" | "production";
  scope: string;
  key: string;
  manifest: string;
  expires_at: number;
  body: { model: string; state: string; questions: typeof questions };
}
export const isHmac = (v: unknown): v is string =>
  typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
export function validateRequest(
  raw: unknown,
  namespace: string,
  now: number,
): CaseRequest {
  try {
    const req = object(raw, [
      "v",
      "namespace",
      "scope",
      "key",
      "manifest",
      "expires_at",
      "body",
    ]);
    if (
      req.v !== 1 ||
      req.namespace !== namespace ||
      !isHmac(req.scope) ||
      !isHmac(req.key) ||
      req.manifest !== POLICY.manifest
    )
      throw new Error();
    if (
      typeof req.expires_at !== "number" ||
      !Number.isSafeInteger(req.expires_at) ||
      req.expires_at <= now ||
      req.expires_at > now + POLICY.cacheTtlMs
    )
      throw new Error();
    const body = object(req.body, ["model", "state", "questions"]);
    if (
      body.model !== contract.selector ||
      canonical(body.questions) !== canonical(questions) ||
      typeof body.state !== "string"
    )
      throw new Error();
    if (
      new TextEncoder().encode(canonical(body)).length >
      contract.input_policy.request_max_bytes
    )
      throw new Error();
    const state = object(strictJson(body.state), ["profile", "candidate"]);
    for (const key of ["profile", "candidate"] as const) {
      const spec = schema.properties[key];
      const item = object(state[key], spec.required);
      for (const [name, rule] of Object.entries(spec.properties)) {
        const value = item[name];
        if ("const" in rule) {
          if (value !== rule.const) throw new Error();
        } else if ("items" in rule) {
          if (
            !Array.isArray(value) ||
            value.some((v) => typeof v !== "string") ||
            new Set(value).size !== value.length
          )
            throw new Error();
          const items = rule.items;
          if (
            value.some((v) =>
              "enum" in items
                ? !(items.enum as string[]).includes(v)
                : new RegExp(items.pattern).exec(v)?.[0] !== v,
            )
          )
            throw new Error();
        } else if (
          typeof value !== "string" ||
          [...value].length < rule.minLength ||
          [...value].length > rule.maxLength
        )
          throw new Error();
      }
    }
    const profile = object(state.profile);
    if (
      !(profile.likes as unknown[]).length &&
      !(profile.dislikes as unknown[]).length
    )
      throw new Error();
    // Always forward a fresh bounded projection, never arbitrary envelope fields.
    return {
      ...req,
      body: { model: contract.selector, state: canonical(state), questions },
    } as unknown as CaseRequest;
  } catch {
    throw new Failure("invalid_input");
  }
}
function number(value: unknown, low: number, high: number): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < low ||
    value > high
  )
    throw new Failure("malformed_response");
  return value;
}
function probabilities(value: unknown, ids: string[]): Record<string, number> {
  const obj = object(value, ids);
  const values = ids.map((id) => number(obj[id], 0, 1));
  const total = values.reduce((a, b) => a + b, 0);
  if (
    !total ||
    Math.abs(total - 1) > contract.validation.probability_sum_tolerance
  )
    throw new Failure("malformed_response");
  return Object.fromEntries(ids.map((id, i) => [id, values[i] / total]));
}
function refused(obj: ObjectValue): boolean {
  return !!obj.refusal || obj.refused === true || obj.type === "refusal";
}
export function validatedResult(raw: unknown): ObjectValue {
  const data = object(raw);
  if (refused(data)) throw new Failure("refused");
  if (
    data.success !== true ||
    (Array.isArray(data.errors) ? data.errors.length : data.errors)
  )
    throw new Failure("service_unavailable");
  const result = object(data.result);
  if (refused(result)) throw new Failure("refused");
  if (result.model !== contract.selector) throw new Failure("configuration");
  const usage = object(result.usage);
  const input_tokens = number(usage.input_tokens, 0, Number.MAX_SAFE_INTEGER);
  const output_tokens = number(usage.output_tokens, 0, Number.MAX_SAFE_INTEGER);
  if (!Number.isInteger(input_tokens) || !Number.isInteger(output_tokens))
    throw new Failure("malformed_response");
  const answers = object(result.answers, ["taste_fit", "fit_score"]);
  const choice = object(answers.taste_fit);
  const score = object(answers.fit_score);
  if (refused(choice) || refused(score)) throw new Failure("refused");
  if (choice.type !== "choice" || score.type !== "score")
    throw new Failure("malformed_response");
  const p = probabilities(
    choice.probabilities,
    Object.keys(contract.questions.taste_fit.criteria),
  );
  if (
    typeof choice.choice !== "string" ||
    !(choice.choice in p) ||
    p[choice.choice] !== Math.max(...Object.values(p))
  )
    throw new Failure("malformed_response");
  const legend = Object.fromEntries(
    contract.questions.fit_score.criteria.map((v, i) => [String(i), v]),
  );
  if (canonical(score.legend) !== canonical(legend))
    throw new Failure("malformed_response");
  const ordinal = probabilities(score.probabilities, Object.keys(legend));
  const expectation = Object.entries(ordinal).reduce(
    (sum, [i, p]) => sum + Number(i) * p,
    0,
  );
  if (
    Math.abs(number(score.score, 0, 4) - expectation) >
    contract.validation.wire_score_tolerance
  )
    throw new Failure("malformed_response");
  // Project only validated native fields. This is also accepted by the slice-2 parser.
  return {
    success: true,
    result: {
      model: contract.selector,
      usage: { input_tokens, output_tokens },
      answers: {
        taste_fit: {
          type: "choice",
          probabilities: p,
          choice: choice.choice,
          confidence: number(choice.confidence, 0, 1),
        },
        fit_score: {
          type: "score",
          probabilities: ordinal,
          legend,
          score: expectation,
          confidence: number(score.confidence, 0, 1),
        },
      },
    },
  };
}
