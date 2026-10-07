import {
  STAGE_AFTER,
  STAGE_INFO,
  STAGE_ORDER,
  STAGE_POSITION,
  blueprintStages,
  outcomeLabel,
  phaseText,
  runSummary,
} from "./stages";
import { browseRun, graphStages, run, scoredRun, stage } from "./test-fixtures";

describe("stages", () => {
  it("describes the eight real stages and how they depend on each other", () => {
    expect(STAGE_AFTER.merge).toEqual(["intent", "requested_places", "nearby_places", "memory"]);
    expect(STAGE_AFTER.requested_places).toEqual(["intent"]);
    expect(blueprintStages().map((s) => s.status)).toEqual(Array(8).fill("waiting"));
  });

  it("knows the signals stage a scorer backend adds, apart from the eight every graph has", () => {
    expect(STAGE_ORDER).toHaveLength(9);
    expect(STAGE_ORDER.indexOf("signals")).toBe(STAGE_ORDER.indexOf("memory") + 1);
    expect(STAGE_AFTER.signals).toEqual([]);
    expect(blueprintStages().map((s) => s.name)).not.toContain("signals");
    // The empty page cannot know whether the server has a scorer, so it shows the common eight.
    expect(blueprintStages().map((s) => s.name)).toEqual(
      STAGE_ORDER.filter((name) => name !== "signals"),
    );
  });

  it("has a title, a position and a distinct cell for every stage the server can report", () => {
    const cells = STAGE_ORDER.map((name) => {
      expect(STAGE_INFO[name].title).not.toBe("");
      expect(STAGE_INFO[name].summary).not.toBe("");
      const { col, row } = STAGE_POSITION[name];
      return `${col},${row}`;
    });
    expect(new Set(cells).size).toBe(STAGE_ORDER.length);
    // The server's own stage names, with and without a scorer, are all drawable.
    for (const scorer of [false, true]) {
      for (const { name } of graphStages(scorer)) expect(STAGE_ORDER).toContain(name);
    }
  });

  it("says what a running run is doing, with a count and no percentage", () => {
    const running = run({ status: "running", outcome: null }, [
      stage("classify"),
      stage("intent"),
      stage("requested_places", { status: "running" }),
      stage("nearby_places", { status: "running" }),
      stage("memory", { status: "error" }),
      stage("merge", { status: "waiting" }),
      stage("rank", { status: "waiting" }),
      stage("explain", { status: "waiting" }),
    ]);
    expect(phaseText(running)).toBe("Searching nearby places (3 of 8 stages done)");
    expect(phaseText(null)).toBe("Starting the search…");
    expect(phaseText(run({}, []))).toBe("Starting the search…");
  });

  it("counts nine stages with a scorer backend and eight without", () => {
    const counted = (r: ReturnType<typeof run>) =>
      phaseText({ ...r, status: "running", outcome: null });
    expect(counted(run())).toBe("Working (8 of 8 stages done)");
    expect(counted(scoredRun())).toBe("Working (9 of 9 stages done)");
    const scored = scoredRun({ status: "running", outcome: null }, [
      stage("classify", { status: "running" }),
      stage("intent", { status: "waiting" }),
      stage("requested_places", { status: "waiting" }),
      stage("nearby_places", { status: "waiting" }),
      stage("memory", { status: "waiting" }),
      stage("signals", { status: "running" }),
      stage("merge", { status: "waiting" }),
      stage("rank", { status: "waiting" }),
      stage("explain", { status: "waiting" }),
    ]);
    expect(phaseText(scored)).toBe("Reading your request (0 of 9 stages done)");
    scored.stages[0] = stage("classify");
    expect(phaseText(scored)).toBe("Reading request signals (1 of 9 stages done)");
  });

  it("explains a partial run without calling the graph a success", () => {
    const partial = run({ status: "failed", outcome: "partial" }, [
      stage("requested_places", { status: "timeout" }),
      stage("merge"),
    ]);
    expect(runSummary(partial)).toBe(
      "Requested places did not finish, so the graph is marked failed. The results that remained are still usable.",
    );
    expect(runSummary(run({ outcome: "no_result" }))).toContain("no place was within the radius");
    expect(runSummary(run({ outcome: "complete" }))).toBe("Every stage finished.");
    expect(
      runSummary(
        run({ status: "failed", outcome: "failed" }, [stage("classify", { status: "error" })]),
      ),
    ).toBe("Classify did not finish, so there was nothing to recommend.");
  });

  it("leaves skipped stages out of the count and the summary of a browse run", () => {
    const running = browseRun({ status: "running", outcome: null });
    running.stages[3] = stage("nearby_places", { status: "running" });
    expect(phaseText(running)).toBe("Searching nearby places (0 of 1 stage done)");
    expect(runSummary(browseRun())).toBe(
      "Browse nearby ran only the places search. The other 7 stages were skipped, and no model was called.",
    );
    expect(runSummary(browseRun({ outcome: "no_result" }))).toContain(
      "No place was within the radius.",
    );
    expect(runSummary(browseRun({ status: "failed", outcome: "failed" }))).toContain(
      "so there was nothing to show",
    );
  });

  it("counts the signals stage as skipped in a browse run on a scorer backend", () => {
    const running = browseRun({ status: "running", outcome: null }, true);
    running.stages[3] = stage("nearby_places", { status: "running" });
    expect(phaseText(running)).toBe("Searching nearby places (0 of 1 stage done)");
    expect(runSummary(browseRun({}, true))).toBe(
      "Browse nearby ran only the places search. The other 8 stages were skipped, and no model was called.",
    );
  });

  it("calls a browse outcome a list, never a recommendation", () => {
    expect(outcomeLabel(run({ outcome: "complete" }))).toBe("Complete recommendation");
    expect(outcomeLabel(browseRun({ outcome: "complete" }))).toBe("Places listed");
    expect(outcomeLabel(browseRun({ outcome: "no_result" }))).toBe("No places found");
    expect(outcomeLabel(browseRun({ outcome: "failed" }))).toBe("No places listed");
    expect(outcomeLabel(browseRun({ status: "running", outcome: null }))).toBe("In progress");
  });
});
