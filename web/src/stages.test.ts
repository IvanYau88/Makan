import { STAGE_AFTER, STAGE_ORDER, blueprintStages, phaseText, runSummary } from "./stages";
import { run, stage } from "./test-fixtures";

describe("stages", () => {
  it("describes the eight real stages and how they depend on each other", () => {
    expect(STAGE_ORDER).toHaveLength(8);
    expect(STAGE_AFTER.merge).toEqual(["intent", "requested_places", "nearby_places", "memory"]);
    expect(STAGE_AFTER.requested_places).toEqual(["intent"]);
    expect(blueprintStages().map((s) => s.status)).toEqual(Array(8).fill("waiting"));
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
});
