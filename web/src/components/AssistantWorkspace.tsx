import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, Check, Sparkles } from "lucide-react";
import { api } from "../api";
import {
  assistantEvidencePath,
  proposalCanApply,
  type AssistantReply,
  type AssistantStatus,
} from "../assistant";
import type { Scenario } from "../types";
import { Badge, ErrorBox, JsonViewer, Loading, SectionHeading } from "./UI";

export default function AssistantWorkspace({
  projectId,
  scenario,
  baselineId,
  baselineRunId,
  alternativeRunId,
  onChanged,
  onBranch,
  planningQuestion,
}: {
  projectId: string;
  scenario?: Scenario;
  baselineId: string | null;
  baselineRunId: string;
  alternativeRunId: string;
  onChanged: (scenario: Scenario) => Promise<void>;
  onBranch: () => void;
  planningQuestion?: string | null;
}) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const sessionId = params.get("assistant_session") ?? "";
  const [message, setMessage] = useState(
    planningQuestion
      ? `Inspect the inputs and missing evidence for this planning question: ${planningQuestion}. Do not infer a validated environmental benefit.`
      : "Check this site, identify missing inputs and explain what evidence is needed for a matched comparison.",
  );
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [applied, setApplied] = useState<string | null>(null);
  const root = `/projects/${encodeURIComponent(projectId)}/assistant`;
  const status = useQuery({
    queryKey: ["assistant-status", projectId],
    queryFn: ({ signal }) =>
      api<AssistantStatus>(`${root}/status`, undefined, signal),
    retry: false,
    refetchInterval: 15000,
  });
  const history = useQuery({
    queryKey: ["assistant-history", projectId, sessionId],
    queryFn: ({ signal }) =>
      api<{ messages: { message: string; reply: AssistantReply }[] }>(
        `${root}/sessions/${encodeURIComponent(sessionId)}`,
        undefined,
        signal,
      ),
    enabled: !!sessionId,
    retry: false,
  });
  const ask = useMutation({
    mutationFn: (question: string) =>
      api<AssistantReply>(`${root}/messages`, {
        message: question.trim(),
        ...(scenario ? { scenario_id: scenario.id } : {}),
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(baselineRunId &&
        alternativeRunId &&
        baselineRunId !== alternativeRunId
          ? {
              baseline_run_id: baselineRunId,
              alternative_run_id: alternativeRunId,
            }
          : {}),
      }),
    onSuccess: async (reply) => {
      setConfirmed(null);
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("assistant_session", reply.session_id);
          return next;
        },
        { replace: true },
      );
      await client.invalidateQueries({
        queryKey: ["assistant-history", projectId],
      });
      await status.refetch();
    },
  });
  const apply = useMutation({
    mutationFn: (reply: AssistantReply) => {
      if (
        !reply.proposal ||
        !proposalCanApply(reply.proposal, scenario, baselineId)
      )
        throw new Error(
          "This proposal no longer matches an editable, checked version. Ask again for the current alternative.",
        );
      return api<Scenario>(
        `/scenarios/${encodeURIComponent(reply.proposal.scenario_id)}/operations`,
        {
          expected_version: reply.proposal.expected_version,
          operations: reply.proposal.operations,
        },
      );
    },
    onSuccess: async (updated, _reply) => {
      setApplied(_reply.id);
      setConfirmed(null);
      await onChanged(updated);
    },
  });
  const replies =
    history.data?.messages ??
    (ask.data ? [{ message: ask.variables ?? message, reply: ask.data }] : []);
  return (
    <div className="content-page assistant-workspace">
      <SectionHeading
        eyebrow="INVESTIGATE"
        title="Study assistant"
        description="Inspect inputs, review a proposed alternative, and open the exact study evidence behind an explanation."
      />
      <div className="assistant-status" role="status">
        <Sparkles size={18} />
        {status.isPending ? (
          <span>Checking the local assistant…</span>
        ) : status.error ? (
          <ErrorBox
            error={status.error}
            onRetry={() => void status.refetch()}
          />
        ) : (
          <div>
            <Badge tone={status.data?.available ? "cyan" : "amber"}>
              {status.data?.state === "ready"
                ? "Local AI available"
                : status.data?.state === "busy"
                  ? "Local AI busy"
                  : "AI unavailable"}
            </Badge>
            <p>{status.data?.message}</p>
          </div>
        )}
      </div>
      <p className="assistant-scope">
        The assistant uses scoped project tools. Proposed edits need your
        review; its explanation cannot establish validated exposure benefit.
      </p>
      <div className="assistant-task-context">
        <strong>
          {scenario
            ? `${scenario.name} · version ${scenario.version}`
            : "Project inputs only"}
        </strong>
        <p>
          {baselineRunId &&
          alternativeRunId &&
          baselineRunId !== alternativeRunId
            ? "An exact baseline and alternative run pair is selected. Explanations must use that evidence."
            : "Select two completed matched runs in Compare before requesting a comparison explanation."}
        </p>
        <div
          className="assistant-prompt-actions"
          aria-label="Suggested investigation tasks"
        >
          <button
            className="button small"
            disabled={ask.isPending}
            onClick={() =>
              setMessage(
                "Inspect the current project and scenario. Identify missing or assumed inputs and explain the next evidence needed. Do not claim a validated pollution forecast or environmental benefit.",
              )
            }
          >
            Check missing inputs
          </button>
          <button
            className="button small"
            disabled={
              ask.isPending ||
              !baselineRunId ||
              !alternativeRunId ||
              baselineRunId === alternativeRunId
            }
            onClick={() =>
              setMessage(
                "Read the exact selected matched comparison. Explain its research-preview limitation and inconclusive status. Do not restate numerical values or IDs; the authoritative evidence cards carry them.",
              )
            }
          >
            Explain selected comparison
          </button>
          <button
            className="button small"
            disabled={ask.isPending || !scenario}
            onClick={() =>
              setMessage(
                "Read the scenario geometry and saved planning requirements. Explain which declared inputs can be reviewed and what constraints must pass before an alternative is saved. Do not apply edits or infer environmental no-harm.",
              )
            }
          >
            Review editing constraints
          </button>
        </div>
      </div>
      <form
        className="assistant-compose"
        onSubmit={(event) => {
          event.preventDefault();
          ask.mutate(message);
        }}
      >
        <label className="form-label">
          Ask about this study
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            minLength={1}
            maxLength={2000}
            required
            rows={3}
            disabled={ask.isPending}
          />
        </label>
        <div className="assistant-compose-actions">
          <span>
            {scenario
              ? `${scenario.name} · v${scenario.version}`
              : "No model selected — input inspection only"}
          </span>
          <button
            className="button primary"
            disabled={
              !status.data?.available || ask.isPending || !message.trim()
            }
          >
            {ask.isPending ? "Investigating…" : "Ask assistant"}
            <ArrowRight size={16} />
          </button>
        </div>
      </form>
      {ask.isPending ? (
        <Loading label="The local model is inspecting scoped inputs. This may take a few minutes…" />
      ) : null}
      {ask.error ? <ErrorBox error={ask.error} /> : null}
      {history.error ? (
        <ErrorBox
          error={history.error}
          onRetry={() => void history.refetch()}
        />
      ) : null}
      {apply.error ? <ErrorBox error={apply.error} /> : null}
      <div className="assistant-conversation">
        {replies.map(({ message: question, reply }) => (
          <article key={reply.id} className="assistant-reply">
            <p className="assistant-question">{question}</p>
            <div className="assistant-reply-heading">
              <h2>Investigation</h2>
              <Badge tone={reply.status === "complete" ? "cyan" : "amber"}>
                {reply.status === "complete"
                  ? "Evidence inspected"
                  : reply.status === "blocked"
                    ? "Response needs review"
                    : "Unavailable"}
              </Badge>
            </div>
            <p className="assistant-answer">{reply.answer}</p>
            <section className="assistant-tools">
              <h3>Tool activity</h3>
              <ol>
                {reply.tool_calls.map((call, index) => (
                  <li key={`${call.name}-${index}`}>
                    <span>{call.name.replaceAll("_", " ")}</span>
                    <Badge
                      tone={
                        call.status === "complete"
                          ? "cyan"
                          : call.status === "failed"
                            ? "red"
                            : "amber"
                      }
                    >
                      {call.status}
                    </Badge>
                  </li>
                ))}
              </ol>
            </section>
            <nav
              className="assistant-evidence"
              aria-label="Linked assistant evidence"
            >
              {reply.evidence.map((evidence) => (
                <Link
                  key={`${evidence.kind}-${evidence.id}`}
                  to={assistantEvidencePath(projectId, evidence, params)}
                >
                  {evidence.label}
                  <ArrowRight size={14} />
                </Link>
              ))}
            </nav>
            <div className="assistant-authoritative">
              {reply.evidence
                .filter((evidence) => evidence.details)
                .map((evidence) => (
                  <AuthoritativeEvidence
                    key={evidence.id}
                    evidence={evidence}
                  />
                ))}
            </div>
            {reply.proposal ? (
              <section className="assistant-proposal">
                <h3>Review proposed alternative</h3>
                <JsonViewer
                  data={reply.proposal.operations}
                  title="Proposed changes"
                />
                <p>
                  Geometry:{" "}
                  {reply.proposal.validation.geometry_valid
                    ? "checked"
                    : "needs review"}{" "}
                  · Programme requirements:{" "}
                  {reply.proposal.validation.programme_status.replaceAll(
                    "_",
                    " ",
                  )}
                </p>
                {reply.proposal.validation.issues.map((issue, index) => (
                  <p key={index}>
                    {String(issue.message ?? issue.code ?? "Review required")}
                  </p>
                ))}
                {scenario?.id === baselineId ? (
                  <>
                    <p>
                      The baseline stays fixed. Create an alternative, then ask
                      the assistant to review that version.
                    </p>
                    <button className="button" onClick={onBranch}>
                      Create an alternative <ArrowRight size={16} />
                    </button>
                  </>
                ) : proposalCanApply(reply.proposal, scenario, baselineId) ? (
                  <>
                    <label className="check-label">
                      <input
                        type="checkbox"
                        checked={confirmed === reply.id}
                        onChange={(event) =>
                          setConfirmed(event.target.checked ? reply.id : null)
                        }
                      />
                      I reviewed these changes for this exact version. Save a
                      new alternative version.
                    </label>
                    <button
                      className="button primary"
                      disabled={
                        confirmed !== reply.id ||
                        apply.isPending ||
                        applied === reply.id
                      }
                      onClick={() => apply.mutate(reply)}
                    >
                      {apply.isPending
                        ? "Saving…"
                        : applied === reply.id
                          ? "Alternative saved"
                          : "Save reviewed alternative"}
                      <Check size={16} />
                    </button>
                  </>
                ) : (
                  <p>
                    Resolve the listed issues or ask again for the current
                    version before applying this proposal.
                  </p>
                )}
              </section>
            ) : null}
            <details className="assistant-detail">
              <summary>Model, limitations and evidence identity</summary>
              <p>
                {reply.model.provider} · {reply.model.model_id} ·{" "}
                {reply.elapsed_seconds.toFixed(1)} seconds
              </p>
              {reply.limitations.map((item) => (
                <p key={item}>{item}</p>
              ))}
              <code>{reply.content_hash}</code>
            </details>
          </article>
        ))}
      </div>
      <details className="assistant-detail">
        <summary>Assistant availability and allowance</summary>
        <JsonViewer data={status.data} title="Assistant status" />
      </details>
      <Link
        className="text-button"
        to={assistantEvidencePath(
          projectId,
          {
            kind: "evidence",
            id: "project-evidence",
            label: "Research evidence",
          },
          params,
        )}
      >
        Open research evidence <ArrowRight size={14} />
      </Link>
    </div>
  );
}

function AuthoritativeEvidence({
  evidence,
}: {
  evidence: AssistantReply["evidence"][number];
}) {
  const details = evidence.details;
  if (!details) return null;
  const number = (value: unknown) =>
    typeof value === "number" && Number.isFinite(value)
      ? value.toFixed(4)
      : "Unavailable";
  if (typeof details.metric_id === "string")
    return (
      <section className="assistant-tool-evidence">
        <h3>Tool-backed assessment metric</h3>
        <p>
          Assessment location: {String(details.receptor_id)} · Passive tracer ·
          µg/m³ · research preview
        </p>
        <dl>
          <div>
            <dt>Baseline</dt>
            <dd>
              {details.valid === false
                ? "Unavailable"
                : number(details.baseline_ug_m3)}
            </dd>
          </div>
          <div>
            <dt>Alternative</dt>
            <dd>
              {details.valid === false
                ? "Unavailable"
                : number(details.alternative_ug_m3)}
            </dd>
          </div>
          <div>
            <dt>Alternative − baseline</dt>
            <dd>
              {details.valid === false
                ? "Unavailable"
                : number(details.delta_ug_m3)}
            </dd>
          </div>
        </dl>
        {details.protected === true ? (
          <Badge
            tone={
              typeof details.delta_ug_m3 === "number" && details.delta_ug_m3 > 0
                ? "amber"
                : "quiet"
            }
          >
            {typeof details.delta_ug_m3 === "number" && details.delta_ug_m3 > 0
              ? "Protected location worsens in this run"
              : "Protected assessment location"}
          </Badge>
        ) : null}
        <JsonViewer data={details} title="Exact metric identity and support" />
      </section>
    );
  return (
    <details className="assistant-tool-evidence">
      <summary>Tool-backed geometry and source inputs</summary>
      <JsonViewer data={details} title="Scoped input values" />
    </details>
  );
}
