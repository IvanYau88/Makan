import { useState } from "react";
import { FlowGraph } from "./FlowGraph";
import { coordinateLabel, durationLabel, radiusLabel } from "./format";
import type { HistoryEntry } from "./history";
import {
  STAGE_INFO,
  STATUS_LABEL,
  blueprintStages,
  outcomeLabel,
  phaseText,
  runSummary,
} from "./stages";
import { MODE_COPY } from "./searchMode";
import type { Run, Stage, StageName } from "./types";

interface Props {
  history: HistoryEntry[];
  onClear: () => void;
}

export function ExecutionView({ history, onClear }: Props) {
  const [chosenKey, setChosenKey] = useState<number | null>(null);
  const [chosenStage, setChosenStage] = useState<StageName | null>(null);
  const entry = history.find((e) => e.key === chosenKey) ?? history[0] ?? null;
  const run = entry?.run ?? null;
  const stages = run && run.stages.length > 0 ? run.stages : blueprintStages();
  // With no pick, start on a stage that ran: a skipped one has nothing to show.
  const stage =
    stages.find((s) => s.name === chosenStage) ??
    stages.find((s) => s.status !== "skipped") ??
    stages[0];

  return (
    <div className="execution">
      <header className="execution-head">
        <p className="eyebrow">Execution</p>
        <h2 className="page-title">The graph Makan actually runs</h2>
        <p className="lead">
          Pick for me runs these eight stages, and a ninth, Signals, when a scorer backend is set.
          Browse nearby runs only the nearby places search and marks the rest skipped, because it
          makes no model call. This page shows what the server recorded for your searches in this
          tab, with real inputs, outputs, timings and errors. History is kept in this tab only and
          is gone when you reload.
        </p>
      </header>

      <div className="execution-body">
        <aside className="history" aria-labelledby="history-title">
          <div className="history-head">
            <h3 id="history-title" className="subtitle">
              Run history
            </h3>
            {history.length > 0 && (
              <button type="button" className="button button-small" onClick={onClear}>
                Clear history
              </button>
            )}
          </div>
          {history.length === 0 ? (
            <p className="hint">
              No runs yet. Search in Discover and the run appears here. Up to 10 are kept.
            </p>
          ) : (
            <ol className="runs" aria-label="Runs">
              {history.map((e) => (
                <li key={e.key}>
                  <button
                    type="button"
                    className="run-button"
                    aria-current={e.key === entry?.key ? "true" : undefined}
                    onClick={() => setChosenKey(e.key)}
                  >
                    <span className="run-title">{e.run.request ?? "Browse nearby"}</span>
                    <span className="run-meta">{runLine(e)}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </aside>

        <div className="inspector">
          {run ? <RunSummary run={run} abandoned={entry?.abandoned ?? false} /> : <Blueprint />}

          <FlowGraph
            stages={stages}
            selected={stage?.name ?? "classify"}
            onSelect={setChosenStage}
          />

          <h3 className="subtitle">Stages</h3>
          <StageList
            stages={stages}
            selected={stage?.name ?? "classify"}
            onSelect={setChosenStage}
            totalMs={run?.duration_ms ?? null}
          />

          {stage && (
            <StageDetail stage={stage} hasRun={!!run} browsing={run?.search_mode === "browse"} />
          )}
        </div>
      </div>
    </div>
  );
}

function runLine(entry: HistoryEntry): string {
  const { run } = entry;
  if (entry.abandoned) return "Stopped watching · it may have finished unseen";
  if (run.status === "running") return phaseText(run);
  return `${run.outcome ? outcomeLabel(run) : run.status} · ${run.mode} · ${durationLabel(run.duration_ms)}`;
}

function Blueprint() {
  return (
    <p className="notice notice-info">
      No run to show yet. This is the graph as it is wired: select a stage to see what it does.
    </p>
  );
}

function RunSummary({ run, abandoned }: { run: Run; abandoned: boolean }) {
  return (
    <section className="run-summary" aria-label="Run summary">
      <dl className="facts">
        <Fact label="Search" value={MODE_COPY[run.search_mode].label} />
        <Fact label="Request" value={`${run.request ?? "None"} · ${radiusLabel(run.radius_m)}`} />
        <Fact
          label="Search center"
          value={coordinateLabel(run.center.latitude, run.center.longitude)}
        />
        <Fact
          label="Graph"
          value={run.status === "running" ? "Running" : run.status === "ok" ? "Ok" : "Failed"}
        />
        <Fact label="Outcome" value={outcomeLabel(run)} />
        <Fact label="Mode" value={run.mode === "demo" ? "Demo" : "Live"} />
        <Fact label="Data source" value={run.data_source} />
        <Fact label="Duration" value={durationLabel(run.duration_ms)} />
      </dl>
      <p
        className={`notice ${run.outcome === "partial" || run.outcome === "failed" ? "notice-warn" : "notice-info"}`}
      >
        {abandoned
          ? "The page stopped following this run, because a newer search began or the connection dropped. The server does not cancel it, so it may have finished without being recorded here."
          : runSummary(run)}
      </p>
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function StageList({
  stages,
  selected,
  onSelect,
  totalMs,
}: {
  stages: Stage[];
  selected: StageName;
  onSelect: (name: StageName) => void;
  totalMs: number | null;
}) {
  const span =
    totalMs ?? Math.max(1, ...stages.map((s) => (s.started_ms ?? 0) + (s.duration_ms ?? 0)));
  return (
    <ol className="stage-list" aria-label="Stages">
      {stages.map((stage, index) => {
        const left = stage.started_ms === null ? 0 : (stage.started_ms / span) * 100;
        const width =
          stage.duration_ms === null ? 0 : Math.max(1.5, (stage.duration_ms / span) * 100);
        return (
          <li key={stage.name}>
            <button
              type="button"
              className="stage-button"
              aria-current={stage.name === selected ? "true" : undefined}
              onClick={() => onSelect(stage.name)}
            >
              <span className="stage-index" aria-hidden="true">
                {index + 1}
              </span>
              <span className="stage-name">{STAGE_INFO[stage.name].title}</span>
              <span className={`stage-status status-${stage.status}`}>
                {STATUS_LABEL[stage.status]}
              </span>
              <span className="stage-time">{durationLabel(stage.duration_ms)}</span>
              <span className="stage-bar" aria-hidden="true">
                <span
                  className={`stage-fill status-${stage.status}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                />
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function StageDetail({
  stage,
  hasRun,
  browsing,
}: {
  stage: Stage;
  hasRun: boolean;
  browsing: boolean;
}) {
  const info = STAGE_INFO[stage.name];
  return (
    <section className="stage-detail" aria-labelledby="stage-detail-title">
      <h3 id="stage-detail-title" className="section-title">
        {info.title}
      </h3>
      <p>
        {browsing && stage.name === "nearby_places"
          ? "The places search, with no filters. Browse nearby runs only this stage, and the list shows its places in the order they came back, nearest first."
          : info.summary}
      </p>
      {hasRun && stage.status === "skipped" ? (
        <>
          <dl className="facts">
            <Fact label="Status" value={STATUS_LABEL.skipped} />
          </dl>
          <p className="hint">
            {browsing
              ? "Browse nearby asks for no suggestion, so this stage did not run and no model was called."
              : "This stage did not run."}
          </p>
        </>
      ) : !hasRun ? null : (
        <>
          <dl className="facts">
            <Fact label="Status" value={STATUS_LABEL[stage.status]} />
            <Fact label="Duration" value={durationLabel(stage.duration_ms)} />
            <Fact
              label="Starts after"
              value={
                stage.after.length
                  ? stage.after.map((n) => STAGE_INFO[n].title).join(", ")
                  : "Nothing: it starts the run"
              }
            />
            <Fact
              label="Ran alongside"
              value={
                stage.parallel_with.length
                  ? stage.parallel_with.map((n) => STAGE_INFO[n].title).join(", ")
                  : "Nothing"
              }
            />
          </dl>
          {stage.status === "waiting" && (
            <p className="hint">It cannot start until every stage before it has ended.</p>
          )}
          {stage.status === "queued" && (
            <p className="hint">Its inputs are ready. It is waiting for a free concurrency slot.</p>
          )}
          {stage.error && (
            <p className="notice notice-error" role="note">
              <strong>{stage.error.type}:</strong> {stage.error.message}
            </p>
          )}
          <Json title="Input" value={stage.input} />
          <Json title="Output" value={stage.output} />
          {stage.calls.map((call, i) => (
            <Json
              key={i}
              title={call.kind === "model" ? "Model calls" : `Tool call: ${call.name ?? ""}`}
              value={call}
            />
          ))}
          {stage.name === "memory" && stage.status === "ok" && (
            <p className="hint">Not used for guest sessions: no stored tastes were read.</p>
          )}
        </>
      )}
    </section>
  );
}

function Json({ title, value }: { title: string; value: unknown }) {
  if (value === null || value === undefined) return null;
  return (
    <div className="json">
      <h4 className="subtitle">{title}</h4>
      <pre tabIndex={0} role="region" aria-label={`${title}, as JSON`}>
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
