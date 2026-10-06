import { STAGE_INFO, STAGE_POSITION, STATUS_LABEL } from "./stages";
import { durationLabel } from "./format";
import type { Stage, StageName } from "./types";

interface Props {
  stages: Stage[];
  selected: StageName;
  onSelect: (name: StageName) => void;
}

const COLUMNS = 6;
const ROWS = 3;

/**
 * The dependency picture. It repeats what the stage list says, so it is hidden from assistive
 * technology and out of the tab order: the list is the accessible way to pick a stage.
 */
export function FlowGraph({ stages, selected, onSelect }: Props) {
  const point = (name: StageName) => {
    const { col, row } = STAGE_POSITION[name];
    return { x: (col + 0.5) / COLUMNS, y: (row + 0.5) / ROWS };
  };
  return (
    <div className="graph" aria-hidden="true">
      <svg className="graph-edges" viewBox="0 0 100 100" preserveAspectRatio="none">
        {stages.flatMap((stage) =>
          stage.after.map((dep) => {
            const from = point(dep);
            const to = point(stage.name);
            const midX = (from.x + to.x) * 50;
            const failed = stages.find((s) => s.name === dep)?.status === "error";
            return (
              <path
                key={`${dep}-${stage.name}`}
                className={`graph-edge${failed ? " graph-edge-failed" : ""}`}
                d={`M ${from.x * 100} ${from.y * 100} C ${midX} ${from.y * 100}, ${midX} ${to.y * 100}, ${to.x * 100} ${to.y * 100}`}
                vectorEffect="non-scaling-stroke"
                fill="none"
              />
            );
          }),
        )}
      </svg>
      {stages.map((stage) => {
        const { x, y } = point(stage.name);
        return (
          <button
            key={stage.name}
            type="button"
            tabIndex={-1}
            className={`node node-${stage.status}${stage.name === selected ? " node-selected" : ""}`}
            style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
            onClick={() => onSelect(stage.name)}
          >
            <span className="node-title">{STAGE_INFO[stage.name].title}</span>
            <span className="node-status">
              {stage.status === "ok" || stage.status === "error" || stage.status === "timeout"
                ? `${STATUS_LABEL[stage.status]} · ${durationLabel(stage.duration_ms)}`
                : stage.status}
            </span>
          </button>
        );
      })}
    </div>
  );
}
