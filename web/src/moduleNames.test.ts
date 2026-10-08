import { readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";

// A relative import has no extension, so on a case-insensitive filesystem (Windows, macOS)
// "./TasteForm" can resolve to tasteForm.ts instead of TasteForm.tsx. The import then fails in the
// browser and the page is blank, while Linux and CI stay green. Keep module names distinct
// without regard to case or extension.
describe("module names", () => {
  it("do not collide when case and extension are ignored", () => {
    const stems = new Map<string, string[]>();
    for (const file of readdirSync(__dirname)) {
      const stem = /^(.+)\.tsx?$/.exec(file)?.[1];
      if (!stem || stem.endsWith(".test")) continue;
      const key = stem.toLowerCase();
      stems.set(key, [...(stems.get(key) ?? []), file]);
    }
    const collisions = [...stems.values()].filter((files) => files.length > 1);
    expect(collisions).toEqual([]);
  });
});
