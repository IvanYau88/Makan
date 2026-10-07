import { ApiError } from "./api";
import {
  GROUP_EXPIRED,
  closeGroup,
  createGroup,
  joinGroup,
  readGroup,
  readGroupResult,
  shareInputs,
} from "./groupApi";
import { toInputs, EMPTY_INPUTS } from "./groupForm";

/** Answer every call with a fresh response (a body reads once), or fail every call. */
function stub(answer: (() => Response) | Error | DOMException) {
  const fetchMock = vi.fn(async () => {
    if (typeof answer !== "function") throw answer;
    return answer();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => vi.unstubAllGlobals());

describe("the group API", () => {
  it("creates a group with a JSON body and no credential", async () => {
    const fetchMock = stub(() => json({ link_token: "L" }, 201));
    await createGroup({ latitude: 3.148, longitude: 101.695, request: "dinner", radius_m: 1609 });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/groups");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(String(init.body))).toEqual({
      latitude: 3.148,
      longitude: 101.695,
      request: "dinner",
      radius_m: 1609,
    });
  });

  it("sends the participant token as a bearer credential, never in the URL", async () => {
    const fetchMock = stub(() => json({}));
    await readGroup("L", "tok");
    await shareInputs("L", "tok", toInputs(EMPTY_INPUTS));
    await closeGroup("L", "tok");
    await readGroupResult("L", "tok");
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([url, init]) => `${init.method} ${url}`)).toEqual([
      "GET /api/groups/L",
      "PUT /api/groups/L/me",
      "POST /api/groups/L/close",
      "GET /api/groups/L/result",
    ]);
    for (const [url, init] of calls) {
      expect(init.headers).toMatchObject({ Authorization: "Bearer tok" });
      expect(url).not.toContain("tok");
      expect(init.cache).toBe("no-store");
    }
  });

  it("joins as a guest, with a name only when one was given", async () => {
    const fetchMock = stub(() => json({ participant_token: "p" }, 201));
    await joinGroup("L", "Sam");
    await joinGroup("L", undefined);
    const bodies = (fetchMock.mock.calls as unknown as [string, RequestInit][]).map(([, init]) =>
      JSON.parse(String(init.body)),
    );
    expect(bodies).toEqual([{ display_name: "Sam" }, {}]);
  });

  it("escapes the link so a strange one cannot change the route", async () => {
    const fetchMock = stub(() => json({}));
    await readGroup("a/b?c", null);
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/groups/a%2Fb%3Fc");
  });

  it("raises the backend's message and stable code", async () => {
    stub(() => json({ error: { code: GROUP_EXPIRED, message: "This session has expired." } }, 410));
    await expect(readGroup("L", null)).rejects.toMatchObject({
      name: "ApiError",
      kind: "server",
      code: GROUP_EXPIRED,
      message: "This session has expired.",
    });
  });

  it("calls bad input invalid, and an answer that is not JSON a connection problem", async () => {
    stub(() => json({ error: { code: "invalid_request", message: "no" } }, 422));
    await expect(readGroup("L", null)).rejects.toMatchObject({ kind: "invalid" });
    stub(() => new Response("<html>bad gateway</html>", { status: 200 }));
    await expect(readGroup("L", null)).rejects.toMatchObject({ kind: "network" });
    stub(() => new Response("<html>bad gateway</html>", { status: 502 }));
    await expect(readGroup("L", null)).rejects.toMatchObject({ kind: "server", code: null });
  });

  it("reports an unreachable server as a network error, and passes an abort through", async () => {
    stub(new TypeError("Failed to fetch"));
    await expect(readGroup("L", null)).rejects.toBeInstanceOf(ApiError);
    stub(new DOMException("aborted", "AbortError"));
    await expect(readGroup("L", null)).rejects.toMatchObject({ name: "AbortError" });
  });
});
