import { LocationError, locate, parseCoordinates } from "./location";

function stubGeolocation(
  result: { coords: { latitude: number; longitude: number } } | { code: number },
) {
  const error = { PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 };
  vi.stubGlobal("navigator", {
    geolocation: {
      getCurrentPosition: (ok: (p: unknown) => void, fail: (e: unknown) => void) =>
        "coords" in result ? ok(result) : fail({ ...error, code: result.code }),
    },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("locate", () => {
  it("rounds the browser location to about 100 m", async () => {
    stubGeolocation({ coords: { latitude: 3.14812, longitude: 101.69534 } });
    await expect(locate()).resolves.toEqual({ latitude: 3.148, longitude: 101.695 });
  });

  it.each([
    [1, "denied"],
    [2, "unavailable"],
    [3, "timeout"],
  ])("reports geolocation error code %i as %s", async (code, failure) => {
    stubGeolocation({ code });
    await expect(locate()).rejects.toMatchObject({ name: "LocationError", failure });
  });

  it("reports a browser with no geolocation", async () => {
    vi.stubGlobal("navigator", {});
    const error = await locate().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(LocationError);
    expect(error).toMatchObject({ failure: "unsupported" });
  });
});

describe("parseCoordinates", () => {
  it("reads valid numbers, including negatives and padding", () => {
    expect(parseCoordinates(" 3.148 ", "-101.5")).toEqual({
      coordinates: { latitude: 3.148, longitude: -101.5 },
    });
  });

  it("accepts the extremes", () => {
    expect(parseCoordinates("90", "-180")).toEqual({
      coordinates: { latitude: 90, longitude: -180 },
    });
  });

  it("says what is wrong with each field", () => {
    expect(parseCoordinates("", "abc")).toEqual({
      errors: { latitude: "Enter a number.", longitude: "Enter a number, such as 40.713." },
    });
    expect(parseCoordinates("91", "181")).toEqual({
      errors: {
        latitude: "Must be between -90 and 90.",
        longitude: "Must be between -180 and 180.",
      },
    });
  });

  it("only flags the bad field", () => {
    expect(parseCoordinates("3", "x")).toEqual({
      errors: { longitude: "Enter a number, such as 40.713." },
    });
  });
});
