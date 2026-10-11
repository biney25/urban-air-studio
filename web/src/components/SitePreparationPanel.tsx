import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { workflowPath } from "../workflowContext";
import { ArrowRight, Check, ShieldAlert } from "lucide-react";
import { api } from "../api";
import {
  preparationPayload,
  type HeightDraft,
  type PreparationContext,
  type PreparationPreview,
  type PreparedSite,
} from "../sitePreparation";
import { Badge, ErrorBox, JsonViewer, Loading } from "./UI";

export default function SitePreparationPanel({
  projectId,
  contextId,
  hasBaseline,
}: {
  projectId: string;
  contextId: string;
  hasBaseline: boolean;
}) {
  const [params] = useSearchParams();
  const context = useQuery({
    queryKey: ["site-preparation-context", projectId, contextId],
    queryFn: ({ signal }) =>
      api<PreparationContext>(
        `/projects/${encodeURIComponent(projectId)}/site-preparations/context?context_id=${encodeURIComponent(contextId)}`,
        undefined,
        signal,
      ),
    retry: false,
  });
  if (context.isPending)
    return <Loading label="Opening building and permission review…" />;
  if (context.error)
    return (
      <ErrorBox error={context.error} onRetry={() => void context.refetch()} />
    );
  if (hasBaseline)
    return (
      <section className="preparation-panel">
        <Badge tone="violet">Baseline already fixed</Badge>
        <p>
          This selected area's context is retained. Review a new site in another
          project; the existing baseline cannot be overwritten.
        </p>
        <Link className="button" to={workflowPath(projectId, "design", params)}>
          Open existing design <ArrowRight size={16} />
        </Link>
      </section>
    );
  return (
    <PreparationReview key={context.data.context_hash} context={context.data} />
  );
}

function PreparationReview({ context }: { context: PreparationContext }) {
  const navigate = useNavigate(),
    client = useQueryClient();
  const [params] = useSearchParams();
  const [reviews, setReviews] = useState<Record<string, HeightDraft>>(() =>
    Object.fromEntries(
      context.buildings.map((b) => [
        b.object_id,
        {
          height: b.height_m === null ? "" : String(b.height_m),
          basis: b.height_m === null ? "assumed" : "osm_tag",
          reference: b.height_m === null ? "" : b.source_url,
          excluded: false,
          reason: "",
        },
      ]),
    ),
  );
  const [exclusions, setExclusions] = useState<Record<string, string>>({});
  const [flat, setFlat] = useState(false),
    [licence, setLicence] = useState(false),
    [illustrative, setIllustrative] = useState(false);
  const [mode, setMode] = useState<
    "geometry_only" | "illustrative_point_study"
  >("geometry_only");
  const [reviewed, setReviewed] = useState(false);
  const [draftError, setDraftError] = useState<unknown>(null);
  const [submitted, setSubmitted] = useState<ReturnType<
    typeof preparationPayload
  > | null>(null);
  const [batchHeight, setBatchHeight] = useState("");
  const [batchReference, setBatchReference] = useState("");
  const [batchConfirmed, setBatchConfirmed] = useState<string | null>(null);
  const projectPath = `/projects/${encodeURIComponent(context.project_id)}`;
  const preview = useMutation({
    mutationFn: (payload: ReturnType<typeof preparationPayload>) =>
      api<PreparationPreview>(
        `${projectPath}/site-preparations/preview`,
        payload,
      ),
    onSuccess: () => setReviewed(false),
  });
  const commit = useMutation({
    mutationFn: () =>
      api<PreparedSite>(`${projectPath}/site-preparations`, {
        ...submitted,
        expected_preparation_hash: preview.data?.preparation_hash,
      }),
    onSuccess: async (result) => {
      client.setQueryData(["scenario", result.scenario.id], result.scenario);
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({
        queryKey: ["project", context.project_id],
      });
      navigate(
        workflowPath(context.project_id, "baseline", params, {
          scenario: result.scenario.id,
          base: null,
          alt: null,
          run: null,
          report: null,
          assistant_session: null,
        }),
      );
    },
  });
  const invalidate = () => {
    preview.reset();
    setSubmitted(null);
    setReviewed(false);
    setDraftError(null);
  };
  const change = (id: string, patch: Partial<HeightDraft>) => {
    invalidate();
    setBatchConfirmed(null);
    setReviews((current) => ({
      ...current,
      [id]: { ...current[id], ...patch },
    }));
  };
  const missing = context.buildings.filter(
    (b) =>
      !reviews[b.object_id]?.excluded && !reviews[b.object_id]?.height.trim(),
  ).length;
  const missingIds = context.buildings
    .filter(
      (b) =>
        !reviews[b.object_id]?.excluded && !reviews[b.object_id]?.height.trim(),
    )
    .map((b) => b.object_id);
  const incompleteReferences = context.buildings.filter(
    (b) =>
      !reviews[b.object_id]?.excluded &&
      reviews[b.object_id]?.reference.trim().length < 8,
  ).length;
  const incompleteOmissions =
    context.buildings.filter(
      (b) =>
        reviews[b.object_id]?.excluded &&
        reviews[b.object_id]?.reason.trim().length < 8,
    ).length +
    context.unsupported_objects.filter(
      (b) => (exclusions[b.object_id] ?? "").trim().length < 8,
    ).length;
  const batchScope = missingIds.join("|");
  const batchValid =
    missingIds.length > 0 &&
    Number.isFinite(Number(batchHeight)) &&
    Number(batchHeight) >= 1 &&
    Number(batchHeight) <= 80 &&
    batchReference.trim().length >= 8 &&
    batchConfirmed === batchScope;
  const busy = preview.isPending || commit.isPending;
  return (
    <section
      className="preparation-panel"
      aria-labelledby="building-review-heading"
    >
      <div className="preparation-heading">
        <div>
          <h2 id="building-review-heading">
            Review buildings and study inputs
          </h2>
          <p>
            {context.buildings.length} buildings · {missing} heights still
            needed · {context.context_only_road_count} roads remain context
          </p>
        </div>
        <Badge tone="amber">Research inputs · unvalidated</Badge>
      </div>
      <p>
        Heights are metres above ground. Record an explicit assumption or your
        own evidence; an OSM tag is community data and remains unverified.
      </p>
      <div className="preparation-progress" role="status">
        <strong>
          {missing || incompleteReferences || incompleteOmissions
            ? "Inputs still need your review"
            : "Object inputs reviewed — check permissions and study assumptions next"}
        </strong>
        <span>
          {missing} heights missing · {incompleteReferences} evidence or
          assumption references needed · {incompleteOmissions} omission reasons
          needed
        </span>
      </div>
      {missingIds.length ? (
        <details className="batch-height-review">
          <summary>
            Declare one height assumption for {missingIds.length} missing-height
            buildings
          </summary>
          <p>
            Use only when the same assumption is appropriate for every listed
            building. This is not measured height data. Existing heights and
            omitted buildings are excluded.
          </p>
          <label className="form-label">
            Assumed height (m above ground)
            <input
              type="number"
              min={1}
              max={80}
              step="any"
              value={batchHeight}
              disabled={busy}
              onChange={(event) => {
                setBatchHeight(event.target.value);
                setBatchConfirmed(null);
              }}
            />
          </label>
          <label className="form-label">
            Shared assumption reference
            <textarea
              value={batchReference}
              minLength={8}
              disabled={busy}
              onChange={(event) => {
                setBatchReference(event.target.value);
                setBatchConfirmed(null);
              }}
              placeholder="Explain why this declared assumption is suitable for these buildings."
            />
          </label>
          <details>
            <summary>Exact affected buildings ({missingIds.length})</summary>
            <ul>
              {missingIds.map((id) => (
                <li key={id}>
                  {context.buildings.find((b) => b.object_id === id)?.name} ·{" "}
                  <code>{id}</code>
                </li>
              ))}
            </ul>
          </details>
          <label className="check-label">
            <input
              type="checkbox"
              checked={batchConfirmed === batchScope}
              disabled={busy}
              onChange={(event) =>
                setBatchConfirmed(event.target.checked ? batchScope : null)
              }
            />
            I reviewed these {missingIds.length} buildings and explicitly apply
            this assumption to each one.
          </label>
          <button
            className="button"
            disabled={busy || !batchValid}
            onClick={() => {
              invalidate();
              setReviews((current) =>
                Object.fromEntries(
                  Object.entries(current).map(([id, value]) => [
                    id,
                    missingIds.includes(id)
                      ? {
                          ...value,
                          height: batchHeight,
                          basis: "assumed",
                          reference: batchReference.trim(),
                        }
                      : value,
                  ]),
                ),
              );
              setBatchConfirmed(null);
            }}
          >
            Apply assumption to {missingIds.length} buildings{" "}
            <Check size={16} />
          </button>
        </details>
      ) : null}
      <div className="building-review-list">
        {context.buildings.map((b) => {
          const review = reviews[b.object_id];
          return (
            <details
              key={b.object_id}
              className="building-review"
              open={context.buildings.length === 1}
            >
              <summary>
                <span>{b.name}</span>
                <Badge
                  tone={
                    review.excluded ? "quiet" : review.height ? "cyan" : "amber"
                  }
                >
                  {review.excluded
                    ? "Omitted"
                    : review.height
                      ? `${review.height} m · ${review.basis.replaceAll("_", " ")}`
                      : "Height needed"}
                </Badge>
              </summary>
              <div className="review-fields">
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={review.excluded}
                    disabled={busy}
                    onChange={(e) =>
                      change(b.object_id, { excluded: e.target.checked })
                    }
                  />
                  Omit this building and retain the reason
                </label>
                {review.excluded ? (
                  <label className="form-label">
                    Omission reason
                    <textarea
                      minLength={8}
                      value={review.reason}
                      disabled={busy}
                      onChange={(e) =>
                        change(b.object_id, { reason: e.target.value })
                      }
                    />
                  </label>
                ) : (
                  <>
                    <label className="form-label">
                      Height (m above ground)
                      <input
                        type="number"
                        min={1}
                        max={80}
                        step="any"
                        required
                        value={review.height}
                        disabled={busy}
                        onChange={(e) =>
                          change(b.object_id, { height: e.target.value })
                        }
                      />
                    </label>
                    <label className="form-label">
                      Height basis
                      <select
                        value={review.basis}
                        disabled={busy}
                        onChange={(e) =>
                          change(b.object_id, {
                            basis: e.target.value as HeightDraft["basis"],
                          })
                        }
                      >
                        <option value="assumed">Explicit assumption</option>
                        <option value="user_declared_evidence">
                          User supplied evidence
                        </option>
                        {b.height_m !== null ? (
                          <option value="osm_tag">
                            Unverified OSM height tag
                          </option>
                        ) : null}
                      </select>
                    </label>
                    <label className="form-label">
                      Evidence / assumption reference
                      <textarea
                        required
                        minLength={8}
                        value={review.reference}
                        disabled={busy}
                        onChange={(e) =>
                          change(b.object_id, { reference: e.target.value })
                        }
                        placeholder="Explain the assumption or identify your survey / permitted source."
                      />
                    </label>
                  </>
                )}
                <details>
                  <summary>Object evidence</summary>
                  <p>
                    Object <code>{b.object_id}</code>
                  </p>
                  {b.source_url ? (
                    <a
                      href={b.source_url}
                      rel="noreferrer"
                      target="_blank"
                      className="text-button"
                    >
                      Open source record
                    </a>
                  ) : null}
                </details>
              </div>
            </details>
          );
        })}
      </div>
      {context.unsupported_objects.length ? (
        <section className="review-unsupported">
          <h3>Unsupported objects need a recorded omission</h3>
          {context.unsupported_objects.map((b) => (
            <label className="form-label" key={b.object_id}>
              {b.object_id} · {b.reason}
              <textarea
                value={exclusions[b.object_id] ?? ""}
                minLength={8}
                disabled={busy}
                onChange={(e) => {
                  invalidate();
                  setExclusions((current) => ({
                    ...current,
                    [b.object_id]: e.target.value,
                  }));
                }}
                placeholder="Why this object is omitted; review the effect on this study."
              />
            </label>
          ))}
        </section>
      ) : null}
      <div className="preparation-options">
        <label className="check-label">
          <input
            type="checkbox"
            checked={flat}
            disabled={busy}
            onChange={(e) => {
              invalidate();
              setFlat(e.target.checked);
            }}
          />
          Use a declared flat-ground assumption; terrain has not been surveyed.
        </label>
        <label className="check-label">
          <input
            type="checkbox"
            checked={licence}
            disabled={busy}
            onChange={(e) => {
              invalidate();
              setLicence(e.target.checked);
            }}
          />
          Retain OpenStreetMap attribution, use this derived model only for
          local research, and confirm my added assumptions or evidence are owned
          or permitted. Public export remains restricted.
        </label>
        <details>
          <summary>Data permission review and limitations</summary>
          <JsonViewer
            data={context.licence_review}
            title="Local licence review"
          />
          {context.limitations.map((item) => (
            <p key={item}>{item}</p>
          ))}
        </details>
        <label className="form-label">
          Study inputs
          <select
            aria-label="Study inputs"
            value={mode}
            disabled={busy}
            onChange={(e) => {
              invalidate();
              setMode(e.target.value as typeof mode);
              setIllustrative(false);
            }}
          >
            <option value="geometry_only">
              Geometry review only — add actual sources and assessment locations
              later
            </option>
            <option value="illustrative_point_study">
              Artificial point-source trial — fictional inputs, for workflow
              investigation
            </option>
          </select>
        </label>
        {mode === "illustrative_point_study" ? (
          <>
            <JsonViewer
              data={context.illustrative_study_template}
              title="Artificial trial inputs"
            />
            <label className="check-label">
              <input
                type="checkbox"
                checked={illustrative}
                disabled={busy}
                onChange={(e) => {
                  invalidate();
                  setIllustrative(e.target.checked);
                }}
              />
              I acknowledge the artificial source and assessment locations are
              illustrative, do not represent traffic or exposure, and cannot
              establish environmental benefit.
            </label>
          </>
        ) : null}
      </div>
      {draftError ? <ErrorBox error={draftError} /> : null}
      {preview.error ? <ErrorBox error={preview.error} /> : null}
      {commit.error ? <ErrorBox error={commit.error} /> : null}
      <div className="preparation-actions">
        <button
          className="button primary"
          disabled={busy || !context.buildings.length}
          onClick={() => {
            try {
              const payload = preparationPayload(
                context,
                reviews,
                exclusions,
                flat,
                licence,
                mode,
                illustrative,
              );
              setSubmitted(payload);
              setDraftError(null);
              preview.mutate(payload);
            } catch (error) {
              setDraftError(error);
            }
          }}
        >
          {preview.isPending
            ? "Checking reviewed inputs…"
            : "Check reviewed inputs"}
          <ArrowRight size={16} />
        </button>
      </div>
      {preview.data ? (
        <section className="preparation-result" aria-live="polite">
          <h3>
            {preview.data.ready_to_commit
              ? "Inputs checked — review the fixed baseline"
              : "Inputs still need review"}
          </h3>
          {preview.data.issues.map((issue, index) => (
            <p key={`${issue.code}-${index}`} className="review-issue">
              <ShieldAlert size={16} />
              {issue.object_id ? `${issue.object_id}: ` : ""}
              {issue.message}
            </p>
          ))}
          {preview.data.warnings.map((item) => (
            <p key={item}>{item}</p>
          ))}
          <JsonViewer
            data={preview.data.proposed_scenario}
            title="Proposed baseline inputs"
          />
          <JsonViewer
            data={{
              source_review: preview.data.source_review,
              permissions: preview.data.compliance_bundle,
              preparation_hash: preview.data.preparation_hash,
            }}
            title="Preparation evidence"
          />
          {preview.data.ready_to_commit ? (
            <>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={reviewed}
                  disabled={busy}
                  onChange={(e) => setReviewed(e.target.checked)}
                />
                I reviewed these exact inputs and omissions. Save them as the
                immutable baseline; scientific validation remains incomplete.
              </label>
              <button
                data-testid="save-prepared-baseline"
                className="button primary"
                disabled={!reviewed || busy}
                onClick={() => commit.mutate()}
              >
                {commit.isPending
                  ? "Saving baseline…"
                  : "Save reviewed baseline"}
                <Check size={16} />
              </button>
            </>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}
