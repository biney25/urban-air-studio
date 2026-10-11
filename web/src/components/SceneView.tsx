import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useId,
  useState,
  type ReactNode,
} from "react";
import { Box, Compass, Layers, Map, Maximize2, RotateCcw } from "lucide-react";
import type { Field, Scenario, Selection } from "../types";
import {
  fieldColor,
  sceneExtent,
  sampleField,
  formatValue,
  isSynthetic,
} from "../geometry";
const CesiumScene = lazy(() => import("./CesiumScene"));
class GraphicsBoundary extends Component<
  { children: ReactNode; onFailure: (reason: string) => void },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onFailure(
      "The 3D module could not load; editing and results are retained",
    );
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}
export interface SceneProps {
  scenario: Scenario;
  field?: Field;
  selected: Selection | null;
  onSelect: (selection: Selection) => void;
  scaleMax: number;
  camera: "oblique" | "plan" | "overview";
  reset: number;
  onFailure?: (reason: string) => void;
}
export default function SceneView(props: Omit<SceneProps, "camera" | "reset">) {
  const [mode, setMode] = useState<"3d" | "2d">("3d");
  const [camera, setCamera] = useState<SceneProps["camera"]>("oblique");
  const [reset, setReset] = useState(0);
  const [failure, setFailure] = useState<string | null>(null);
  return (
    <section
      className={`scene ${mode === "2d" && !props.field ? "scene-plan" : ""}`}
      aria-label="Analytical site model"
    >
      <div className="scene-toolbar">
        <div className="segmented" aria-label="Map display">
          <button
            className={mode === "3d" ? "active" : ""}
            onClick={() => setMode("3d")}
            disabled={!!failure}
            aria-pressed={mode === "3d"}
          >
            <Box size={14} />
            3D
          </button>
          <button
            data-testid="map-2d"
            className={mode === "2d" ? "active" : ""}
            onClick={() => setMode("2d")}
            aria-pressed={mode === "2d"}
          >
            <Map size={14} />
            2D plan
          </button>
        </div>
        {mode === "3d" ? (
          <div className="scene-tools">
            <button
              onClick={() => {
                setCamera("oblique");
                setReset((x) => x + 1);
              }}
              title="Site oblique"
            >
              <Layers size={16} />
              <span>Oblique</span>
            </button>
            <button
              onClick={() => {
                setCamera("plan");
                setReset((x) => x + 1);
              }}
              title="Plan and reset north"
            >
              <Compass size={16} />
              <span>North</span>
            </button>
            <button
              onClick={() => {
                setCamera("overview");
                setReset((x) => x + 1);
              }}
              title="Fit site"
            >
              <Maximize2 size={16} />
              <span>Fit</span>
            </button>
          </div>
        ) : (
          <span className="scene-plan-label">
            Fixed north-up plan · native geometry
          </span>
        )}
      </div>
      {failure ? (
        <div className="fallback-note" role="status">
          <details>
            <summary>Graphics fallback · 2D plan remains usable</summary>
            <p>
              {failure}. All controls and results remain available in the 2D
              plan.
            </p>
          </details>
          <button
            className="text-button"
            onClick={() => {
              setFailure(null);
              setMode("3d");
            }}
          >
            <RotateCcw size={14} />
            Retry 3D
          </button>
        </div>
      ) : null}
      <div className="scene-canvas">
        {mode === "3d" && !failure ? (
          <GraphicsBoundary
            onFailure={(reason) => {
              setFailure(reason);
              setMode("2d");
            }}
          >
            <Suspense
              fallback={
                <div className="scene-loading">
                  Preparing the analytical 3D scene…
                </div>
              }
            >
              <CesiumScene
                {...props}
                camera={camera}
                reset={reset}
                onFailure={(reason) => {
                  setFailure(reason);
                  setMode("2d");
                }}
              />
            </Suspense>
          </GraphicsBoundary>
        ) : (
          <SvgScene {...props} />
        )}
      </div>
      {props.field && mode === "3d" && !failure ? (
        <div className="scene-probe">
          <FieldProbe field={props.field} />
        </div>
      ) : null}
      <div className="scene-caption">
        <span>
          <i className="violet-dot" />
          Independent semantic geometry
        </span>
        <span>
          Local ENU · metres ·{" "}
          {isSynthetic(props.scenario) ? "synthetic inputs" : "imported inputs"}
        </span>
      </div>
      {props.field ? (
        <div className="field-legend">
          <div>
            <strong>Passive tracer</strong>
            <span>
              {props.field.units} · {formatValue(props.field.height_m, 1)} m
              slice · end state
            </span>
          </div>
          <div className="legend-ramp" />
          <div className="legend-values">
            <span>0</span>
            <span>{formatValue(props.scaleMax)} µg/m³</span>
          </div>
          <span className="legend-mask">▧ Masked / no data</span>
        </div>
      ) : (
        <div className="model-note">
          {mode === "2d" ? (
            <details>
              <summary>
                Geometry only · select an object on the plan or in the list
              </summary>
              <p>
                No concentration field is displayed. Create a study to compute a
                research preview.
              </p>
            </details>
          ) : (
            <>
              <span className="eyebrow">GEOMETRY VIEW</span>
              <p>
                No concentration field displayed.
                <br />
                Create a study to compute a research preview.
              </p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
export function SvgScene({
  scenario,
  field,
  selected,
  onSelect,
  scaleMax,
}: Omit<SceneProps, "camera" | "reset">) {
  const patternId = useId().replaceAll(":", "");
  const extent = field?.extent ?? sceneExtent(scenario);
  const [xmin, ymin, xmax, ymax] = extent;
  const w = xmax - xmin,
    h = ymax - ymin;
  const [probe, setProbe] = useState<{
    x: number;
    y: number;
    value: number | null;
    reason: string | null;
  } | null>(null);
  useEffect(() => setProbe(null), [field, scenario.id]);
  const sx = (x: number) => x - xmin,
    sy = (y: number) => ymax - y;
  return (
    <div className="svg-view">
      <svg
        data-testid="svg-map"
        viewBox={`-15 -15 ${w + 30} ${h + 30}`}
        preserveAspectRatio="xMidYMid meet"
        role="group"
        aria-label="Plan of buildings, sources, receptors and computed slice"
        onClick={(e) => {
          if (!field || (e.target as Element).closest(".svg-object")) return;
          const svg = e.currentTarget,
            p = svg.createSVGPoint(),
            m = svg.getScreenCTM();
          if (!m) return;
          p.x = e.clientX;
          p.y = e.clientY;
          const local = p.matrixTransform(m.inverse());
          const x = local.x + xmin,
            y = ymax - local.y;
          setProbe({ x, y, ...sampleField(field, x, y) });
        }}
      >
        <defs>
          <pattern
            id={`${patternId}-grid`}
            width="20"
            height="20"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 20 0 L 0 0 0 20"
              fill="none"
              stroke="#26334c"
              strokeWidth=".4"
            />
          </pattern>
          <pattern
            id={`${patternId}-no-data`}
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
          >
            <path d="M0 6L6 0" stroke="#8b9bb6" strokeWidth=".7" />
          </pattern>
        </defs>
        <rect
          x="0"
          y="0"
          width={w}
          height={h}
          fill={`url(#${patternId}-grid)`}
          stroke="#3b4962"
          strokeDasharray="3 3"
          strokeWidth=".7"
        />
        {field
          ? Array.from({ length: field.nx * field.ny }, (_, i) => {
              const ix = i % field.nx,
                iy = Math.floor(i / field.nx),
                value = field.values[i];
              return (
                <rect
                  key={i}
                  x={(ix * w) / field.nx}
                  y={h - ((iy + 1) * h) / field.ny}
                  width={w / field.nx + 0.02}
                  height={h / field.ny + 0.02}
                  fill={
                    field.mask[i] && value !== null
                      ? fieldColor(value, scaleMax)
                      : `url(#${patternId}-no-data)`
                  }
                  opacity=".68"
                />
              );
            })
          : null}
        {scenario.buildings.map((b) => (
          <g
            key={b.id}
            className="svg-object"
            role="button"
            tabIndex={0}
            aria-label={`Select ${b.name}, ${b.height_m} metres`}
            onClick={() => onSelect({ kind: "building", id: b.id })}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect({ kind: "building", id: b.id });
              }
            }}
          >
            <polygon
              points={b.footprint
                .map((p) => `${sx(p[0])},${sy(p[1])}`)
                .join(" ")}
              fill={selected?.id === b.id ? "#776cae" : "#8d81c2"}
              stroke={selected?.id === b.id ? "#45dce5" : "#c8b8fa"}
              strokeWidth={selected?.id === b.id ? "1.8" : ".7"}
            />
            <title>
              {b.name} · {b.height_m} m
            </title>
          </g>
        ))}
        {scenario.sources.map((s) => (
          <g
            key={s.id}
            className="svg-object"
            role="button"
            tabIndex={0}
            aria-label={`Select source ${s.name}`}
            onClick={() => onSelect({ kind: "source", id: s.id })}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect({ kind: "source", id: s.id });
              }
            }}
          >
            <circle
              cx={sx(s.x)}
              cy={sy(s.y)}
              r="4"
              fill="#111a2d"
              stroke="#efbf72"
              strokeWidth="1.2"
            />
            <path
              d={`M${sx(s.x) - 1.6},${sy(s.y)}h3.2M${sx(s.x)},${sy(s.y) - 1.6}v3.2`}
              stroke="#efbf72"
              strokeWidth=".8"
            />
            <title>Input source · {s.name}</title>
          </g>
        ))}
        {scenario.receptors.map((r) => (
          <g
            key={r.id}
            className="svg-object"
            role="button"
            tabIndex={0}
            aria-label={`Select receptor ${r.name}`}
            onClick={() => onSelect({ kind: "receptor", id: r.id })}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect({ kind: "receptor", id: r.id });
              }
            }}
          >
            <circle
              cx={sx(r.x)}
              cy={sy(r.y)}
              r="3.5"
              fill="#131e2e"
              stroke="#8cebdf"
              strokeWidth="1.2"
            />
            <circle cx={sx(r.x)} cy={sy(r.y)} r="1" fill="#8cebdf" />
            <text x={sx(r.x) + 5} y={sy(r.y) - 4} fill="#e6f7f6" fontSize="4.8">
              {r.name}
            </text>
            <title>
              {r.name} · {r.z} m {r.protected ? "· protected" : ""}
            </title>
          </g>
        ))}
        <text x={w - 12} y="-5" fill="#bed0e6" fontSize="5" textAnchor="middle">
          N ↑
        </text>
        <line
          x1="5"
          y1={h + 8}
          x2="25"
          y2={h + 8}
          stroke="#bed0e6"
          strokeWidth=".8"
        />
        <text
          x="15"
          y={h + 14}
          fill="#bed0e6"
          fontSize="4.5"
          textAnchor="middle"
        >
          20 m
        </text>
      </svg>
      {probe ? (
        <div className="probe-note" role="status" aria-live="polite">
          Display cell at ({formatValue(probe.x, 1)}, {formatValue(probe.y, 1)})
          m: {formatValue(probe.value)} µg/m³
          {probe.reason ? ` · ${probe.reason}` : ""}
        </div>
      ) : null}
      {field ? (
        <FieldProbe field={field} onProbe={setProbe} hideOutput />
      ) : null}
      <span className="svg-hint">
        Select an object on the plan or in the list.{" "}
        {field
          ? "Click a field cell or enter coordinates below to probe its piecewise-constant display value. "
          : ""}
        {field ? "Exact assessment-location metrics appear below." : ""}
      </span>
    </div>
  );
}

type ProbeValue = {
  x: number;
  y: number;
  value: number | null;
  reason: string | null;
};
export function FieldProbe({
  field,
  onProbe,
  hideOutput = false,
}: {
  field: Field;
  onProbe?: (probe: ProbeValue) => void;
  hideOutput?: boolean;
}) {
  const [x, setX] = useState(String((field.extent[0] + field.extent[2]) / 2));
  const [y, setY] = useState(String((field.extent[1] + field.extent[3]) / 2));
  const [probe, setProbe] = useState<ProbeValue | null>(null);
  useEffect(() => {
    setProbe(null);
  }, [field]);
  return (
    <form
      className="field-probe"
      aria-label="Probe native display cell"
      onSubmit={(event) => {
        event.preventDefault();
        const point = {
          x: Number(x),
          y: Number(y),
          ...sampleField(field, Number(x), Number(y)),
        };
        setProbe(point);
        onProbe?.(point);
      }}
    >
      <label>
        East / X (m)
        <input
          type="number"
          required
          step="any"
          data-testid="probe-x"
          value={x}
          onChange={(event) => setX(event.target.value)}
        />
      </label>
      <label>
        North / Y (m)
        <input
          type="number"
          required
          step="any"
          data-testid="probe-y"
          value={y}
          onChange={(event) => setY(event.target.value)}
        />
      </label>
      <button className="button small" data-testid="probe-cell">
        Probe cell
      </button>
      {!hideOutput && probe ? (
        <output className="probe-note" aria-live="polite">
          Display cell: {formatValue(probe.value)} µg/m³
          {probe.reason
            ? ` · ${probe.reason}`
            : " · native piecewise-constant value"}
        </output>
      ) : null}
    </form>
  );
}
