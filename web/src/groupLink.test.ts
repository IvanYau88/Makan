import {
  forgetCredential,
  groupPath,
  groupUrl,
  linkFromPath,
  loadCredential,
  saveCredential,
} from "./groupLink";

describe("group links", () => {
  it("reads the link token out of a shared address", () => {
    expect(linkFromPath("/g/abc-123")).toBe("abc-123");
    expect(linkFromPath("/g/abc-123/")).toBe("abc-123");
    expect(linkFromPath("/g/a%20b")).toBe("a b");
  });

  it("is not fooled by other addresses", () => {
    for (const path of ["/", "/g", "/g/", "/g/a/b", "/group/abc", "/api/groups/abc"]) {
      expect(linkFromPath(path)).toBeNull();
    }
  });

  it("keeps a malformed escape as it is, and leaves the server to say the link is unknown", () => {
    expect(linkFromPath("/g/%E0%A4%A")).toBe("%E0%A4%A");
  });

  it("builds the address to share on this origin", () => {
    expect(groupPath("abc")).toBe("/g/abc");
    expect(groupUrl("abc")).toBe(`${window.location.origin}/g/abc`);
  });
});

describe("the participant token kept on this device", () => {
  it("is kept per group and can be forgotten", () => {
    expect(loadCredential("one")).toBeNull();
    saveCredential("one", "token-1");
    saveCredential("two", "token-2");
    expect(loadCredential("one")).toBe("token-1");
    expect(loadCredential("two")).toBe("token-2");
    forgetCredential("one");
    expect(loadCredential("one")).toBeNull();
    expect(loadCredential("two")).toBe("token-2");
  });

  it("works without storage, since a blocked browser still lets a person use the page", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => saveCredential("one", "t")).not.toThrow();
    expect(() => forgetCredential("one")).not.toThrow();
    expect(loadCredential("one")).toBeNull();
    vi.restoreAllMocks();
  });
});
