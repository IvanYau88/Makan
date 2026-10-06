import { MAX_HISTORY, abandonRun, recordRun } from "./history";
import type { HistoryEntry } from "./history";
import { run } from "./test-fixtures";

describe("run history", () => {
  it("puts a new run first and updates the same search in place", () => {
    let history: HistoryEntry[] = [];
    history = recordRun(history, 1, run({ status: "running", request: "a" }));
    history = recordRun(history, 2, run({ request: "b" }));
    history = recordRun(history, 1, run({ request: "a", status: "ok" }));
    expect(history.map((e) => [e.key, e.run.status])).toEqual([
      [2, "ok"],
      [1, "ok"],
    ]);
  });

  it("keeps only the newest ten", () => {
    let history: HistoryEntry[] = [];
    for (let key = 1; key <= MAX_HISTORY + 3; key += 1) {
      history = recordRun(history, key, run({ request: `search ${key}` }));
    }
    expect(history).toHaveLength(MAX_HISTORY);
    expect(history[0]?.key).toBe(MAX_HISTORY + 3);
    expect(history.at(-1)?.key).toBe(4);
  });

  it("marks a run it stopped following, but not one that already ended", () => {
    let history = recordRun([], 1, run({ status: "running" }));
    history = recordRun(history, 2, run({ status: "ok" }));
    history = abandonRun(abandonRun(history, 1), 2);
    expect(history.find((e) => e.key === 1)?.abandoned).toBe(true);
    expect(history.find((e) => e.key === 2)?.abandoned).toBe(false);
  });
});
