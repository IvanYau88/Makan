import { DEFAULT_MODE, MODE_COPY, loadMode, saveMode } from "./searchMode";

afterEach(() => vi.restoreAllMocks());

describe("the remembered search mode", () => {
  it("starts on Pick for me and remembers what was chosen", () => {
    expect(DEFAULT_MODE).toBe("recommend");
    expect(loadMode()).toBe("recommend");
    saveMode("browse");
    expect(loadMode()).toBe("browse");
    saveMode("recommend");
    expect(loadMode()).toBe("recommend");
  });

  it("ignores a stored value it does not know", () => {
    window.localStorage.setItem("makan.search-mode", "something new");
    expect(loadMode()).toBe("recommend");
  });

  it("carries on when storage cannot be read or written", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(loadMode()).toBe("recommend");
    expect(() => saveMode("browse")).not.toThrow();
  });

  it("names the two choices as the page words them", () => {
    expect(MODE_COPY.recommend.label).toBe("Pick for me");
    expect(MODE_COPY.browse.label).toBe("Browse nearby");
  });
});
