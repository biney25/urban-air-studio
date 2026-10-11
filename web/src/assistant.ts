import type { Operation, Scenario } from "./types";
import { workflowPath } from "./workflowContext";
export interface AssistantStatus {
  available: boolean;
  state: "ready" | "unavailable" | "busy";
  message: string;
  model: { provider: "ollama" | "gemini"; model_id: string };
  limits: Record<string, unknown>;
  reason_code?: string | null;
}
export interface AssistantProposal {
  scenario_id: string;
  expected_version: number;
  input_hash: string;
  operations: Operation[];
  validation: {
    valid: boolean;
    geometry_valid: boolean;
    programme_status: string;
    issues: Record<string, unknown>[];
  };
  requires_confirmation: true;
}
export interface AssistantReply {
  id: string;
  session_id: string;
  status: "complete" | "blocked" | "unavailable";
  answer: string;
  evidence: {
    kind:
      "project" | "scenario" | "run" | "comparison" | "proposal" | "evidence";
    id: string;
    label: string;
    details?: Record<string, unknown> | null;
  }[];
  tool_calls: {
    name: string;
    status: "complete" | "blocked" | "failed";
    evidence_ids: string[];
  }[];
  proposal: AssistantProposal | null;
  limitations: string[];
  model: { provider: "ollama" | "gemini"; model_id: string };
  elapsed_seconds: number;
  content_hash: string;
}
export function proposalCanApply(
  proposal: AssistantProposal,
  scenario: Scenario | undefined,
  baselineId: string | null,
) {
  return (
    !!scenario &&
    scenario.id !== baselineId &&
    proposal.scenario_id === scenario.id &&
    proposal.expected_version === scenario.version &&
    proposal.input_hash === scenario.content_hash &&
    proposal.validation.valid &&
    proposal.validation.geometry_valid &&
    proposal.operations.length > 0
  );
}
export function assistantEvidencePath(
  projectId: string,
  evidence: AssistantReply["evidence"][number],
  searchParams?: URLSearchParams,
) {
  if (evidence.kind === "scenario")
    return workflowPath(
      projectId,
      `scenarios/${encodeURIComponent(evidence.id)}`,
      searchParams,
      { scenario: searchParams ? evidence.id : undefined },
    );
  if (evidence.kind === "run")
    return workflowPath(
      projectId,
      `runs/${encodeURIComponent(evidence.id)}`,
      searchParams,
      { run: searchParams ? evidence.id : undefined },
    );
  const base = evidence.details?.baseline_run_id,
    alt = evidence.details?.alternative_run_id;
  if (typeof base === "string" && typeof alt === "string")
    return workflowPath(projectId, "compare", searchParams, { base, alt });
  return workflowPath(projectId, "evidence", searchParams);
}
