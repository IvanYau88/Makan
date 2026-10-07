import { ApiError } from "./api";
import {
  deleteAccount,
  exportData,
  fetchMe,
  saveProfile,
  saveTaste,
  sessionEnded,
} from "./accountApi";

const TASTE = { likes: ["thai"], dislikes: [], allergies: ["peanut"], diets: [], never_places: [] };

function stub(response: Response | Error | DOMException) {
  const fetchMock = vi.fn().mockImplementation(async () => {
    if (!(response instanceof Response)) throw response;
    return response.clone();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const requestOf = (fetchMock: ReturnType<typeof vi.fn>) => {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init, headers: init.headers as Record<string, string> };
};

describe("account calls", () => {
  it("sends the token as a bearer credential and never caches", async () => {
    const fetchMock = stub(new Response(JSON.stringify({ user: { id: "u", email: null } })));
    await fetchMe("tok");
    const { url, init, headers } = requestOf(fetchMock);
    expect(url).toBe("/api/me");
    expect(headers.Authorization).toBe("Bearer tok");
    expect(init.cache).toBe("no-store");
  });

  it("saves the profile and the taste as JSON and returns what the server stored", async () => {
    const profile = { display_name: "Bob", location_history_opt_in: false };
    let fetchMock = stub(new Response(JSON.stringify({ profile })));
    await expect(saveProfile("tok", profile)).resolves.toEqual(profile);
    expect(requestOf(fetchMock).init.method).toBe("PUT");
    expect(requestOf(fetchMock).url).toBe("/api/me/profile");
    expect(JSON.parse(String(requestOf(fetchMock).init.body))).toEqual(profile);

    fetchMock = stub(new Response(JSON.stringify({ taste: TASTE })));
    await expect(saveTaste("tok", TASTE)).resolves.toEqual(TASTE);
    expect(requestOf(fetchMock).url).toBe("/api/me/taste");
  });

  it("exports the file text and deletes with DELETE", async () => {
    let fetchMock = stub(new Response('{"user":{}}'));
    await expect(exportData("tok")).resolves.toBe('{"user":{}}');
    expect(requestOf(fetchMock).url).toBe("/api/me/export");

    fetchMock = stub(new Response(null, { status: 204 }));
    await deleteAccount("tok");
    expect(requestOf(fetchMock).init.method).toBe("DELETE");
  });

  it("raises the server's message and code, and 422 as invalid", async () => {
    stub(
      new Response(
        JSON.stringify({ error: { code: "invalid_request", message: "Use 40 characters." } }),
        {
          status: 422,
        },
      ),
    );
    await expect(
      saveProfile("tok", { display_name: "x", location_history_opt_in: false }),
    ).rejects.toMatchObject({
      kind: "invalid",
      code: "invalid_request",
      message: "Use 40 characters.",
    });
  });

  it("says it cannot reach Makan when the network fails, but lets an abort through", async () => {
    stub(new TypeError("offline"));
    await expect(fetchMe("tok")).rejects.toMatchObject({ kind: "network" });
    const abort = new DOMException("Aborted", "AbortError");
    stub(abort);
    await expect(fetchMe("tok")).rejects.toBe(abort);
  });
});

describe("sessionEnded", () => {
  it.each(["token_expired", "invalid_token", "account_deleted"])("is true for %s", (code) => {
    expect(sessionEnded(new ApiError("server", "m", code))).toBe(true);
  });

  it("is false for other failures", () => {
    expect(sessionEnded(new ApiError("server", "m", "invalid_request"))).toBe(false);
    expect(sessionEnded(new ApiError("network", "m"))).toBe(false);
    expect(sessionEnded(new Error("x"))).toBe(false);
  });
});
