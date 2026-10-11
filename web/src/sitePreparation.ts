import type { Project, Scenario } from "./types";

export interface PreparationContext {
  schema_version: "site-preparation-context-v1";
  project_id: string;
  context_id: string;
  context_hash: string;
  buildings: {
    object_id: string;
    name: string;
    height_m: number | null;
    height_basis: string;
    source_url: string;
    footprint: unknown;
  }[];
  unsupported_objects: {
    object_id: string;
    reason: string;
    requires_exclusion: boolean;
  }[];
  context_only_road_count: number;
  source_snapshot: Record<string, unknown>;
  licence_review: Record<string, unknown>;
  illustrative_study_template: Record<string, unknown>;
  limitations: string[];
}
export interface HeightDraft {
  height: string;
  basis: "assumed" | "osm_tag" | "user_declared_evidence";
  reference: string;
  excluded: boolean;
  reason: string;
}
export interface PreparationPreview {
  preparation_hash: string;
  ready_to_commit: boolean;
  issues: { code: string; message: string; object_id?: string | null }[];
  warnings: string[];
  proposed_scenario: Record<string, unknown> | null;
  source_review: Record<string, unknown>;
  compliance_bundle: Record<string, unknown>;
}
export interface PreparedSite {
  scenario: Scenario;
  project: Project;
  preparation: Record<string, unknown>;
}
export function preparationPayload(
  context: PreparationContext,
  reviews: Record<string, HeightDraft>,
  exclusions: Record<string, string>,
  flat: boolean,
  licence: boolean,
  mode: "geometry_only" | "illustrative_point_study",
  illustrative: boolean,
) {
  const heights = context.buildings
    .filter((b) => !reviews[b.object_id]?.excluded)
    .map((b) => {
      const r = reviews[b.object_id];
      if (
        !r ||
        !r.height.trim() ||
        !Number.isFinite(Number(r.height)) ||
        Number(r.height) < 1 ||
        Number(r.height) > 80 ||
        r.reference.trim().length < 8
      )
        throw new Error(
          `Review ${b.name}: enter a height from 1 to 80 m and an evidence or assumption reference of at least 8 characters.`,
        );
      return {
        object_id: b.object_id,
        height_m: Number(r.height),
        height_basis: r.basis,
        reference: r.reference.trim(),
        units: "m_AGL",
      };
    });
  const excluded = context.buildings
    .filter((b) => reviews[b.object_id]?.excluded)
    .map((b) => ({
      object_id: b.object_id,
      reason: reviews[b.object_id].reason.trim(),
    }));
  for (const b of context.unsupported_objects)
    excluded.push({
      object_id: b.object_id,
      reason: (exclusions[b.object_id] ?? "").trim(),
    });
  if (excluded.some((e) => e.reason.length < 8))
    throw new Error(
      "Every omitted object needs a reason of at least 8 characters. Omissions remain in the review record.",
    );
  if (!flat || !licence)
    throw new Error(
      "Review the flat-ground assumption and local data permissions before continuing.",
    );
  if (mode === "illustrative_point_study" && !illustrative)
    throw new Error(
      "Acknowledge the artificial source and assessment inputs before continuing.",
    );
  return {
    context_id: context.context_id,
    expected_context_hash: context.context_hash,
    heights,
    exclusions: excluded,
    acknowledge_flat_ground: flat,
    licence_review: {
      review_id: "osm-odbl-local-study-v1",
      accept_attribution: licence,
      accept_local_only: licence,
      assumptions_owned_or_permitted: licence,
    },
    study_inputs: {
      mode,
      acknowledge_illustrative: illustrative,
      sources: [],
      receptors: [],
    },
  };
}
