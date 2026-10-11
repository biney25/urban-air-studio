import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Layers, Check } from "lucide-react";
import { api } from "../api";
import type { Comparison, Project, Run, Scenario } from "../types";
import { completedStudyRuns, comparisonForPair, previewMatches, sourceFactors, type ResponseCandidate, type ResponsePreview, type ResponseStudy } from "../responseStudies";
import { Badge, ErrorBox, Loading } from "./UI";
import "./responseStudies.css";

interface Props {
  project: Project;
  scenario?: Scenario;
  runs: Run[];
  onOpenScenario: (id: string) => void;
  onComparePair: (baselineRunId: string, alternativeRunId: string) => void;
}
export default function ResponseStudyPanel(props: Props) {
  return <details className="response-study-panel"><summary><Layers size={18} /><span>Compare source controls</span><Badge>Up to 3 alternatives</Badge></summary>
    <ResponseStudyEditor key={`${props.project.id}:${props.scenario?.id ?? "none"}`} {...props} />
  </details>;
}
function ResponseStudyEditor({ project, scenario, runs, onOpenScenario, onComparePair }: Props) {
  const client = useQueryClient();
  const [name, setName] = useState("Source-control comparison");
  const [percentages, setPercentages] = useState("50, 75, 100");
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [localError, setLocalError] = useState<unknown>();
  const [commitKey, setCommitKey] = useState("");
  const brief = useQuery({ queryKey: ["planning-brief", project.id], queryFn: ({ signal }) => api<{ id: string; content_hash: string } | null>(`/projects/${project.id}/planning-brief`, undefined, signal) });
  const studies = useQuery({ queryKey: ["response-studies", project.id], queryFn: ({ signal }) => api<ResponseStudy[]>(`/projects/${project.id}/response-studies`, undefined, signal), retry: false });
  const preview = useMutation({ mutationFn: (body: Record<string, unknown>) => api<ResponsePreview>(`/projects/${project.id}/response-studies/preview`, body), onSuccess: () => setCommitKey(crypto.randomUUID()) });
  const commit = useMutation({ mutationFn: (body: Record<string, unknown>) => api<ResponseStudy>(`/projects/${project.id}/response-studies`, body), onSuccess: async () => { preview.reset(); await Promise.all([client.invalidateQueries({ queryKey: ["response-studies", project.id] }), client.invalidateQueries({ queryKey: ["project", project.id] })]); } });
  let factors: number[] = [];
  try { factors = sourceFactors(percentages); } catch { /* Presented when the user requests a preview. */ }
  const currentPreview = previewMatches(preview.data, scenario, brief.data?.content_hash, factors, sourceIds, name.trim()) ? preview.data : undefined;
  const eligible = !!scenario && !!brief.data && sourceIds.length > 0 && !!name.trim() && factors.length > 0;
  const prepare = () => {
    setLocalError(undefined);
    try {
      const values = sourceFactors(percentages);
      if (!scenario || !brief.data || !sourceIds.length) throw new Error("Choose a source and save your planning question and requirements first.");
      preview.mutate({ name: name.trim(), scenario_id: scenario.id, expected_version: scenario.version, parent_hash: scenario.content_hash, brief_hash: brief.data.content_hash, source_ids: sourceIds, factors: values });
    } catch (error) { setLocalError(error); }
  };
  return <div className="response-study-content">
    <p>Try a small set of source-rate changes while keeping buildings, assessment locations and study conditions fixed.</p>
    <p className="response-study-notice">Research previews. A lower source rate is an input assumption; it does not establish a real-world pollution reduction or an optimal design.</p>
    {!scenario ? <p>Prepare a baseline in Design before creating alternatives.</p> : <>
      <p className="response-study-parent">Starting design: <strong>{scenario.name}</strong> · version {scenario.version}</p>
      {!brief.data && !brief.isPending ? <p className="response-study-notice">Save the planning question and requirements in Design before creating a constrained comparison.</p> : null}
      <div className="response-study-form">
        <label>Comparison name<input value={name} maxLength={120} onChange={(e) => { setName(e.target.value); preview.reset(); }} /></label>
        <label>Source rates (% of current rate)<input inputMode="decimal" value={percentages} onChange={(e) => { setPercentages(e.target.value); preview.reset(); }} aria-describedby="response-factor-help" /></label>
      </div>
      <p id="response-factor-help" className="quiet">For example: 50, 75, 100. The 100% alternative is an unchanged source-rate control.</p>
      <fieldset className="response-source-choices"><legend>Sources to change</legend>
        {scenario.sources.length ? scenario.sources.map((s) => <label key={s.id}><input type="checkbox" checked={sourceIds.includes(s.id)} onChange={(e) => { setSourceIds((old) => e.target.checked ? [...old, s.id] : old.filter((id) => id !== s.id)); preview.reset(); }} /><span>{s.name}<small>{s.rate_g_s.toLocaleString()} g/s</small></span></label>) : <p>Add a supported point source before comparing source controls.</p>}
      </fieldset>
      <button className="button" disabled={!eligible || preview.isPending || commit.isPending} onClick={prepare}>{preview.isPending ? "Checking alternatives…" : "Preview alternatives"}<ArrowRight size={16} /></button>
      {currentPreview ? <section className="response-confirmation" aria-label="Alternative preview">
        <h3>Review before saving</h3><p>No study runs have started. Each alternative will be a separate saved design.</p>
        <div className="response-candidate-grid">{currentPreview.candidates.map((c) => <CandidateInputs key={c.factor} candidate={c} scenario={scenario} />)}</div>
        <button className="button primary" disabled={commit.isPending || !currentPreview.candidates.every((c) => c.validation.valid)} onClick={() => commit.mutate({ preview_id: currentPreview.id, preview_hash: currentPreview.content_hash, idempotency_key: commitKey })}><Check size={16} />{commit.isPending ? "Saving alternatives…" : "Save these alternatives"}</button>
      </section> : null}
    </>}
    {localError || preview.error || commit.error || brief.error ? <ErrorBox error={localError || preview.error || commit.error || brief.error} /> : null}
    {commit.isSuccess ? <p role="status">Alternatives saved. Open each design to review and start its study.</p> : null}
    <div className="response-saved-studies"><h3>Saved source comparisons</h3>
      {studies.isPending ? <Loading label="Opening saved comparisons…" /> : studies.error ? <ErrorBox error={studies.error} onRetry={() => void studies.refetch()} /> : !studies.data?.length ? <p className="quiet">Your saved alternatives and their study results will appear here.</p> : studies.data.map((study) => <section className="response-saved-study" key={study.id}><h4>{study.name}</h4><div className="response-candidate-grid">{study.candidates.map((c) => <SavedCandidate key={c.scenario_id} candidate={c} study={study} project={project} runs={runs} onOpenScenario={onOpenScenario} onComparePair={onComparePair} />)}</div></section>)}
    </div>
  </div>;
}
function CandidateInputs({ candidate, scenario }: { candidate: ResponseCandidate; scenario: Scenario }) {
  return <article className="response-candidate"><h4>{(candidate.factor * 100).toLocaleString(undefined, { maximumFractionDigits: 10 })}% source rate</h4>
    {candidate.unchanged_control ? <Badge>Unchanged control</Badge> : null}
    <ul>{candidate.source_rates.map((rate) => { const original = scenario.sources.find((s) => s.id === rate.source_id); return <li key={rate.source_id}>{original?.name ?? rate.source_id}<span>{original?.rate_g_s ?? rate.before_g_s} → {rate.after_g_s ?? rate.rate_g_s ?? (original ? original.rate_g_s * candidate.factor : "unavailable")} g/s</span></li>; })}</ul>
    <Badge tone={candidate.validation.valid ? "cyan" : "amber"}>{candidate.validation.valid ? "Geometry requirements pass" : "Review requirements"}</Badge>
    {candidate.validation.issues?.map((issue, i) => <p key={i}>{issue.message}</p>)}
  </article>;
}
function SavedCandidate({ candidate, study, project, runs, onOpenScenario, onComparePair }: { candidate: ResponseCandidate; study: ResponseStudy; project: Project; runs: Run[]; onOpenScenario: Props["onOpenScenario"]; onComparePair: Props["onComparePair"] }) {
  const baseRuns = completedStudyRuns(runs, study.parent_scenario_id);
  const altRuns = completedStudyRuns(runs, candidate.scenario_id ?? "");
  const [base, setBase] = useState("");
  const [alternative, setAlternative] = useState("");
  const compare = useMutation({ mutationFn: () => api<{ comparison: Comparison }>(`/projects/${project.id}/response-studies/${study.id}/compare`, { candidate_scenario_id: candidate.scenario_id, baseline_run_id: base, alternative_run_id: alternative }) });
  const exact = comparisonForPair(compare.data?.comparison, base, alternative);
  return <article className={`response-candidate${exact ? " response-candidate-completed" : ""}`}><h4>{(candidate.factor * 100).toLocaleString(undefined, { maximumFractionDigits: 10 })}% source rate</h4>
    {candidate.unchanged_control ? <Badge>Unchanged control</Badge> : null}
    <p>{altRuns.length ? `${altRuns.length} completed research ${altRuns.length === 1 ? "study" : "studies"}` : "Study not completed"}</p>
    <button className="button" disabled={!candidate.scenario_id} onClick={() => candidate.scenario_id && onOpenScenario(candidate.scenario_id)}>Open design<ArrowRight size={16} /></button>
    {baseRuns.length && altRuns.length ? <div className="response-pair-selection">
      <label>Starting-design study<select value={base} onChange={(e) => { setBase(e.target.value); compare.reset(); }}><option value="">Choose a study</option>{baseRuns.map((r) => <option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString()} · {r.id.slice(-8)}</option>)}</select></label>
      <label>Alternative study<select value={alternative} onChange={(e) => { setAlternative(e.target.value); compare.reset(); }}><option value="">Choose a study</option>{altRuns.map((r) => <option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString()} · {r.id.slice(-8)}</option>)}</select></label>
      <button className="button" disabled={!base || !alternative || compare.isPending} onClick={() => compare.mutate()}>{compare.isPending ? "Checking pair…" : "Compare this pair"}</button>
    </div> : <p className="quiet">Run the starting design and this alternative under the same conditions before comparing.</p>}
    {compare.error ? <ErrorBox error={compare.error} /> : null}
    {exact ? <><div className="table-wrap" tabIndex={0} role="region" aria-label="Source-control comparison results"><table><caption>Actual preview differences · alternative − starting design</caption><thead><tr><th scope="col">Location</th><th scope="col">Δ µg/m³</th></tr></thead><tbody>{exact.metrics.map((m) => <tr key={m.receptor_id}><th scope="row">{m.name}{m.protected ? <small>Protected</small> : null}</th><td>{m.delta_ug_m3 === null ? "No valid sample" : m.delta_ug_m3.toPrecision(4)}</td></tr>)}</tbody></table></div><p className="quiet">Positive differences worsen concentration in this diagnostic. Real-world benefit remains unqualified.</p><button className="button" onClick={() => onComparePair(base, alternative)}>Open full comparison<ArrowRight size={16} /></button></> : null}
  </article>;
}
