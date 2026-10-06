import { ApiError, fetchConfig, recommend } from "./api";
import { RESULT, run } from "./test-fixtures";
import type { RecommendRequest, Run } from "./types";

const BODY: RecommendRequest = {
  mode: "recommend",
  latitude: 3.148,
  longitude: 101.695,
  request: "thai",
  radius_m: 1000,
};

/** A response whose body arrives in the given pieces, so lines can be split anywhere. */
function chunked(...pieces: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const piece of pieces) controller.enqueue(encoder.encode(piece));
        controller.close();
      },
    }),
    { status: 200 },
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("recommend", () => {
  it("reads run snapshots and the result even when lines are split across chunks", async () => {
    const snapshot = run({ status: "running", outcome: null });
    const lines = [
      JSON.stringify({ type: "run", run: snapshot }),
      JSON.stringify({ type: "result", recommendation: RESULT }),
    ].join("\n");
    const cut = lines.indexOf("running") + 3;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(chunked(lines.slice(0, cut), lines.slice(cut))),
    );
    const seen: Run[] = [];
    const answer = await recommend(BODY, (r) => seen.push(r));
    expect(seen).toEqual([snapshot]);
    expect(answer.pick?.name).toBe("Mid Thai");
  });

  it("posts to the streaming route as JSON", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        chunked(JSON.stringify({ type: "result", recommendation: RESULT }) + "\n"),
      );
    vi.stubGlobal("fetch", fetchMock);
    await recommend(BODY, () => undefined);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/recommendations/stream");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(BODY);
  });

  it("turns an error line into an ApiError that carries the failed run", async () => {
    const failed = run({ status: "failed", outcome: "failed" });
    const line = JSON.stringify({
      type: "error",
      status: 503,
      error: { code: "model_busy", message: "Busy." },
      run: failed,
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(chunked(line + "\n")));
    const error = await recommend(BODY, () => undefined).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "model_busy", message: "Busy.", run: failed });
  });

  it("ignores lines it cannot read and fails when no answer ever comes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(chunked("not json\n", '{"type":"other"}\n')));
    await expect(recommend(BODY, () => undefined)).rejects.toMatchObject({ kind: "network" });
  });

  it("reports an HTTP error with the server's message, or a generic one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "invalid_request", message: "Check it." } }), {
          status: 422,
        }),
      ),
    );
    await expect(recommend(BODY, () => undefined)).rejects.toMatchObject({
      kind: "invalid",
      code: "invalid_request",
      message: "Check it.",
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 502 })));
    await expect(recommend(BODY, () => undefined)).rejects.toMatchObject({
      kind: "server",
      code: null,
    });
  });

  it("lets an abort through as an abort, not as a network error", async () => {
    const abort = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abort));
    await expect(recommend(BODY, () => undefined)).rejects.toBe(abort);
  });
});

describe("fetchConfig", () => {
  it("returns the mode and the map tiles", async () => {
    const config = {
      mode: "demo",
      map: { tile_url: "https://t/{z}/{x}/{y}.png", attribution: "© T", attribution_url: null },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(config))));
    await expect(fetchConfig()).resolves.toEqual(config);
  });

  it("gives null for anything it cannot use", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ mode: "x" }))));
    await expect(fetchConfig()).resolves.toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await expect(fetchConfig()).resolves.toBeNull();
  });
});
