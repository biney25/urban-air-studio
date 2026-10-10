import { useId, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Trash2 } from "lucide-react";
import { api, downloadProtected } from "../api";
import {
  isObject,
  type EvidenceRecord,
  type EvidenceSummary,
  type SourceAllocation,
} from "../evidence";
import type { Scenario } from "../types";
import { Badge, Empty, ErrorBox, Loading, NumericInput } from "./UI";

const hash = (value: unknown) =>
  typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const display = (value: number) =>
  value.toLocaleString(undefined, { maximumSignificantDigits: 7 });
const invalid = (
  message = "Allocation metadata is inconsistent. No source or scientific approval is inferred.",
): never => {
  throw new Error(message);
};
export interface AllocationContext {
  scenario_id: string;
  scenario_version: number;
  scenario_hash: string;
  coordinate_frame_hash: string;
  coordinate_frame: Record<string, unknown>;
  manual_source_count: number;
  allocation_build_hash: string;
  geometry_kind: "local_metre_polyline";
  units: "m";
  qualified: false;
  admitted_species: string[];
  admitted_background: string[];
}
export interface AllocationRoad {
  segment_id: string;
  coordinates: [number, number][];
}
export interface AllocationControls {
  max_element_length_m: number;
  source_height_m: number;
  max_source_elements: number;
}
export function decodeAllocationContext(
  value: unknown,
  scenarioId: string,
): AllocationContext {
  if (
    !isObject(value) ||
    value.scenario_id !== scenarioId ||
    value.qualified !== false ||
    value.geometry_kind !== "local_metre_polyline" ||
    value.units !== "m" ||
    !hash(value.scenario_hash) ||
    !hash(value.coordinate_frame_hash) ||
    !hash(value.allocation_build_hash) ||
    !isObject(value.coordinate_frame) ||
    !Number.isInteger(value.scenario_version) ||
    Number(value.scenario_version) < 1 ||
    !Number.isInteger(value.manual_source_count) ||
    Number(value.manual_source_count) < 0 ||
    !Array.isArray(value.admitted_species) ||
    !value.admitted_species.includes("passive_tracer") ||
    !Array.isArray(value.admitted_background) ||
    !value.admitted_background.includes("independent_inflow")
  )
    return invalid();
  return value as unknown as AllocationContext;
}
export function decodeSourceAllocation(
  value: unknown,
  projectId: string,
): SourceAllocation {
  if (
    !isObject(value) ||
    value.schema_version !== "phase-b-source-allocation-record-v1" ||
    value.project_id !== projectId ||
    value.qualified !== false ||
    value.status !== "allocated_unexecuted" ||
    typeof value.title !== "string" ||
    !hash(value.plan_hash) ||
    typeof value.id !== "string" ||
    !/^allocation_[0-9a-f]{32}$/.test(value.id) ||
    typeof value.scenario_id !== "string" ||
    typeof value.parent_scenario_id !== "string" ||
    typeof value.inventory_evidence_id !== "string" ||
    !hash(value.content_hash) ||
    !hash(value.artifact_checksum) ||
    !isObject(value.effective_rights) ||
    ["derive", "solve", "export"].some(
      (key) =>
        typeof (value.effective_rights as Record<string, unknown>)[key] !==
        "boolean",
    ) ||
    typeof value.effective_evidence_kind !== "string" ||
    !["observed", "provider-modelled", "simulated", "illustrative"].includes(
      value.effective_evidence_kind,
    ) ||
    !isObject(value.summary)
  )
    return invalid();
  const summary = value.summary;
  if (
    summary.qualified !== false ||
    summary.species !== "passive_tracer" ||
    !finite(summary.total_rate_g_s) ||
    summary.total_rate_g_s < 0 ||
    !finite(summary.background_ug_m3) ||
    summary.background_ug_m3 < 0 ||
    !Number.isInteger(summary.source_count) ||
    Number(summary.source_count) < 1 ||
    Number(summary.source_count) > 200 ||
    !Array.isArray(summary.total_range_g_s) ||
    summary.total_range_g_s.length !== 2 ||
    !summary.total_range_g_s.every(finite) ||
    summary.total_range_g_s[0] < 0 ||
    summary.total_range_g_s[0] > summary.total_rate_g_s ||
    summary.total_range_g_s[1] < summary.total_rate_g_s ||
    summary.range_basis !==
      "declared_parameter_bounds_not_probability_or_confidence_interval" ||
    typeof summary.interval_start_utc !== "string" ||
    typeof summary.interval_end_utc !== "string"
  )
    return invalid();
  return value as unknown as SourceAllocation;
}
export function inventoryRoads(
  record: EvidenceRecord,
): { id: string; length_m: number }[] {
  if (
    record.kind !== "inventory" ||
    record.payload.species !== "passive_tracer" ||
    !isObject(record.payload.background) ||
    record.payload.background.mode !== "independent_inflow"
  )
    return invalid(
      "Choose a passive_tracer inventory with independent inflow background. PM/gas species and coarse-total adjustment need their own compatible method.",
    );
  const segments = record.payload.segments;
  if (!Array.isArray(segments) || !segments.length || segments.length > 200)
    return invalid();
  const result = segments.map((segment) => {
    if (
      !isObject(segment) ||
      typeof segment.id !== "string" ||
      !finite(segment.length_m) ||
      segment.length_m <= 0
    )
      return invalid();
    return { id: segment.id, length_m: segment.length_m };
  });
  if (new Set(result.map((row) => row.id)).size !== result.length)
    return invalid();
  return result;
}
export function roadLength(coordinates: [number, number][]): number | null {
  if (
    coordinates.length < 2 ||
    coordinates.some((row) => row.length !== 2 || row.some((v) => !finite(v)))
  )
    return null;
  return coordinates
    .slice(1)
    .reduce(
      (sum, point, index) =>
        sum +
        Math.hypot(
          point[0] - coordinates[index][0],
          point[1] - coordinates[index][1],
        ),
      0,
    );
}
export function allocationRequest(
  context: AllocationContext,
  record: EvidenceRecord,
  roads: AllocationRoad[],
  controls: AllocationControls,
  replace: boolean,
  title?: string,
) {
  const expected = inventoryRoads(record);
  if (replace !== true)
    return invalid(
      "Confirm replacement of manual sources in a new child scenario before allocating.",
    );
  if (title !== undefined && (!title.trim() || title.trim().length > 120))
    return invalid(
      "Give the new allocation a clear title of at most 120 characters.",
    );
  if (!record.effective_rights.derive || !record.effective_rights.solve)
    return invalid(
      "The selected inventory must permit deriving and solving its sources.",
    );
  if (
    roads.length !== expected.length ||
    new Set(roads.map((row) => row.segment_id)).size !== roads.length
  )
    return invalid("Assign every inventory road exactly once.");
  for (const road of roads) {
    const segment = expected.find((row) => row.id === road.segment_id);
    const length = roadLength(road.coordinates);
    if (
      !segment ||
      length === null ||
      length <= 0 ||
      road.coordinates.some((point) =>
        point.some((v) => v < -1500 || v > 1500),
      ) ||
      Math.abs(length - segment.length_m) / segment.length_m > 0.001
    )
      return invalid(
        "Each road needs finite local-metre points matching its inventory length within0.1%.",
      );
  }
  if (
    !finite(controls.max_element_length_m) ||
    controls.max_element_length_m < 1 ||
    controls.max_element_length_m > 100 ||
    !finite(controls.source_height_m) ||
    controls.source_height_m < 0.25 ||
    controls.source_height_m > 100 ||
    !Number.isInteger(controls.max_source_elements) ||
    controls.max_source_elements < 1 ||
    controls.max_source_elements > 200
  )
    return invalid(
      "Declare source height, maximum element length and a bounded integer source allowance.",
    );
  const count = expected.reduce(
    (sum, road) =>
      sum + Math.ceil(road.length_m / controls.max_element_length_m),
    0,
  );
  if (count > controls.max_source_elements)
    return invalid(
      "The declared discretisation exceeds the source-element allowance.",
    );
  return {
    ...(title === undefined ? {} : { title: title.trim() }),
    scenario_id: context.scenario_id,
    expected_version: context.scenario_version,
    expected_scenario_hash: context.scenario_hash,
    inventory_evidence_id: record.id,
    expected_inventory_hash: record.content_hash,
    replace_manual_sources: true,
    geometry: {
      unit: "m",
      coordinate_frame_hash: context.coordinate_frame_hash,
      roads,
    },
    controls,
  };
}

export function AllocationSummaryView({
  allocation,
}: {
  allocation: SourceAllocation;
}) {
  const summary = allocation.summary;
  return (
    <section aria-label="Frozen traffic allocation summary">
      <h3>Frozen source allocation</h3>
      <Badge tone="amber">
        {allocation.effective_evidence_kind} · unqualified
      </Badge>
      <div className="evidence-metrics">
        <div>
          <span>Central source mass</span>
          <strong>{display(summary.total_rate_g_s)} g/s</strong>
        </div>
        <div>
          <span>Declared parameter range</span>
          <strong>
            {summary.total_range_g_s.map(display).join(" – ")} g/s
          </strong>
        </div>
        <div>
          <span>Finite source elements</span>
          <strong>{summary.source_count}</strong>
        </div>
        <div>
          <span>Independent inflow background</span>
          <strong>{display(summary.background_ug_m3)} µg/m³</strong>
        </div>
      </div>
      <p>
        Stationary representative of {summary.interval_start_utc} to{" "}
        {summary.interval_end_utc}. The range is supplied parameter bounds, with
        no probability or confidence level inferred.
      </p>
      <p>
        Uniform road release is approximated by finite midpoint elements. Native
        fluid volume and resolution/source-width sensitivity still need their
        own checks.
      </p>
      <p>
        Continue through Case preparation and Detailed execution for this
        inventory-bound child scenario.
      </p>
      <p>
        New child scenario <code>{allocation.scenario_id}</code> · parent{" "}
        <code>{allocation.parent_scenario_id}</code>.
      </p>
      <p>
        Inventory <code>{allocation.inventory_evidence_id}</code> · allocation
        hash <code>{allocation.content_hash}</code>.
      </p>
    </section>
  );
}

export default function TrafficAllocationPanel({
  projectId,
  records,
  scenario,
  scenarios,
  onScenario,
}: {
  projectId: string;
  records: EvidenceSummary[];
  scenario?: Scenario;
  scenarios: Scenario[];
  onScenario: (id: string) => void;
}) {
  const client = useQueryClient();
  const parentId = useId(),
    inventorySelectId = useId();
  const [scenarioId, setScenarioId] = useState(scenario?.id ?? "");
  const [inventoryId, setInventoryId] = useState("");
  const [selectedAllocation, setSelectedAllocation] = useState("");
  const [downloadError, setDownloadError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const base = `/projects/${encodeURIComponent(projectId)}/source-allocations`;
  const parent = scenarios.find((row) => row.id === scenarioId);
  const inventory = useQuery({
    queryKey: ["evidence", projectId, inventoryId],
    enabled: !!inventoryId,
    queryFn: ({ signal }) =>
      api<EvidenceRecord>(
        `/projects/${projectId}/evidence/${inventoryId}`,
        undefined,
        signal,
      ),
  });
  const context = useQuery({
    queryKey: ["allocation-context", projectId, scenarioId],
    enabled: !!scenarioId,
    queryFn: async ({ signal }) =>
      decodeAllocationContext(
        await api<unknown>(
          `${base}/context?scenario_id=${encodeURIComponent(scenarioId)}`,
          undefined,
          signal,
        ),
        scenarioId,
      ),
    retry: false,
  });
  const saved = useQuery({
    queryKey: ["source-allocations", projectId],
    queryFn: async ({ signal }) => {
      const response = await api<unknown>(base, undefined, signal);
      if (!Array.isArray(response) || response.length > 100) return invalid();
      return response.map((row) => decodeSourceAllocation(row, projectId));
    },
  });
  const create = useMutation({
    mutationFn: async (request: ReturnType<typeof allocationRequest>) => {
      const result = await api<unknown>(base, request);
      if (
        !isObject(result) ||
        !isObject(result.scenario) ||
        result.scenario.project_id !== projectId ||
        result.scenario.parent_id !== request.scenario_id ||
        typeof result.scenario.id !== "string"
      )
        return invalid();
      const allocation = decodeSourceAllocation(result.allocation, projectId);
      if (
        allocation.scenario_id !== result.scenario.id ||
        allocation.parent_scenario_id !== request.scenario_id ||
        allocation.inventory_evidence_id !== request.inventory_evidence_id
      )
        return invalid();
      return { allocation, scenario: result.scenario as unknown as Scenario };
    },
    onSuccess: async (result) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["project", projectId] }),
        client.invalidateQueries({ queryKey: ["qualification", projectId] }),
        client.invalidateQueries({
          queryKey: ["source-allocations", projectId],
        }),
      ]);
      setSelectedAllocation(result.allocation.id);
      setNotice(
        "Traffic sources and background frozen in a new child scenario. The parent and inventory remain preserved; the allocation is unqualified.",
      );
      onScenario(result.scenario.id);
    },
  });
  async function download(record: SourceAllocation) {
    setDownloadError(null);
    try {
      await downloadProtected(
        `${base}/${record.id}/download`,
        `${record.id}.json`,
      );
    } catch (error) {
      setDownloadError(error);
    }
  }
  const selected = saved.data?.find((row) => row.id === selectedAllocation);
  return (
    <section className="evidence-form" aria-label="Traffic source allocation">
      <div className="evidence-section-title">
        <div>
          <span className="eyebrow">Inventory to native inputs</span>
          <h2>Allocate traffic sources</h2>
        </div>
        <Badge tone="amber">Passive tracer · unqualified</Badge>
      </div>
      <p>
        Bind a frozen traffic inventory to explicit roads in the study’s local
        metre frame. The checked server operation creates a new child scenario
        with conservative source mass and independent inflow background.
      </p>
      <div className="form-grid">
        <div className="form-label">
          <label htmlFor={parentId}>Allocation parent scenario</label>
          <select
            id={parentId}
            value={scenarioId}
            disabled={create.isPending}
            onChange={(event) => setScenarioId(event.target.value)}
          >
            <option value="">Choose the preserved parent</option>
            {scenarios.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name} · v{row.version}
              </option>
            ))}
          </select>
        </div>
        <div className="form-label">
          <label htmlFor={inventorySelectId}>Frozen traffic inventory</label>
          <select
            id={inventorySelectId}
            value={inventoryId}
            disabled={create.isPending}
            onChange={(event) => setInventoryId(event.target.value)}
          >
            <option value="">Choose a saved inventory</option>
            {records
              .filter((row) => row.kind === "inventory")
              .map((row) => (
                <option key={row.id} value={row.id}>
                  {row.title} · {row.effective_evidence_kind}
                </option>
              ))}
          </select>
        </div>
      </div>
      {context.isFetching && !context.data ? (
        <Loading label="Checking frozen metre frame…" />
      ) : context.error ? (
        <ErrorBox error={context.error} />
      ) : null}
      {inventory.isFetching && !inventory.data ? (
        <Loading label="Reading frozen inventory…" />
      ) : inventory.error ? (
        <ErrorBox error={inventory.error} />
      ) : null}
      {context.data && inventory.data && parent ? (
        <AllocationForm
          key={`${context.data.scenario_id}:${inventory.data.id}`}
          context={context.data}
          record={inventory.data}
          scenario={parent}
          pending={create.isPending}
          error={create.error}
          onAllocate={(request) => create.mutate(request)}
        />
      ) : (
        <p>
          Choose a parent and inventory to enter road coordinates and source
          controls. Changing either selection clears the geometry draft.
        </p>
      )}
      {notice ? (
        <p role="status" className="evidence-notice">
          {notice}
        </p>
      ) : null}
      {downloadError ? <ErrorBox error={downloadError} /> : null}
      <h3>Retained allocation records</h3>
      {saved.isPending ? (
        <Loading label="Loading retained allocations…" />
      ) : saved.error ? (
        <ErrorBox error={saved.error} />
      ) : !saved.data?.length ? (
        <Empty title="No allocation records yet">
          A checked new-child operation retains its input geometry, source plan,
          permissions and hashes here.
        </Empty>
      ) : (
        <div className="saved-evidence-grid">
          <nav
            className="saved-record-list"
            aria-label="Frozen traffic allocations"
          >
            {saved.data.map((row) => (
              <button
                key={row.id}
                data-testid={`source-allocation-${row.id}`}
                className={`saved-record ${selectedAllocation === row.id ? "active" : ""}`}
                aria-pressed={selectedAllocation === row.id}
                onClick={() => setSelectedAllocation(row.id)}
              >
                <span>
                  <strong>{row.title}</strong>
                  <small>
                    {row.summary.source_count} source elements ·{" "}
                    {row.effective_evidence_kind}
                  </small>
                  <code>{row.id}</code>
                </span>
              </button>
            ))}
          </nav>
          <article className="evidence-record-detail">
            {selected ? (
              <>
                <AllocationSummaryView allocation={selected} />
                <button
                  className="button"
                  type="button"
                  disabled={!selected.effective_rights.export}
                  onClick={() => void download(selected)}
                >
                  <Download size={15} />
                  Download allocation record
                </button>
                <button
                  className="button small"
                  type="button"
                  onClick={() => onScenario(selected.scenario_id)}
                >
                  Open allocated child scenario
                </button>
              </>
            ) : (
              <p>
                Select a retained allocation to inspect source mass, background
                and provenance.
              </p>
            )}
          </article>
        </div>
      )}
    </section>
  );
}

export function AllocationForm({
  context,
  record,
  scenario,
  pending,
  error,
  onAllocate,
}: {
  context: AllocationContext;
  record: EvidenceRecord;
  scenario: Scenario;
  pending: boolean;
  error: unknown;
  onAllocate: (request: ReturnType<typeof allocationRequest>) => void;
}) {
  let segments;
  try {
    segments = inventoryRoads(record);
  } catch (error) {
    return <ErrorBox error={error} />;
  }
  return (
    <ValidAllocationForm
      context={context}
      record={record}
      scenario={scenario}
      segments={segments}
      pending={pending}
      error={error}
      onAllocate={onAllocate}
    />
  );
}
function ValidAllocationForm({
  context,
  record,
  scenario,
  segments,
  pending,
  error,
  onAllocate,
}: {
  context: AllocationContext;
  record: EvidenceRecord;
  scenario: Scenario;
  segments: { id: string; length_m: number }[];
  pending: boolean;
  error: unknown;
  onAllocate: (request: ReturnType<typeof allocationRequest>) => void;
}) {
  const [roads, setRoads] = useState<AllocationRoad[]>(
    segments.map((row) => ({
      segment_id: row.id,
      coordinates: [
        [NaN, NaN],
        [NaN, NaN],
      ],
    })),
  );
  const [controls, setControls] = useState<AllocationControls>({
    max_element_length_m: NaN,
    source_height_m: NaN,
    max_source_elements: 200,
  });
  const [replace, setReplace] = useState(false),
    [localError, setLocalError] = useState<unknown>(null);
  const [title, setTitle] = useState("");
  function point(
    roadIndex: number,
    pointIndex: number,
    axis: number,
    value: number,
  ) {
    setRoads((old) =>
      old.map((road, r) =>
        r !== roadIndex
          ? road
          : {
              ...road,
              coordinates: road.coordinates.map((row, p) =>
                p !== pointIndex
                  ? row
                  : (row.map((v, a) => (a === axis ? value : v)) as [
                      number,
                      number,
                    ]),
              ),
            },
      ),
    );
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    setLocalError(null);
    try {
      onAllocate(
        allocationRequest(context, record, roads, controls, replace, title),
      );
    } catch (error) {
      setLocalError(error);
    }
  }
  const background = isObject(record.payload.background)
    ? record.payload.background.value_ug_m3
    : undefined;
  return (
    <form
      className="evidence-form"
      onSubmit={submit}
      aria-label="Create checked traffic allocation"
    >
      <label className="form-label">
        Allocation title
        <input
          required
          maxLength={120}
          disabled={pending}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <section
        className="evidence-card"
        aria-label="Declared traffic mass and support"
      >
        <h3>Declared mass and background</h3>
        <Badge tone="amber">{record.effective_evidence_kind}</Badge>
        <p>
          Central source mass{" "}
          {finite(record.summary.total_rate_g_s)
            ? display(record.summary.total_rate_g_s)
            : "Not supplied"}{" "}
          g/s · parameter range{" "}
          {Array.isArray(record.summary.total_range_g_s) &&
          record.summary.total_range_g_s.every(finite)
            ? record.summary.total_range_g_s.map(display).join(" – ")
            : "Not supplied"}{" "}
          g/s.
        </p>
        <p>
          Independent inflow background{" "}
          {finite(background) ? display(background) : "Not supplied"} µg/m³.
          Declared parameter bounds do not imply a confidence interval or field
          calibration.
        </p>
        <p>
          Local-metre frame identity{" "}
          <code>{context.coordinate_frame_hash}</code>. Use x east and y north
          in this frozen frame; latitude/longitude values are incompatible.
        </p>
        <details className="json-viewer">
          <summary>Frozen coordinate frame</summary>
          <pre>{JSON.stringify(context.coordinate_frame, null, 2)}</pre>
        </details>
      </section>
      {roads.map((road, roadIndex) => {
        const length = roadLength(road.coordinates);
        return (
          <fieldset className="evidence-fieldset" key={road.segment_id}>
            <legend>
              Road {road.segment_id} · declared length{" "}
              {display(segments[roadIndex].length_m)} m
            </legend>
            {road.coordinates.map((row, pointIndex) => (
              <div className="form-grid" key={pointIndex}>
                <NumericInput
                  label={`Road ${road.segment_id} point ${pointIndex + 1} x`}
                  id={`allocation-road-${roadIndex}-point-${pointIndex}-x`}
                  value={row[0]}
                  onChange={(v) => point(roadIndex, pointIndex, 0, v)}
                  min={-1500}
                  max={1500}
                  unit="m"
                  disabled={pending}
                />
                <NumericInput
                  label={`Road ${road.segment_id} point ${pointIndex + 1} y`}
                  id={`allocation-road-${roadIndex}-point-${pointIndex}-y`}
                  value={row[1]}
                  onChange={(v) => point(roadIndex, pointIndex, 1, v)}
                  min={-1500}
                  max={1500}
                  unit="m"
                  disabled={pending}
                />
                {road.coordinates.length > 2 ? (
                  <button
                    type="button"
                    className="text-button"
                    disabled={pending}
                    aria-label={`Remove road ${road.segment_id} point ${pointIndex + 1}`}
                    onClick={() =>
                      setRoads((old) =>
                        old.map((v, i) =>
                          i === roadIndex
                            ? {
                                ...v,
                                coordinates: v.coordinates.filter(
                                  (_, p) => p !== pointIndex,
                                ),
                              }
                            : v,
                        ),
                      )
                    }
                  >
                    <Trash2 size={14} />
                    Remove point
                  </button>
                ) : null}
              </div>
            ))}
            <button
              className="button small"
              type="button"
              disabled={pending || road.coordinates.length >= 1000}
              onClick={() =>
                setRoads((old) =>
                  old.map((v, i) =>
                    i === roadIndex
                      ? { ...v, coordinates: [...v.coordinates, [NaN, NaN]] }
                      : v,
                  ),
                )
              }
            >
              <Plus size={14} />
              Add road point
            </button>
            <p>
              Input geometry length:{" "}
              {length === null
                ? "enter all coordinates"
                : `${display(length)} m`}
              . The server requires agreement with the inventory length
              within0.1%, checks buildings/domain and refuses overlapping road
              supports.
            </p>
          </fieldset>
        );
      })}
      <InputGeometryPreview scenario={scenario} roads={roads} />
      <fieldset className="evidence-fieldset">
        <legend>Explicit finite-source controls</legend>
        <div className="form-grid">
          <NumericInput
            label="Maximum source element length"
            id="allocation-element-length"
            value={controls.max_element_length_m}
            onChange={(v) =>
              setControls((old) => ({ ...old, max_element_length_m: v }))
            }
            min={1}
            max={100}
            unit="m"
            disabled={pending}
          />
          <NumericInput
            label="Traffic source height"
            id="allocation-source-height"
            value={controls.source_height_m}
            onChange={(v) =>
              setControls((old) => ({ ...old, source_height_m: v }))
            }
            min={0.25}
            max={100}
            unit="m"
            disabled={pending}
          />
          <NumericInput
            label="Maximum source elements"
            id="allocation-max-elements"
            value={controls.max_source_elements}
            onChange={(v) =>
              setControls((old) => ({ ...old, max_source_elements: v }))
            }
            min={1}
            max={200}
            step={1}
            disabled={pending}
          />
        </div>
        <p>
          This is a stationary finite-element approximation of road emissions.
          Source-width/resolution sensitivity and native fluid-volume support
          remain separate requirements.
        </p>
      </fieldset>
      <div className="permission-options">
        <label>
          <input
            type="checkbox"
            checked={replace}
            disabled={pending}
            onChange={(event) => setReplace(event.target.checked)}
          />
          I confirm replacing the parent’s {context.manual_source_count} manual
          source rows with allocated traffic sources in a new child scenario.
        </label>
      </div>
      {localError || error ? <ErrorBox error={localError || error} /> : null}
      <button
        className="button primary"
        type="submit"
        disabled={
          pending ||
          !replace ||
          !record.effective_rights.derive ||
          !record.effective_rights.solve
        }
      >
        {pending
          ? "Checking and freezing allocation…"
          : "Create allocated child scenario"}
      </button>
    </form>
  );
}

function InputGeometryPreview({
  scenario,
  roads,
}: {
  scenario: Scenario;
  roads: AllocationRoad[];
}) {
  const valid = roads.filter((road) => roadLength(road.coordinates) !== null);
  const domain = scenario.analysis_domain;
  if (
    !domain ||
    ![domain.xmin, domain.ymin, domain.xmax, domain.ymax].every(finite)
  )
    return null;
  const [xmin, ymin, xmax, ymax] = [
    domain.xmin,
    domain.ymin,
    domain.xmax,
    domain.ymax,
  ] as number[];
  const width = xmax - xmin,
    height = ymax - ymin;
  if (width <= 0 || height <= 0) return null;
  const stroke = Math.max(width, height) / 200;
  return (
    <section
      className="evidence-card"
      aria-label="Allocation input geometry preview"
    >
      <h3>Input geometry preview · local metres</h3>
      <p>
        Road polylines use the frozen input frame. This diagram previews input
        geometry and does not establish native fluid support or scientific
        accuracy.
      </p>
      <svg
        role="img"
        aria-label="Local study domain, buildings, receptor inputs and declared road geometry"
        viewBox={`${xmin} ${-ymax} ${width} ${height}`}
        style={{
          display: "block",
          width: "100%",
          maxHeight: 320,
          background: "#080f20",
          border: "1px solid #3b506e",
        }}
      >
        <g transform="scale(1,-1)">
          {scenario.buildings.map((row) => (
            <polygon
              key={row.id}
              points={row.footprint.map((point) => point.join(",")).join(" ")}
              fill="#4b5e78"
              stroke="#a1b4cb"
              strokeWidth={stroke}
            />
          ))}
          {scenario.receptors.map((row) => (
            <circle
              key={row.id}
              cx={row.x}
              cy={row.y}
              r={stroke * 2}
              fill={row.protected ? "#c5adff" : "#a6e8e5"}
            />
          ))}
          {valid.map((row) => (
            <polyline
              key={row.segment_id}
              points={row.coordinates.map((point) => point.join(",")).join(" ")}
              fill="none"
              stroke="#f4bf63"
              strokeWidth={stroke * 2}
            />
          ))}
        </g>
      </svg>
      <p className="micro">
        Grey: buildings · amber: road release · cyan: receptor · violet:
        protected receptor. Domain x {xmin} to {xmax} m, y {ymin} to {ymax} m.
      </p>
    </section>
  );
}
