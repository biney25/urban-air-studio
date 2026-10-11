import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import type { Project, Scenario } from "../types";
import { Badge, ErrorBox, JsonViewer, Loading } from "./UI";

interface Corridor {
  id: string;
  name: string;
  footprint: [number, number][];
}
interface Requirements {
  question: string;
  species: "passive_tracer";
  storey_height_m: number | null;
  min_floor_area_m2: number | null;
  min_building_gap_m: number | null;
  access_corridors: Corridor[];
  protected_location_ids: string[];
}
interface Brief {
  id: string;
  revision: number;
  content_hash: string;
  requirements: Requirements;
}
interface ConstraintCheck {
  valid: boolean;
  status: "unconfigured" | "pass" | "fail";
  issues: { code: string; message: string; object_id?: string }[];
  evaluations: Record<string, unknown>[];
  qualification: "geometry_only";
}

export default function PlanningBriefPanel({
  project,
  scenario,
}: {
  project: Project;
  scenario?: Scenario;
}) {
  const brief = useQuery({
    queryKey: ["planning-brief", project.id],
    queryFn: ({ signal }) =>
      api<Brief | null>(
        `/projects/${encodeURIComponent(project.id)}/planning-brief`,
        undefined,
        signal,
      ),
    retry: false,
  });
  return (
    <details className="planning-brief-panel">
      <summary>
        Planning question and requirements{" "}
        {brief.data ? (
          <Badge tone="cyan">Revision {brief.data.revision}</Badge>
        ) : (
          <Badge tone="amber">Review needed</Badge>
        )}
      </summary>
      {brief.isPending ? (
        <Loading label="Opening planning requirements…" />
      ) : brief.error ? (
        <ErrorBox error={brief.error} onRetry={() => void brief.refetch()} />
      ) : (
        <BriefEditor
          key={`${project.id}-${brief.data?.revision ?? 0}`}
          project={project}
          scenario={scenario}
          brief={brief.data ?? null}
        />
      )}
    </details>
  );
}

function BriefEditor({
  project,
  scenario,
  brief,
}: {
  project: Project;
  scenario?: Scenario;
  brief: Brief | null;
}) {
  const client = useQueryClient();
  const [question, setQuestion] = useState(
    brief?.requirements.question ?? project.planning_question ?? "",
  );
  const [storey, setStorey] = useState(
    brief?.requirements.storey_height_m?.toString() ?? "",
  );
  const [floorArea, setFloorArea] = useState(
    brief?.requirements.min_floor_area_m2?.toString() ?? "",
  );
  const [gap, setGap] = useState(
    brief?.requirements.min_building_gap_m?.toString() ?? "",
  );
  const [protectedIds, setProtectedIds] = useState(
    brief?.requirements.protected_location_ids ?? [],
  );
  const [corridors, setCorridors] = useState(
    brief?.requirements.access_corridors ?? [],
  );
  const [corridorName, setCorridorName] = useState(""),
    [x, setX] = useState(""),
    [y, setY] = useState(""),
    [width, setWidth] = useState(""),
    [depth, setDepth] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  const [dirty, setDirty] = useState(false);
  const check = useMutation({
    mutationFn: () =>
      api<ConstraintCheck>(
        `/projects/${encodeURIComponent(project.id)}/constraints/check`,
        { scenario_id: scenario?.id },
      ),
  });
  const save = useMutation({
    mutationFn: () => {
      const optional = (value: string, min: number, max: number) => {
        if (!value.trim()) return null;
        const n = Number(value);
        if (!Number.isFinite(n) || n < min || n > max)
          throw new Error(
            `Enter a number from ${min} to ${max}, or leave the requirement unspecified.`,
          );
        return n;
      };
      return api<Brief>(
        `/projects/${encodeURIComponent(project.id)}/planning-brief`,
        {
          expected_revision: brief?.revision ?? 0,
          question: question.trim(),
          species: "passive_tracer",
          storey_height_m: optional(storey, 2, 8),
          min_floor_area_m2: optional(floorArea, 0, 1000000),
          min_building_gap_m: optional(gap, 0, 100),
          access_corridors: corridors,
          protected_location_ids: protectedIds,
        },
      );
    },
    onSuccess: async (value) => {
      client.setQueryData(["planning-brief", project.id], value);
      await client.invalidateQueries({ queryKey: ["project", project.id] });
      await client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const edited = () => {
    setDirty(true);
    check.reset();
    save.reset();
    setLocalError(null);
  };
  return (
    <form
      className="brief-editor"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <p>
        Record the decision and any programme requirements. These checks assess
        declared geometry; they do not prove access-code compliance, planning
        approval or environmental no-harm.
      </p>
      <label className="form-label">
        Planning question
        <textarea
          value={question}
          maxLength={1000}
          required
          rows={3}
          onChange={(event) => {
            edited();
            setQuestion(event.target.value);
          }}
        />
      </label>
      <div className="brief-numbers">
        <label className="form-label">
          Declared storey height (m)
          <input
            type="number"
            min={2}
            max={8}
            step="any"
            value={storey}
            placeholder="Unspecified"
            onChange={(event) => {
              edited();
              setStorey(event.target.value);
            }}
          />
        </label>
        <label className="form-label">
          Minimum floor area (m²)
          <input
            type="number"
            min={0}
            max={1000000}
            step="any"
            value={floorArea}
            placeholder="Unspecified"
            onChange={(event) => {
              edited();
              setFloorArea(event.target.value);
            }}
          />
        </label>
        <label className="form-label">
          Minimum building gap (m)
          <input
            type="number"
            min={0}
            max={100}
            step="any"
            value={gap}
            placeholder="Unspecified"
            onChange={(event) => {
              edited();
              setGap(event.target.value);
            }}
          />
        </label>
      </div>
      <p className="micro">
        Floor area uses the explicit storey-height assumption. Blank fields
        remain unspecified; they do not create an inferred capacity requirement.
      </p>
      {scenario ? (
        <fieldset className="brief-protected">
          <legend>Protected assessment locations</legend>
          <p className="micro">
            Retain these locations through edits. Their pollution outcomes must
            still be inspected in the matched comparison.
          </p>
          {scenario.receptors.length ? (
            scenario.receptors.map((point) => (
              <label className="check-label" key={point.id}>
                <input
                  type="checkbox"
                  checked={protectedIds.includes(point.id)}
                  onChange={(event) => {
                    edited();
                    setProtectedIds((current) =>
                      event.target.checked
                        ? [...current, point.id]
                        : current.filter((id) => id !== point.id),
                    );
                  }}
                />
                {point.name}
              </label>
            ))
          ) : (
            <p>
              Add assessment locations before choosing protection requirements.
            </p>
          )}
          {protectedIds
            .filter(
              (id) => !scenario.receptors.some((point) => point.id === id),
            )
            .map((id) => (
              <p key={id} className="review-issue">
                Protected location {id} is absent from this version; it stays in
                the requirements.
              </p>
            ))}
        </fieldset>
      ) : null}
      <details className="brief-corridors">
        <summary>Access corridors · local metre coordinates</summary>
        <p>
          Draw a rectangular clear corridor using the site's local frame.
          Existing non-rectangular corridors remain preserved.
        </p>
        {corridors.map((corridor) => (
          <div className="brief-corridor-item" key={corridor.id}>
            <span>{corridor.name}</span>
            <button
              className="text-button"
              type="button"
              onClick={() => {
                edited();
                setCorridors((current) =>
                  current.filter((item) => item.id !== corridor.id),
                );
              }}
            >
              <Trash2 size={14} />
              Remove
            </button>
            <JsonViewer
              data={corridor.footprint}
              title="Corridor coordinates"
            />
          </div>
        ))}
        <label className="form-label">
          Corridor name
          <input
            value={corridorName}
            maxLength={120}
            onChange={(event) => setCorridorName(event.target.value)}
          />
        </label>
        <div className="brief-numbers">
          {[
            ["Start X (m)", x, setX],
            ["Start Y (m)", y, setY],
            ["Width (m)", width, setWidth],
            ["Depth (m)", depth, setDepth],
          ].map(([label, value, setter]) => (
            <label className="form-label" key={label as string}>
              {label as string}
              <input
                type="number"
                step="any"
                value={value as string}
                onChange={(event) =>
                  (setter as (value: string) => void)(event.target.value)
                }
              />
            </label>
          ))}
        </div>
        <button
          className="button"
          type="button"
          onClick={() => {
            try {
              const [px, py, w, d] = [x, y, width, depth].map((value) =>
                value.trim() ? Number(value) : NaN,
              );
              if (
                !corridorName.trim() ||
                ![px, py, w, d].every(Number.isFinite) ||
                w <= 0 ||
                d <= 0 ||
                Math.abs(px) > 1500 ||
                Math.abs(py) > 1500 ||
                Math.abs(px + w) > 1500 ||
                Math.abs(py + d) > 1500
              )
                throw new Error(
                  "Enter a name and finite local coordinates, with positive width and depth, within ±1500 m.",
                );
              edited();
              setCorridors((current) => [
                ...current,
                {
                  id: `access-${Date.now()}`,
                  name: corridorName.trim(),
                  footprint: [
                    [px, py],
                    [px + w, py],
                    [px + w, py + d],
                    [px, py + d],
                  ],
                },
              ]);
              setCorridorName("");
            } catch (error) {
              setLocalError(error);
            }
          }}
        >
          <Plus size={16} />
          Add declared corridor
        </button>
      </details>
      {localError ? <ErrorBox error={localError} /> : null}
      {save.error ? <ErrorBox error={save.error} /> : null}
      {check.error ? <ErrorBox error={check.error} /> : null}
      <div className="brief-actions">
        <button
          className="button"
          disabled={
            save.isPending ||
            !question.trim() ||
            (!!floorArea.trim() && !storey.trim())
          }
        >
          {save.isPending ? "Saving requirements…" : "Save requirements"}
          <Check size={16} />
        </button>
        <button
          className="button"
          type="button"
          disabled={!brief || !scenario || check.isPending || dirty}
          onClick={() => check.mutate()}
        >
          {check.isPending ? "Checking…" : "Check saved requirements"}
        </button>
      </div>
      {check.data ? (
        <section className="brief-result" aria-live="polite">
          <Badge tone={check.data.valid ? "cyan" : "amber"}>
            {check.data.status === "pass"
              ? "Declared geometry requirements pass"
              : check.data.status === "unconfigured"
                ? "Requirements need review"
                : "Design needs review"}
          </Badge>
          {check.data.issues.map((issue, index) => (
            <p key={index}>{issue.message}</p>
          ))}
          <p>
            Environmental effects and neighbouring locations still require a
            matched study. This is a geometry-only check.
          </p>
          <JsonViewer
            data={check.data.evaluations}
            title="Constraint evidence"
          />
        </section>
      ) : null}
    </form>
  );
}
