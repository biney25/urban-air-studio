import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Box,
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  Download,
  FileText,
  FlaskConical,
  FolderOpen,
  GitBranch,
  Layers,
  LockKeyhole,
  MapPin,
  Plus,
  RotateCcw,
  Undo2,
  Redo2,
  Upload,
  Wind,
} from "lucide-react";
import { api, downloadJson, downloadProtected, resultApi } from "./api";
import { AuthStatus } from "./components/AuthGate";
import { comparisonMatchesSelection, matchedSliceIssue } from "./comparison";
import { gatedViews, projectView } from "./viewState";
import type {
  Comparison,
  Conditions,
  GeoJSON,
  ImportRecord,
  Operation,
  Project,
  ProjectDetail,
  Quote,
  Report,
  Result,
  Run,
  Scenario,
  Selection,
} from "./types";
import {
  fieldMaximum,
  formatValue,
  isActive,
  isSynthetic,
  polygonArea,
  shortId,
} from "./geometry";
import SceneView, { SvgScene } from "./components/SceneView";
import {
  Badge,
  CheckRow,
  Empty,
  ErrorBox,
  EvidenceNote,
  JsonViewer,
  Loading,
  NumericInput,
  SectionHeading,
} from "./components/UI";
import ObjectInspector from "./components/ObjectInspector";
import EvidenceWorkspace from "./components/EvidenceWorkspace";
const DEFAULT_CONDITIONS: Conditions = {
  wind_speed_m_s: 3,
  wind_from_deg: 270,
  diffusivity_m2_s: 3,
  background_ug_m3: 0,
  duration_s: 120,
};
const laterCapabilities = [
  [
    "AI planner",
    "Stage C",
    "Bounded Strands tools and evidence evaluation are required.",
  ],
  [
    "Optimisation",
    "Stage C",
    "A qualified parent model, constraints and confirmation studies are required.",
  ],
  [
    "Field validation",
    "Stage B",
    "Independent benchmark and measured local campaign evidence are required.",
  ],
  [
    "Review & sharing",
    "Stage D",
    "Production identity, scoped access and review policy are required.",
  ],
  [
    "City & process expansion",
    "Stage E",
    "New-site, seasonal and specialised-process qualification is required.",
  ],
];
export default function Studio() {
  const { projectId = "" } = useParams();
  const location = useLocation(),
    navigate = useNavigate(),
    client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const { sub, raw: rawView, view } = projectView(location.pathname);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [redo, setRedo] = useState<string[]>([]);
  const [branchName, setBranchName] = useState("Alternative A");
  const [actionError, setActionError] = useState<unknown>(null);
  const [conditions, setConditions] = useState<Conditions>(DEFAULT_CONDITIONS);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [reportTitle, setReportTitle] = useState(
    "Urban design comparison — draft",
  );
  const detail = useQuery({
    queryKey: ["project", projectId],
    queryFn: ({ signal }) =>
      api<ProjectDetail>(`/projects/${projectId}`, undefined, signal),
    refetchInterval: (q) =>
      q.state.data?.runs.some((r) => isActive(r.status)) ? 1000 : false,
  });
  const fallbackScenario =
    detail.data?.project.baseline_scenario_id ??
    detail.data?.scenarios.at(-1)?.id;
  const scenarioId =
    (rawView === "scenarios" ? sub[1] : params.get("scenario")) ??
    fallbackScenario ??
    "";
  const scenarioQuery = useQuery({
    queryKey: ["scenario", scenarioId],
    queryFn: ({ signal }) =>
      api<Scenario>(`/scenarios/${scenarioId}`, undefined, signal),
    enabled: !!scenarioId,
    initialData: () => detail.data?.scenarios.find((s) => s.id === scenarioId),
  });
  const scenario = scenarioQuery.data;
  const baseline = detail.data?.scenarios.find(
    (s) => s.id === detail.data?.project.baseline_scenario_id,
  );
  const completed =
    detail.data?.runs.filter((r) => r.status === "complete_unqualified") ?? [];
  const defaultRun = [...completed]
    .reverse()
    .find((r) => r.scenario_id === scenarioId);
  const runId =
    (view === "result" ? sub[1] : params.get("run")) ?? defaultRun?.id ?? "";
  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: ({ signal }) => api<Run>(`/runs/${runId}`, undefined, signal),
    enabled: !!runId,
    refetchInterval: (q) =>
      q.state.data && isActive(q.state.data.status) ? 750 : false,
  });
  const resultQuery = useQuery({
    queryKey: ["result", runId],
    queryFn: ({ signal }) => resultApi(runId, signal),
    enabled: !!runId && runQuery.data?.status === "complete_unqualified",
  });
  const resultScenario =
    detail.data?.scenarios.find((s) => s.id === runQuery.data?.scenario_id) ??
    scenario;
  const baseRunId =
    params.get("base") ??
    [...completed].reverse().find((r) => r.scenario_id === baseline?.id)?.id ??
    "";
  const alternativeRunId =
    params.get("alt") ??
    [...completed].reverse().find((r) => r.scenario_id !== baseline?.id)?.id ??
    baseRunId;
  const comparison = useMutation({
    mutationFn: (pair: {
      projectId: string;
      baselineRunId: string;
      alternativeRunId: string;
    }) =>
      api<Comparison>(`/projects/${pair.projectId}/compare`, {
        baseline_run_id: pair.baselineRunId,
        alternative_run_id: pair.alternativeRunId,
      }),
  });
  const comparisonData = comparisonMatchesSelection(
    comparison.data,
    comparison.variables?.projectId,
    projectId,
    baseRunId,
    alternativeRunId,
  )
    ? comparison.data
    : undefined;
  const baseField = useQuery({
    queryKey: ["result", baseRunId],
    queryFn: ({ signal }) => resultApi(baseRunId, signal),
    enabled: !!comparisonData && view === "compare",
  });
  const alternativeField = useQuery({
    queryKey: ["result", alternativeRunId],
    queryFn: ({ signal }) => resultApi(alternativeRunId, signal),
    enabled: !!comparisonData && view === "compare",
  });
  const resetComparison = comparison.reset;
  useEffect(() => {
    resetComparison();
  }, [projectId, location.pathname, location.search, resetComparison]);
  useEffect(() => {
    setQuote(null);
    setRedo([]);
    setSelected(null);
    setActionError(null);
  }, [projectId]);
  const reportId =
    rawView === "reports"
      ? (sub[1] ?? params.get("report") ?? "")
      : (params.get("report") ?? "");
  const reportQuery = useQuery({
    queryKey: ["report", reportId],
    queryFn: ({ signal }) =>
      api<Report>(`/reports/${reportId}`, undefined, signal),
    enabled: !!reportId,
  });
  const path = (next: string, sid?: string) =>
    `/projects/${projectId}/${next}${next.includes("?") ? "" : sid ? `?scenario=${sid}` : scenarioId ? `?scenario=${scenarioId}` : ""}`;
  const chooseScenario = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("scenario", id);
    setParams(next);
    if (rawView === "scenarios")
      navigate(`/projects/${projectId}/scenarios/${id}`);
    setActionError(null);
  };
  const go = (next: string) => navigate(path(next));
  const changed = async (s: Scenario) => {
    client.setQueryData(["scenario", s.id], s);
    await client.invalidateQueries({ queryKey: ["project", projectId] });
    setRedo([]);
    navigate(`/projects/${projectId}/scenarios/${s.id}`);
    setActionError(null);
    setQuote(null);
  };
  const operation = useMutation({
    mutationFn: (operations: Operation[]) =>
      api<Scenario>(`/scenarios/${scenarioId}/operations`, {
        expected_version: scenario?.version,
        operations,
      }),
    onSuccess: changed,
    onError: setActionError,
  });
  const branch = useMutation({
    mutationFn: () =>
      api<Scenario>(`/scenarios/${scenarioId}/branch`, {
        name: branchName.trim(),
      }),
    onSuccess: changed,
    onError: setActionError,
  });
  const establish = useMutation({
    mutationFn: () =>
      api<Project>(`/projects/${projectId}/baseline`, {
        scenario_id: scenarioId,
      }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["project", projectId] });
      go("design");
    },
    onError: setActionError,
  });
  const quoteMutation = useMutation({
    mutationFn: () =>
      api<Quote>(`/projects/${projectId}/quote`, {
        scenario_ids: Array.from(
          new Set(
            [baseline?.id, scenarioId].filter((id): id is string => !!id),
          ),
        ),
        conditions,
        tier: "T1",
      }),
    onSuccess: setQuote,
    onError: setActionError,
  });
  const submit = useMutation({
    mutationFn: () =>
      api<{ runs: Run[] }>(`/projects/${projectId}/runs`, {
        quote_id: quote?.id,
        idempotency_key: `quote-${quote?.id}`,
      }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["project", projectId] });
      setQuote(null);
      go("runs");
    },
    onError: setActionError,
  });
  const runAction = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "cancel" | "retry" }) =>
      api<Run>(`/runs/${id}/${action}`, {}),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["project", projectId] });
      await client.invalidateQueries({ queryKey: ["run"] });
    },
    onError: setActionError,
  });
  const makeReport = useMutation({
    mutationFn: () => {
      if (!comparisonData)
        throw new Error("Check this exact run pair before creating a report.");
      return api<Report>(`/projects/${projectId}/reports`, {
        baseline_run_id: comparisonData.baseline_run_id,
        alternative_run_id: comparisonData.alternative_run_id,
        title: reportTitle,
      });
    },
    onSuccess: (r) => {
      client.setQueryData(["report", r.id], r);
      if (r.project_id === projectId)
        navigate(`/projects/${projectId}/reports/${r.id}`);
    },
    onError: setActionError,
  });
  useEffect(() => {
    if (!scenario) return;
    const stillExists =
      selected &&
      (selected.kind === "building"
        ? scenario.buildings
        : selected.kind === "source"
          ? scenario.sources
          : scenario.receptors
      ).some((o) => o.id === selected.id);
    if (!stillExists)
      setSelected(
        scenario.buildings[0]
          ? { kind: "building", id: scenario.buildings[0].id }
          : null,
      );
  }, [scenario, selected]);
  useEffect(() => {
    setActionError(null);
  }, [view]);
  const setCondition = (key: keyof Conditions, value: number) => {
    setConditions((s) => ({ ...s, [key]: value }));
    setQuote(null);
  };
  if (detail.isPending)
    return <Loading label="Opening your planning workspace…" />;
  if (detail.error || !detail.data)
    return (
      <div className="page-error">
        <Link to="/projects" className="text-button">
          <ArrowLeft size={16} />
          Projects
        </Link>
        <ErrorBox error={detail.error} onRetry={() => void detail.refetch()} />
      </div>
    );
  const project = detail.data.project;
  const isBaseline = scenarioId === project.baseline_scenario_id;
  const executedContextScenario = detail.data.scenarios.find(s => s.id === runQuery.data?.scenario_id);
  const busy = operation.isPending || branch.isPending;
  const area =
    scenario?.buildings.reduce((sum, b) => sum + polygonArea(b.footprint), 0) ??
    0;
  const ancestors: Scenario[] = [];
  let cursor = scenario;
  const seen = new Set<string>();
  while (cursor && !seen.has(cursor.id)) {
    ancestors.push(cursor);
    seen.add(cursor.id);
    cursor = detail.data.scenarios.find((s) => s.id === cursor?.parent_id);
  }
  const selectObject = (s: Selection) => {
    setSelected(s);
  };
  const tabList =
    view === "explore" ||
    view === "data" ||
    view === "imports" ||
    view === "baseline"
      ? [
          ["explore", "Site explorer"],
          ["data", "Data audit"],
          ["imports", "Import"],
          ["baseline", "Baseline"],
        ]
      : view === "design" || view === "sources" || view === "receptors"
        ? [
            ["design", "Design editor"],
            ["sources", "Sources"],
            ["receptors", "Receptors"],
          ]
        : view === "study" ||
            view === "runs" ||
            view === "result" ||
            view === "compare" ||
            view === "reports" ||
            view === "exports" ||
            view === "evidence"
          ? [
              ["study", "Study setup"],
              ["runs", "Run centre"],
              ["compare", "Compare"],
              ["reports", "Reports"],
              ["exports", "Export"],
              ["evidence", "Evidence"],
            ]
          : [["investigate", "Capability roadmap"]];
  return (
    <div
      className={`studio-app ${scenario && (["explore", "design", "sources", "receptors"].includes(view) || (view === "result" && resultQuery.data)) ? "spatial-workspace" : ""}`}
    >
      <header className="studio-header">
        <Link className="brand compact" to="/projects" aria-label="Urban Air Studio projects">
          <span className="brand-mark">
            <Wind size={20} />
          </span>
          <span>
            URBAN AIR{" "}<span>STUDIO</span>
          </span>
        </Link>
        <span className="header-separator" />
        <Link className="project-title" to="/projects">
          <FolderOpen size={15} />
          {project.name}
          <ChevronDown size={13} />
        </Link>
        <div className="header-context">
          <span>Passive tracer</span>
          <span>µg/m³</span>
          <Badge tone="amber">T1 · research preview</Badge>
          <AuthStatus />
        </div>
      </header>
      <aside className="workspace-rail" aria-label="Workspace navigation">
        {[
          {
            id: "explore",
            icon: MapPin,
            label: "Explore",
            group: ["explore", "data", "imports", "baseline"],
          },
          {
            id: "design",
            icon: Box,
            label: "Design",
            group: ["design", "sources", "receptors"],
          },
          {
            id: "compare",
            icon: BarChart3,
            label: "Compare",
            group: ["study", "runs", "result", "compare", "reports", "exports", "evidence"],
          },
          {
            id: "investigate",
            icon: FlaskConical,
            label: "Investigate",
            group: ["investigate"],
          },
        ].map((n) => (
          <button
            key={n.id}
            className={n.group.includes(view) ? "active" : ""}
            onClick={() => go(n.id)}
            title={n.label}
            aria-label={`${n.label} workspace`}
            aria-current={n.group.includes(view) ? "page" : undefined}
          >
            <n.icon size={21} />
            <span>{n.label}</span>
          </button>
        ))}
        <Link to="/projects" title="Projects" aria-label="Projects">
          <FolderOpen size={20} />
        </Link>
        <span className="rail-version">A / 01</span>
      </aside>
      <div className="studio-body">
        <div className="compact-review-note">Compact review layout · expand the window for a larger modelling canvas.</div>
        <nav className="task-nav" aria-label="Task views">
          {tabList.map(([id, label]) => (
            <button
              key={id}
              onClick={() => go(id)}
              className={view === id ? "active" : ""}
              aria-current={view === id ? "page" : undefined}
            >
              {label}
            </button>
          ))}
          <span className="task-nav-tail" data-testid="snapshot-context">
            {view === "result" ? (
              runQuery.data ? <>
                <span className="violet-dot" />
                Executed result · {runQuery.data.scenario_id === project.baseline_scenario_id ? "Baseline" : "Alternative"}
                {executedContextScenario ? ` · v${executedContextScenario.version}` : ""}
                {` · ${shortId(runQuery.data.scenario_id)}`}
              </> : <>Executed result · {runQuery.error ? "snapshot unavailable" : "loading snapshot"}</>
            ) : scenario ? (
              <>
                <span className="violet-dot" />
                {isBaseline ? "Baseline" : "Alternative"} · v{scenario.version}
              </>
            ) : (
              <>No baseline</>
            )}
          </span>
        </nav>
        <main id="main-content" className="workspace-main">
          {actionError ? <ErrorBox error={actionError} /> : null}
          {view === "evidence" ? <EvidenceWorkspace key={projectId} projectId={projectId} scenario={scenario} scenarios={detail.data.scenarios} onScenario={id => navigate(path("evidence", id))} /> : null}
          {view === "imports" ? (
            <ImportView
              projectId={projectId}
              imports={detail.data.imports}
              onImported={async (s) => {
                client.setQueryData(["scenario", s.id], s);
                await client.invalidateQueries({
                  queryKey: ["project", projectId],
                });
                navigate(path("baseline", s.id));
              }}
            />
          ) : null}
          {view === "data" ? (
            <DataAudit
              scenario={scenario}
              imports={detail.data.imports}
              onImport={() => go("imports")}
            />
          ) : null}
          {view === "baseline" ? (
            <div className="content-page">
              <SectionHeading
                eyebrow="ESTABLISH THE REFERENCE"
                title="Baseline review"
                description="Freeze an audited input version. A baseline is geometry and assumptions, not a validated air-quality result."
              />
              {scenario ? (
                <div className="baseline-layout">
                  <div>
                    <EvidenceNote
                      synthetic={isSynthetic(scenario)}
                      illustrative={scenario.evidence_kind === "illustrative"}
                    />
                    <CheckRow
                      label="Coordinate frame declared"
                      detail={`WGS84 origin ${scenario.origin.longitude.toFixed(5)}, ${scenario.origin.latitude.toFixed(5)} → local ENU metres`}
                    />
                    <CheckRow
                      label="Immutable geometry snapshot"
                      detail={`${scenario.buildings.length} buildings · ${scenario.sources.length} sources · ${scenario.receptors.length} receptors`}
                    />
                    <CheckRow
                      label="Scientific status"
                      detail="Numerical preview only. Independent site validation has not been performed."
                      good={false}
                    />
                    <JsonViewer
                      data={scenario.diagnostics}
                      title="Geometry diagnostics"
                    />
                    <JsonViewer
                      data={{
                        scenario_id: scenario.id,
                        content_hash: scenario.content_hash,
                        origin: scenario.origin,
                        evidence_kind: scenario.evidence_kind,
                      }}
                      title="Baseline input manifest"
                    />
                    <button
                      data-testid="establish-baseline"
                      className="button primary"
                      disabled={establish.isPending || isBaseline}
                      onClick={() => establish.mutate()}
                    >
                      {isBaseline ? (
                        <>
                          <Check size={16} />
                          Current baseline
                        </>
                      ) : (
                        <>
                          Establish baseline
                          <ArrowRight size={16} />
                        </>
                      )}
                    </button>
                  </div>
                  <div className="baseline-scene">
                    <SceneView
                      scenario={scenario}
                      selected={selected}
                      onSelect={selectObject}
                      scaleMax={1}
                    />
                  </div>
                </div>
              ) : (
                <Empty
                  title="Import a reference first"
                  action={
                    <button
                      className="button primary"
                      onClick={() => go("imports")}
                    >
                      Open import
                      <ArrowRight size={15} />
                    </button>
                  }
                >
                  An audited geometry import is needed before establishing a
                  baseline.
                </Empty>
              )}
            </div>
          ) : null}
          {["explore", "design", "sources", "receptors"].includes(view) ? (
            scenario ? (
              <>
                <div className="design-context">
                  <div>
                    <span className="eyebrow">
                      {view === "explore"
                        ? "SITE EXPLORER"
                        : "SCENARIO WORKBENCH"}
                    </span>
                    <h1>{scenario.name}</h1>
                  </div>
                  <div className="scenario-controls">
                    <label>
                      Version
                      <select
                        data-testid="scenario-select"
                        value={scenarioId}
                        onChange={(e) => chooseScenario(e.target.value)}
                      >
                        {detail.data.scenarios.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name} · v{s.version} · {shortId(s.id)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <Badge tone={isBaseline ? "violet" : "cyan"}>
                      {isBaseline ? "Frozen baseline" : "Immutable alternative"}
                    </Badge>
                  </div>
                </div>
                <EvidenceNote
                  synthetic={isSynthetic(scenario)}
                  illustrative={scenario.evidence_kind === "illustrative"}
                />
                {defaultRun ? null : (
                  <div className="stale-strip">
                    <Activity size={14} />
                    {isBaseline
                      ? "This baseline has no completed study."
                      : "Current version has no completed result. Earlier fields cannot apply to these edits."}
                    <button onClick={() => go("study")}>
                      Set up study
                      <ArrowRight size={13} />
                    </button>
                  </div>
                )}
                <div className="scene-layout">
                  <SceneView
                    scenario={scenario}
                    selected={selected}
                    onSelect={selectObject}
                    scaleMax={1}
                  />
                  <aside className="inspector">
                    <div className="inspector-top">
                      <span className="eyebrow">
                        {view === "sources"
                          ? "SOURCE INVENTORY"
                          : view === "receptors"
                            ? "RECEPTOR SET"
                            : view === "explore"
                              ? "SITE CONTENTS"
                              : "OBJECT INSPECTOR"}
                      </span>
                      <span>{shortId(scenario.id)}</span>
                    </div>
                    {view === "explore" ? (
                      <>
                        <div className="inspector-heading">
                          <h2>Evidence coverage</h2>
                          <p>
                            Geometry imported. Observational providers are not
                            connected.
                          </p>
                        </div>
                        <div className="coverage-item">
                          <span className="violet-dot" />
                          <strong>Semantic geometry</strong>
                          <Badge tone="violet">Available</Badge>
                        </div>
                        <div className="coverage-item">
                          <span className="mask-dot" />
                          <strong>Measured air quality</strong>
                          <Badge>Unavailable</Badge>
                        </div>
                        <div className="coverage-item">
                          <span className="mask-dot" />
                          <strong>Site validation</strong>
                          <Badge>Stage B</Badge>
                        </div>
                        <button className="button" onClick={() => go("data")}>
                          Inspect data audit
                          <ArrowRight size={15} />
                        </button>
                      </>
                    ) : (
                      <>
                        <div className="branch-form">
                          <label className="form-label">
                            Alternative name
                            <input
                              data-testid="branch-name"
                              value={branchName}
                              onChange={(e) => setBranchName(e.target.value)}
                              maxLength={100}
                            />
                          </label>
                          <button
                            data-testid="clone-scenario"
                            className="button"
                            disabled={busy || !branchName.trim()}
                            onClick={() => branch.mutate()}
                          >
                            <GitBranch size={15} />
                            Clone this version
                          </button>
                        </div>
                        {isBaseline ? (
                          <p className="inspector-message">
                            <LockKeyhole size={15} />
                            The baseline is frozen. Clone an alternative to
                            edit.
                          </p>
                        ) : null}
                        <ObjectInspector
                          key={`${scenario.id}-${selected?.id}`}
                          scenario={scenario}
                          selected={selected}
                          disabled={busy || isBaseline}
                          onOperation={(op) => operation.mutate([op])}
                        />
                      </>
                    )}
                    <ObjectList
                      scenario={scenario}
                      selected={selected}
                      onSelect={selectObject}
                      filter={
                        view === "sources"
                          ? "source"
                          : view === "receptors"
                            ? "receptor"
                            : undefined
                      }
                    />
                  </aside>
                </div>
                <div className="bottom-dock">
                  <div className="dock-metric">
                    <span>Building footprint</span>
                    <strong>
                      {formatValue(area, 0)}
                      <small>m²</small>
                    </strong>
                  </div>
                  <div className="dock-metric">
                    <span>Source rate</span>
                    <strong>
                      {formatValue(
                        scenario.sources.reduce(
                          (sum, s) => sum + s.rate_g_s,
                          0,
                        ),
                        3,
                      )}
                      <small>g/s</small>
                    </strong>
                  </div>
                  <div className="dock-history">
                    <div>
                      <span className="eyebrow">VERSION HISTORY</span>
                      <div className="history-controls">
                        <button
                          aria-label="Undo to parent version"
                          data-testid="undo-edit"
                          disabled={!scenario.parent_id || isBaseline || busy}
                          onClick={() => {
                            setRedo((s) => [scenario.id, ...s]);
                            chooseScenario(scenario.parent_id!);
                          }}
                        >
                          <Undo2 size={15} />
                        </button>
                        <button
                          aria-label="Redo selected version"
                          data-testid="redo-edit"
                          disabled={!redo.length || busy}
                          onClick={() => {
                            const next = redo[0];
                            setRedo((s) => s.slice(1));
                            chooseScenario(next);
                          }}
                        >
                          <Redo2 size={15} />
                        </button>
                      </div>
                    </div>
                    <div className="history-items">
                      {ancestors
                        .slice(0, 5)
                        .reverse()
                        .map((s) => (
                          <button
                            key={s.id}
                            onClick={() => chooseScenario(s.id)}
                            className={s.id === scenarioId ? "current" : ""}
                          >
                            v{s.version}
                            <span>{shortId(s.id)}</span>
                          </button>
                        ))}
                    </div>
                  </div>
                  <button
                    className="button primary"
                    onClick={() => go("study")}
                  >
                    Set up a study
                    <ArrowRight size={16} />
                  </button>
                </div>
              </>
            ) : (
              <Empty
                title="Bring a site into the studio"
                action={
                  <button
                    className="button primary"
                    onClick={() => go("imports")}
                  >
                    <Upload size={16} />
                    Import geometry
                  </button>
                }
              >
                Import your own bounded area or start with the synthetic
                reference. Geometry, sources and receptors establish the
                workspace.
              </Empty>
            )
          ) : null}
          {view === "study" ? (
            <div className="content-page">
              <SectionHeading
                eyebrow="FREEZE A COMPARABLE EXPERIMENT"
                title="Study setup"
                description="One shared set of conditions for the baseline and selected alternative. Only the bounded T1 passive-tracer method is available."
              />
              {baseline && scenario ? (
                <div className="study-layout">
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      setActionError(null);
                      quoteMutation.mutate();
                    }}
                    className="study-form"
                  >
                    <div className="study-pair">
                      <div>
                        <span>BASELINE</span>
                        <strong>{baseline.name}</strong>
                        <small>
                          v{baseline.version} · {shortId(baseline.id)}
                        </small>
                      </div>
                      <ChevronRight size={20} />
                      <div>
                        <span>ALTERNATIVE</span>
                        <select
                          aria-label="Study alternative"
                          value={scenarioId}
                          onChange={(e) => {
                            chooseScenario(e.target.value);
                            setQuote(null);
                          }}
                        >
                          {detail.data.scenarios.map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.name} · v{s.version}
                            </option>
                          ))}
                        </select>
                        <small>
                          {isBaseline
                            ? "Same input: no-change invariant study"
                            : `v${scenario.version} · ${shortId(scenario.id)}`}
                        </small>
                      </div>
                    </div>
                    <div className="form-grid">
                      <NumericInput
                        label="Wind speed"
                        value={conditions.wind_speed_m_s}
                        min={0.5}
                        max={10}
                        step="any"
                        unit="m/s"
                        id="wind-speed"
                        onChange={(v) => setCondition("wind_speed_m_s", v)}
                      />
                      <NumericInput
                        label="Wind comes from"
                        value={conditions.wind_from_deg}
                        min={0}
                        max={360}
                        unit="° N"
                        id="wind-from"
                        onChange={(v) => setCondition("wind_from_deg", v)}
                      />
                      <NumericInput
                        label="Diffusivity"
                        value={conditions.diffusivity_m2_s}
                        min={0.1}
                        max={30}
                        step="any"
                        unit="m²/s"
                        id="diffusivity"
                        onChange={(v) => setCondition("diffusivity_m2_s", v)}
                      />
                      <NumericInput
                        label="Inflow background"
                        value={conditions.background_ug_m3}
                        min={0}
                        max={5000}
                        step="any"
                        unit="µg/m³"
                        id="background"
                        onChange={(v) => setCondition("background_ug_m3", v)}
                      />
                      <NumericInput
                        label="Transport duration"
                        value={conditions.duration_s}
                        min={10}
                        max={600}
                        step="any"
                        unit="s"
                        id="duration"
                        onChange={(v) => setCondition("duration_s", v)}
                      />
                    </div>
                    <div className="form-note">
                      <Wind size={17} />
                      <span>
                        Meteorological wind direction is where flow comes from.
                        No weather provider or measured boundary conditions are
                        implied.
                      </span>
                    </div>
                    <button
                      data-testid="quote-study"
                      type="submit"
                      className="button"
                      disabled={quoteMutation.isPending}
                    >
                      {quoteMutation.isPending
                        ? "Checking resources…"
                        : "Check & freeze allowance"}
                      <ArrowRight size={16} />
                    </button>
                  </form>
                  <aside className="study-summary">
                    <span className="eyebrow">METHOD & RESOURCE ALLOWANCE</span>
                    <h2>
                      Bounded transport
                      <br />
                      research preview
                    </h2>
                    <Badge tone="amber">T1 · no local validation</Badge>
                    <p>
                      Geometry affects the numerical transport domain. This
                      simplified method is not RANS CFD, an official AQI or a
                      health assessment.
                    </p>
                    {quote ? (
                      <>
                        <div className="quote-details">
                          <div>
                            <span>Queued cases</span>
                            <strong>{quote.cases}</strong>
                          </div>
                          <div>
                            <span>Local maximum</span>
                            <strong>{quote.maximum_seconds} s</strong>
                          </div>
                        </div>
                        <p className="micro">
                          Resource allowance, not a bill or a measured
                          turnaround promise. Expires{" "}
                          {new Date(quote.expires_at).toLocaleTimeString(
                            "en-IN",
                          )}
                          .
                        </p>
                        {quote.limitations.map((l, i) => (
                          <p className="limitation" key={i}>
                            {l}
                          </p>
                        ))}
                        <button
                          data-testid="submit-study"
                          className="button primary"
                          disabled={submit.isPending}
                          onClick={() => submit.mutate()}
                        >
                          {submit.isPending
                            ? "Submitting…"
                            : "Queue reproducible study"}
                          <ArrowRight size={16} />
                        </button>
                      </>
                    ) : (
                      <p className="summary-placeholder">
                        Set conditions, then freeze the resource allowance.
                        Exact inputs are recorded before queueing.
                      </p>
                    )}
                  </aside>
                </div>
              ) : (
                <Empty
                  title="A baseline is required"
                  action={
                    <button
                      className="button primary"
                      onClick={() => go(scenario ? "baseline" : "imports")}
                    >
                      Establish baseline
                      <ArrowRight size={16} />
                    </button>
                  }
                >
                  Import and audit a reference before computing comparable
                  cases.
                </Empty>
              )}
            </div>
          ) : null}
          {view === "runs" ? (
            <div className="content-page">
              <SectionHeading
                eyebrow="DURABLE EXECUTION"
                title="Run centre"
                description="Queued work survives the browser. A solver exit cannot bypass verification."
                action={
                  <button className="button" onClick={() => go("study")}>
                    <Plus size={15} />
                    New study
                  </button>
                }
              />
              {detail.data.runs.length ? (
                <div className="run-list">
                  {[...detail.data.runs].reverse().map((r) => (
                    <div
                      key={r.id}
                      className="run-item"
                      data-testid={`run-${r.id}`}
                    >
                      <div className="run-status-icon">
                        <Activity size={20} />
                      </div>
                      <div className="run-main">
                        <h3>
                          {detail.data.scenarios.find(
                            (s) => s.id === r.scenario_id,
                          )?.name ?? shortId(r.scenario_id)}
                          <Badge
                            tone={
                              r.status === "failed"
                                ? "red"
                                : r.status === "complete_unqualified"
                                  ? "amber"
                                  : "quiet"
                            }
                          >
                            {r.status.replaceAll("_", " ")}
                          </Badge>
                        </h3>
                        <span>
                          {r.stage} · attempt {r.attempt} · {shortId(r.id)}
                        </span>
                        {isActive(r.status) ? (
                          <div
                            className="progress-track"
                            aria-label={`Stage progress ${Math.round(r.progress * 100)} percent`}
                          >
                            <span
                              style={{
                                width: `${Math.min(100, Math.max(0, r.progress * 100))}%`,
                              }}
                            />
                          </div>
                        ) : null}
                        {r.error ? (
                          <p className="run-error">
                            {typeof r.error === "string"
                              ? r.error
                              : JSON.stringify(r.error)}
                          </p>
                        ) : null}
                      </div>
                      <div className="run-actions">
                        {isActive(r.status) ? (
                          <button
                            className="button small"
                            disabled={runAction.isPending || r.cancel_requested}
                            onClick={() =>
                              runAction.mutate({ id: r.id, action: "cancel" })
                            }
                          >
                            {r.cancel_requested
                              ? "Cancellation requested"
                              : "Cancel"}
                          </button>
                        ) : r.status === "complete_unqualified" ? (
                          <button
                            className="button small"
                            onClick={() =>
                              navigate(`/projects/${projectId}/runs/${r.id}`)
                            }
                          >
                            Inspect result
                            <ArrowRight size={14} />
                          </button>
                        ) : (
                          <button
                            className="button small"
                            disabled={runAction.isPending}
                            onClick={() =>
                              runAction.mutate({ id: r.id, action: "retry" })
                            }
                          >
                            <RotateCcw size={14} />
                            Retry as new attempt
                          </button>
                        )}
                        <button
                          aria-label={`Download manifest ${shortId(r.id)}`}
                          title="Download exact manifest"
                          className="icon-button"
                          onClick={() =>
                            downloadJson(
                              `run-${r.id}-manifest.json`,
                              r.manifest,
                            )
                          }
                        >
                          <Download size={16} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty
                  title="No runs queued yet"
                  action={
                    <button
                      className="button primary"
                      onClick={() => go("study")}
                    >
                      Set up study
                      <ArrowRight size={16} />
                    </button>
                  }
                >
                  Freeze a baseline and alternative under shared conditions to
                  create an actual numerical study.
                </Empty>
              )}
            </div>
          ) : null}
          {view === "result" ? (
            runQuery.isPending ? (
              <Loading label="Loading run evidence…" />
            ) : runQuery.error ? (
              <ErrorBox error={runQuery.error} />
            ) : runQuery.data && resultScenario ? (
              <>
                <div className="design-context">
                  <div>
                    <span className="eyebrow">
                      EXECUTED RESULT · {shortId(runId)}
                    </span>
                    <h1>{resultScenario.name}</h1>
                  </div>
                  <Badge tone="amber">
                    {runQuery.data.status.replaceAll("_", " ")}
                  </Badge>
                </div>
                {resultQuery.isPending &&
                runQuery.data.status === "complete_unqualified" ? (
                  <Loading label="Loading computed field…" />
                ) : resultQuery.error ? (
                  <ErrorBox error={resultQuery.error} />
                ) : resultQuery.data ? (
                  <>
                    <EvidenceNote
                      synthetic={isSynthetic(resultScenario)}
                      illustrative={
                        resultScenario.evidence_kind === "illustrative"
                      }
                    />
                    <div className="result-context">
                      <span>Passive tracer · µg/m³</span>
                      <span>
                        End state after:{" "}
                        {String(
                          (
                            runQuery.data.manifest.conditions as
                              Conditions | undefined
                          )?.duration_s ?? "—",
                        )}{" "}
                        s
                      </span>
                      <span>
                        Slice {formatValue(resultQuery.data.field.height_m, 2)}{" "}
                        m
                      </span>
                      <span>Simulated · research preview</span>
                    </div>
                    <div className="scene-layout">
                      <SceneView
                        scenario={resultScenario}
                        field={resultQuery.data.field}
                        selected={selected}
                        onSelect={selectObject}
                        scaleMax={fieldMaximum([resultQuery.data.field])}
                      />
                      <aside className="inspector">
                        <div className="inspector-heading">
                          <span className="eyebrow">
                            AUTHORITATIVE RECEPTOR VALUES
                          </span>
                          <h2>Outdoor concentration</h2>
                          <p>
                            Transient end-state concentration, not time-averaged
                            exposure or AQI. Requested height and containing
                            native-cell centre are shown for each receptor.
                          </p>
                        </div>
                        <MetricTable metrics={resultQuery.data.metrics} />
                        <div className="inspector-section">
                          <span className="eyebrow">INTERPRETATION LIMITS</span>
                          {resultQuery.data.limitations.map((l, i) => (
                            <p className="limitation" key={i}>
                              {l}
                            </p>
                          ))}
                        </div>
                        <JsonViewer
                          data={resultQuery.data.diagnostics}
                          title="Numerical diagnostics"
                        />
                        <button
                          className="button"
                          onClick={() => go("compare")}
                        >
                          Compare matched runs
                          <ArrowRight size={15} />
                        </button>
                        <button
                          className="button"
                          onClick={() =>
                            downloadJson(
                              `run-${runId}-manifest.json`,
                              runQuery.data?.manifest,
                            )
                          }
                        >
                          <Download size={15} />
                          Exact run manifest
                        </button>
                      </aside>
                    </div>
                  </>
                ) : (
                  <Empty
                    title={`Run ${runQuery.data.status.replaceAll("_", " ")}`}
                    action={
                      <button className="button" onClick={() => go("runs")}>
                        Open run centre
                        <ArrowRight size={16} />
                      </button>
                    }
                  >
                    The full field is available only after numerical
                    verification and atomic publication. Failed work remains
                    diagnostics.
                  </Empty>
                )}
              </>
            ) : null
          ) : null}
          {view === "compare" ? (
            <div className="content-page">
              <SectionHeading
                eyebrow="MATCHED CONDITIONS · FIXED SUPPORT"
                title="Compare alternatives"
                description="Inspect absolute differences and protected neighbours. The default mesh is not convergence-qualified: small differences can be numerical or native-cell sampling effects. These research previews cannot establish a real environmental benefit."
              />
              <div className="comparison-selectors">
                <label className="form-label">
                  Baseline run
                  <select
                    data-testid="baseline-run"
                    value={baseRunId}
                    onChange={(e) => {
                      const next = new URLSearchParams(params);
                      next.set("base", e.target.value);
                      setParams(next);
                      comparison.reset();
                    }}
                  >
                    <option value="">Choose completed run</option>
                    {completed.map((r) => (
                      <option key={r.id} value={r.id}>
                        {
                          detail.data.scenarios.find(
                            (s) => s.id === r.scenario_id,
                          )?.name
                        }{" "}
                        · {shortId(r.id)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="form-label">
                  Alternative run
                  <select
                    data-testid="alternative-run"
                    value={alternativeRunId}
                    onChange={(e) => {
                      const next = new URLSearchParams(params);
                      next.set("alt", e.target.value);
                      setParams(next);
                      comparison.reset();
                    }}
                  >
                    <option value="">Choose completed run</option>
                    {completed.map((r) => (
                      <option key={r.id} value={r.id}>
                        {
                          detail.data.scenarios.find(
                            (s) => s.id === r.scenario_id,
                          )?.name
                        }{" "}
                        · {shortId(r.id)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  data-testid="compare-runs"
                  className="button primary"
                  disabled={
                    !baseRunId || !alternativeRunId || comparison.isPending
                  }
                  onClick={() =>
                    comparison.mutate({
                      projectId,
                      baselineRunId: baseRunId,
                      alternativeRunId: alternativeRunId,
                    })
                  }
                >
                  Compare matched runs
                  <ArrowRight size={16} />
                </button>
              </div>
              {comparison.error ? (
                <ErrorBox error={comparison.error} />
              ) : comparison.isPending ? (
                <Loading label="Checking pairing and calculating differences…" />
              ) : comparisonData ? (
                <>
                  <div className="compare-summary">
                    <Badge tone="amber">
                      {comparisonData.qualification.replaceAll("_", " ")}
                    </Badge>
                    <span>Δ = alternative − baseline · µg/m³</span>
                    <span>
                      {baseRunId === alternativeRunId
                        ? "Same-run no-change check"
                        : "Matched-condition comparison"}
                    </span>
                  </div>
                  <MatchedFields
                    baselineResult={baseField.data}
                    alternativeResult={alternativeField.data}
                    baselineScenario={detail.data.scenarios.find(
                      (s) =>
                        s.id ===
                        detail.data.runs.find((r) => r.id === baseRunId)
                          ?.scenario_id,
                    )}
                    alternativeScenario={detail.data.scenarios.find(
                      (s) =>
                        s.id ===
                        detail.data.runs.find((r) => r.id === alternativeRunId)
                          ?.scenario_id,
                    )}
                    baselineRunId={baseRunId}
                    alternativeRunId={alternativeRunId}
                    error={baseField.error ?? alternativeField.error}
                  />
                  <p className="micro">
                    Receptor values are transient end states at the frozen
                    duration; they are not averaged exposure or AQI. Δ uses the
                    exact selected run pair.
                  </p>
                  <ComparisonTable data={comparisonData} />
                  <div className="comparison-limitations">
                    {comparisonData.limitations.map((l, i) => (
                      <p key={i}>{l}</p>
                    ))}
                  </div>
                  <div className="report-callout">
                    <FileText size={24} />
                    <div>
                      <h3>Keep the complete decision record</h3>
                      <p>
                        Create a draft with exact run manifests, numerical
                        evidence and limitations.
                      </p>
                    </div>
                    <label className="form-label">
                      Report title
                      <input
                        data-testid="report-title"
                        value={reportTitle}
                        onChange={(e) => setReportTitle(e.target.value)}
                        maxLength={180}
                      />
                    </label>
                    <button
                      data-testid="create-report"
                      className="button"
                      disabled={makeReport.isPending || !reportTitle.trim()}
                      onClick={() => makeReport.mutate()}
                    >
                      Create draft
                      <ArrowRight size={15} />
                    </button>
                  </div>
                  <JsonViewer
                    data={comparisonData}
                    title="Comparison evidence"
                  />
                </>
              ) : (
                <Empty title="Choose a pair to compare">
                  Both runs must be complete, share species, conditions and
                  receptor support, and use compatible model versions.
                  Mismatches return their exact blocker.
                </Empty>
              )}
            </div>
          ) : null}
          {view === "reports" ? (
            <div className="content-page">
              <SectionHeading
                eyebrow="IMMUTABLE DECISION RECORD"
                title="Reports"
                description="Drafts reference exact evidence. Scientific and professional approval are not available in the local Phase A workspace."
              />
              {reportId ? (
                reportQuery.isPending ? (
                  <Loading label="Loading report draft…" />
                ) : reportQuery.error ? (
                  <ErrorBox error={reportQuery.error} />
                ) : reportQuery.data ? (
                  <ReportView report={reportQuery.data} />
                ) : null
              ) : (
                <Empty
                  title="Create a report from a comparison"
                  action={
                    <button
                      className="button primary"
                      onClick={() => go("compare")}
                    >
                      Compare & draft
                      <ArrowRight size={16} />
                    </button>
                  }
                >
                  A report freezes the two selected runs, metrics, input
                  provenance and explicit research limitations.
                </Empty>
              )}
            </div>
          ) : null}
          {view === "exports" ? (
            <div className="content-page">
              <SectionHeading
                eyebrow="PERMITTED RESEARCH EVIDENCE"
                title="Export & reproduce"
                description="Download exact scenario inputs and run manifests. Native solver packages and fully reproducible cross-platform runners have later release gates."
              />
              {scenario ? (
                <>
                  <div className="export-item">
                    <Database size={23} />
                    <div>
                      <h3>
                        {scenario.name} · v{scenario.version}
                      </h3>
                      <p>
                        Local ENU geometry, origin, sources, receptors and
                        immutable input hash.
                      </p>
                    </div>
                    <button
                      className="button"
                      onClick={() =>
                        downloadJson(`scenario-${scenario.id}.json`, scenario)
                      }
                    >
                      <Download size={15} />
                      Scenario JSON
                    </button>
                  </div>
                  {detail.data.runs.map((r) => (
                    <div className="export-item" key={r.id}>
                      <FileText size={21} />
                      <div>
                        <h3>Run {shortId(r.id)}</h3>
                        <p>
                          {r.status.replaceAll("_", " ")} · exact conditions,
                          method and input identity
                        </p>
                      </div>
                      <button
                        className="button"
                        onClick={() =>
                          downloadJson(`run-${r.id}-manifest.json`, r.manifest)
                        }
                      >
                        <Download size={15} />
                        Manifest
                      </button>
                      {r.status === "complete_unqualified" ? (
                        <button
                          className="button"
                          onClick={async () => {
                            try {
                              downloadJson(
                                `run-${r.id}-result.json`,
                                await resultApi(r.id),
                              );
                            } catch (e) {
                              setActionError(e);
                            }
                          }}
                        >
                          Field & metrics JSON
                        </button>
                      ) : null}
                    </div>
                  ))}
                </>
              ) : (
                <Empty title="No exportable inputs yet">
                  Import and audit a geometry package first. No restricted
                  external provider imagery is used by this workspace.
                </Empty>
              )}
            </div>
          ) : null}
          {view === "investigate" || gatedViews.includes(view) ? (
            <div className="content-page">
              <SectionHeading
                eyebrow="THE FULL PRODUCT REMAINS THE REFERENCE"
                title="Capability roadmap"
                description="These capabilities have named scientific and operational gates. Phase A does not substitute invented outputs for them."
              />
              {gatedViews.includes(view) ? (
                <div className="fallback-note" role="status">
                  The requested{" "}
                  {view === "optimisations" ? "optimisation" : view} capability
                  is unavailable in this local Phase A workspace. Its release
                  and evidence gates are listed below.
                </div>
              ) : null}
              <div className="capability-list">
                {laterCapabilities.map(([name, stage, reason]) => (
                  <div key={name}>
                    <LockKeyhole size={21} />
                    <div>
                      <h3>{name}</h3>
                      <p>{reason}</p>
                    </div>
                    <Badge>{stage} · unavailable</Badge>
                  </div>
                ))}
              </div>
              <p className="reference-note">
                The original blueprint and full execution guide are retained
                unchanged as project references. Phase A is the first working
                path, not the final scientific qualification.
              </p>
              <p className="reference-note">
                Production settings, roles, provider keys and budgets require a
                configured deployment. The local identity grants no production
                access.
              </p>
            </div>
          ) : null}
          {view === "unknown" ? (
            <div className="content-page">
              <Empty
                title="This workspace route is unavailable"
                action={
                  <button
                    className="button primary"
                    onClick={() => go("explore")}
                  >
                    Open site explorer
                    <ArrowRight size={16} />
                  </button>
                }
              >
                Use the workspace navigation to open an implemented task or
                inspect the capability roadmap.
              </Empty>
            </div>
          ) : null}
        </main>
        <footer className="studio-footer">
          <span>
            <span className="status-dot" />
            Loopback development workspace
          </span>
          <span data-testid="snapshot-footer">
            Project {shortId(projectId)} ·{" "}
            {view === "result"
              ? runQuery.data ? `executed run ${shortId(runQuery.data.id)} · snapshot ${shortId(runQuery.data.scenario_id)}` : "executed snapshot unavailable"
              : scenario ? `selected design snapshot ${shortId(scenario.id)}` : "no geometry"}
          </span>
          <span>No provider imagery · no observed AQI</span>
        </footer>
      </div>
    </div>
  );
}
function MatchedFields({
  baselineResult,
  alternativeResult,
  baselineScenario,
  alternativeScenario,
  baselineRunId,
  alternativeRunId,
  error,
}: {
  baselineResult?: Result;
  alternativeResult?: Result;
  baselineScenario?: Scenario;
  alternativeScenario?: Scenario;
  baselineRunId: string;
  alternativeRunId: string;
  error: unknown;
}) {
  if (error) return <ErrorBox error={error} />;
  if (
    !baselineResult ||
    !alternativeResult ||
    !baselineScenario ||
    !alternativeScenario
  )
    return (
      <Loading label="Loading verified slices from the matched run pair…" />
    );
  const issue = matchedSliceIssue(
    baselineResult.field,
    alternativeResult.field,
  );
  if (issue)
    return (
      <div className="fallback-note" role="status">
        {issue}
      </div>
    );
  const max = fieldMaximum([baselineResult.field, alternativeResult.field]);
  return (
    <section
      className="matched-fields"
      aria-label="Locked-scale baseline and alternative fields"
    >
      <div className="matched-field-context">
        <strong>Matched native slices · fixed plan view</strong>
        <span>
          Passive tracer · end state ·{" "}
          {formatValue(baselineResult.field.height_m, 2)} m above local ground ·
          scale 0–{formatValue(max)} µg/m³
        </span>
        <span>
          Identical extent, height, units and scale. Hatched cells are solid/no
          data. Cells can be probed; no resampling is applied.
        </span>
      </div>
      <div className="matched-field-grid">
        {[
          {
            label: "Baseline",
            runId: baselineRunId,
            scenario: baselineScenario,
            result: baselineResult,
          },
          {
            label: "Alternative",
            runId: alternativeRunId,
            scenario: alternativeScenario,
            result: alternativeResult,
          },
        ].map((item) => (
          <article key={item.label} className="matched-field-panel">
            <h3>
              {item.label} · {item.scenario.name}
              <small>
                Run {shortId(item.runId)} ·{" "}
                {isSynthetic(item.scenario)
                  ? "synthetic geometry"
                  : "imported geometry"}
              </small>
            </h3>
            <SvgScene
              key={item.runId}
              scenario={item.scenario}
              field={item.result.field}
              selected={null}
              onSelect={() => {}}
              scaleMax={max}
            />
          </article>
        ))}
      </div>
      <div className="matched-field-legend">
        <span>0 µg/m³</span>
        <div className="legend-ramp" />
        <span>{formatValue(max)} µg/m³</span>
        <span>▧ Masked / no data</span>
      </div>
    </section>
  );
}
function ObjectList({
  scenario,
  selected,
  onSelect,
  filter,
}: {
  scenario: Scenario;
  selected: Selection | null;
  onSelect: (s: Selection) => void;
  filter?: Selection["kind"];
}) {
  const groups: [Selection["kind"], { id: string; name: string }[]][] = [
    ["building", scenario.buildings],
    ["source", scenario.sources],
    ["receptor", scenario.receptors],
  ];
  return (
    <div className="object-list">
      <span className="eyebrow">SCENE OBJECTS</span>
      {groups
        .filter(([kind]) => !filter || kind === filter)
        .map(([kind, objects]) => (
          <div key={kind}>
            <h3>
              {kind === "building"
                ? "Buildings"
                : kind === "source"
                  ? "Sources"
                  : "Receptors"}
              <span>{objects.length}</span>
            </h3>
            {objects.map((o) => (
              <button
                data-testid={`select-${o.id}`}
                key={o.id}
                className={selected?.id === o.id ? "selected" : ""}
                onClick={() => onSelect({ kind, id: o.id })}
              >
                {kind === "building" ? (
                  <Box size={13} />
                ) : kind === "source" ? (
                  <Wind size={13} />
                ) : (
                  <MapPin size={13} />
                )}
                <span>{o.name}</span>
                <ChevronRight size={12} />
              </button>
            ))}
          </div>
        ))}
    </div>
  );
}
function ImportView({
  projectId,
  imports,
  onImported,
}: {
  projectId: string;
  imports: ImportRecord[];
  onImported: (s: Scenario) => void | Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null),
    [sourceLabel, setSourceLabel] = useState("User supplied geometry"),
    [rights, setRights] = useState({
      derive: false,
      solve: false,
      export: false,
    });
  const [parseError, setParseError] = useState<unknown>(null);
  const mutation = useMutation({
    mutationFn: async (synthetic: boolean) => {
      setParseError(null);
      const geojson = synthetic
        ? await api<GeoJSON>("/fixtures/campus")
        : (JSON.parse(await file!.text()) as GeoJSON);
      return api<{
        id: string;
        scenario: Scenario;
        audit: Record<string, unknown>;
      }>(`/projects/${projectId}/imports`, {
        filename: synthetic ? "synthetic-campus.geojson" : file?.name,
        geojson,
        rights: synthetic
          ? { derive: true, solve: true, export: true }
          : rights,
        source_label: synthetic
          ? "Synthetic reference campus — fictional"
          : sourceLabel,
      });
    },
    onSuccess: (r) => onImported(r.scenario),
    onError: setParseError,
  });
  return (
    <div className="content-page">
      <SectionHeading
        eyebrow="START WITH TRUSTWORTHY INPUTS"
        title="Bring your site into focus"
        description="Import independent WGS84 GeoJSON for a bounded area. Buildings, point sources and receptors are audited before they become a baseline."
      />
      <div className="import-layout">
        <form
          className="import-form"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate(false);
          }}
        >
          <label className="upload-drop">
            <Upload size={29} />
            <strong>{file?.name ?? "Choose a GeoJSON file"}</strong>
            <span>FeatureCollection · WGS84 longitude/latitude</span>
            <input
              data-testid="geometry-upload"
              type="file"
              accept=".geojson,.json,application/geo+json,application/json"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <label className="form-label">
            Source / provenance label
            <input
              data-testid="source-label"
              required
              value={sourceLabel}
              onChange={(e) => setSourceLabel(e.target.value)}
              maxLength={160}
            />
          </label>
          <fieldset className="rights-checkboxes">
            <legend>Confirm rights for these inputs</legend>
            {(["derive", "solve", "export"] as const).map((key) => (
              <label key={key}>
                <input
                  data-testid={`right-${key}`}
                  type="checkbox"
                  checked={rights[key]}
                  onChange={(e) =>
                    setRights((s) => ({ ...s, [key]: e.target.checked }))
                  }
                />
                {key === "derive"
                  ? "Transform and edit geometry"
                  : key === "solve"
                    ? "Use for scientific computation"
                    : "Export the supplied input data"}
              </label>
            ))}
          </fieldset>
          <p className="micro">
            Your confirmation is recorded as an input declaration. It does not
            establish ownership or survey accuracy.
          </p>
          <button
            data-testid="import-upload"
            className="button primary"
            disabled={
              !file ||
              !sourceLabel.trim() ||
              !Object.values(rights).every(Boolean) ||
              mutation.isPending
            }
          >
            Audit & import
            <ArrowRight size={16} />
          </button>
          {parseError ? <ErrorBox error={parseError} /> : null}
        </form>
        <aside className="import-guide">
          <span className="eyebrow">TRY THE COMPLETE WORKFLOW</span>
          <div className="fixture-icon">
            <Layers size={36} />
          </div>
          <h2>
            A synthetic campus.
            <br />A real execution path.
          </h2>
          <p>
            The included fictional reference has independent building geometry,
            explicit source rates and receptor points. Its approximate Delhi
            origin is only a coordinate example.
          </p>
          <Badge tone="violet">Synthetic · not a surveyed Delhi site</Badge>
          <button
            data-testid="import-fixture"
            className="button"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate(true)}
          >
            {mutation.isPending
              ? "Auditing geometry…"
              : "Import synthetic reference"}
            <ArrowRight size={16} />
          </button>
          <Link
            className="text-button"
            to="/api/fixtures/campus"
            target="_blank"
            reloadDocument
          >
            <Download size={14} />
            Inspect the GeoJSON schema
          </Link>
          <div className="supported-format">
            <strong>Supported now</strong>
            <span>
              Polygon buildings with height_m.
              <br />
              Point sources with rate_g_s.
              <br />
              Point receptors with z and protected.
            </span>
            <strong>Later import gate</strong>
            <span>
              IFC, GeoPackage, terrain, openings and advanced source geometry.
            </span>
          </div>
        </aside>
      </div>
      {imports.length ? (
        <section className="past-imports">
          <h2>Import history</h2>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Scrollable import diagnostics">
            <table>
              <thead>
                <tr>
                  <th>File</th>
                  <th>Provenance</th>
                  <th>Snapshot</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {imports.map((i) => (
                  <tr key={i.id}>
                    <td>{i.filename}</td>
                    <td>{i.source_label}</td>
                    <td>{shortId(i.scenario_id)}</td>
                    <td>
                      {new Date(i.created_at).toLocaleDateString("en-IN")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}
function DataAudit({
  scenario,
  imports,
  onImport,
}: {
  scenario?: Scenario;
  imports: ImportRecord[];
  onImport: () => void;
}) {
  return (
    <div className="content-page">
      <SectionHeading
        eyebrow="QUALITY BEFORE GRAPHICS"
        title="Data & geometry audit"
        description="Keep source declarations, coordinate support and scientific blockers separate from an attractive model."
      />
      {scenario ? (
        <>
          <div className="audit-grid">
            <div className="audit-panel">
              <Badge tone="violet">Imported snapshot</Badge>
              <h2>Geometry & sources</h2>
              <CheckRow
                label="Explicit geometry"
                detail={`${scenario.buildings.length} buildings with supplied heights`}
              />
              <CheckRow
                label="Coordinate support"
                detail={`Origin ${scenario.origin.longitude.toFixed(5)}°, ${scenario.origin.latitude.toFixed(5)}° · local ENU metres`}
              />
              <CheckRow
                label="Input rate and receptor support"
                detail={`${scenario.sources.length} point sources · ${scenario.receptors.length} receptor points`}
              />
              <JsonViewer
                data={scenario.diagnostics}
                title="Audited diagnostics"
              />
            </div>
            <div className="audit-panel">
              <Badge tone="amber">Qualification blockers</Badge>
              <h2>Evidence still required</h2>
              <CheckRow
                label="No field observations"
                detail="No station connector or independently calibrated instruments are connected."
                good={false}
              />
              <CheckRow
                label="No surveyed site qualification"
                detail="Imported height/source declarations do not establish their accuracy."
                good={false}
              />
              <CheckRow
                label="Detailed model unavailable"
                detail="T2 RANS, seasonal ensembles, vegetation and chemistry require Stage B/E evidence."
                good={false}
              />
            </div>
          </div>
          <h2 className="subheading">Input lineage</h2>
          {imports.map((i) => (
            <div className="lineage-item" key={i.id}>
              <Database size={18} />
              <div>
                <strong>{i.source_label}</strong>
                <span>
                  {i.filename} · snapshot {shortId(i.scenario_id)}
                </span>
              </div>
              <JsonViewer
                data={{
                  rights: i.rights,
                  audit: i.audit,
                  created_at: i.created_at,
                }}
                title="Import evidence"
              />
            </div>
          ))}
        </>
      ) : (
        <Empty
          title="No input package to audit"
          action={
            <button className="button primary" onClick={onImport}>
              Import geometry
              <ArrowRight size={16} />
            </button>
          }
        >
          The audit is populated from actual import checks, not invented
          coverage scores.
        </Empty>
      )}
    </div>
  );
}
function MetricTable({ metrics }: { metrics: Result["metrics"] }) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Scrollable receptor metrics">
      <table>
        <thead>
          <tr>
            <th>Receptor</th>
            <th>µg/m³</th>
            <th>Support</th>
            <th>Requested height</th>
            <th>Native cell centre</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => (
            <tr key={m.receptor_id}>
              <td>{m.name}</td>
              <td className="numeric">
                {m.valid ? formatValue(m.value_ug_m3) : "—"}
              </td>
              <td>{m.valid ? "Native cell" : (m.reason ?? "Unavailable")}</td>
              <td>{formatValue(m.requested_height_m, 2)} m</td>
              <td>
                {m.cell_centre
                  ? `(${m.cell_centre.map((v) => formatValue(v, 2)).join(", ")}) m`
                  : "Unavailable"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function ComparisonTable({ data }: { data: Comparison }) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Scrollable comparison metrics">
      <table data-testid="comparison-table">
        <thead>
          <tr>
            <th>Receptor</th>
            <th>Baseline µg/m³</th>
            <th>Alternative µg/m³</th>
            <th>Requested → native sample height (m)</th>
            <th>Δ µg/m³</th>
            <th>Change</th>
            <th>Neighbour protection</th>
          </tr>
        </thead>
        <tbody>
          {data.metrics.map((m) => (
            <tr key={m.receptor_id}>
              <td>{m.name}</td>
              <td className="numeric">{formatValue(m.baseline_ug_m3)}</td>
              <td className="numeric">{formatValue(m.alternative_ug_m3)}</td>
              <td>
                {formatValue(m.requested_height_m, 2)} →{" "}
                {formatValue(m.baseline_cell_centre?.[2], 2)} /{" "}
                {formatValue(m.alternative_cell_centre?.[2], 2)}
                <small className="sampling-note">
                  baseline / alternative cell centre
                </small>
              </td>
              <td
                className={`numeric ${m.delta_ug_m3 !== null && m.delta_ug_m3 > 0 ? "worsening" : ""}`}
              >
                {m.delta_ug_m3 !== null && m.delta_ug_m3 > 0 ? "+" : ""}
                {formatValue(m.delta_ug_m3, 4)}
              </td>
              <td
                className="numeric"
                title={m.percent_suppression_reason ?? undefined}
              >
                {m.percent_change === null
                  ? "—"
                  : `${formatValue(m.percent_change)}%`}
              </td>
              <td>
                {m.protected ? (
                  <Badge
                    tone={
                      m.delta_ug_m3 !== null && m.delta_ug_m3 > 0
                        ? "amber"
                        : "quiet"
                    }
                  >
                    {m.delta_ug_m3 !== null && m.delta_ug_m3 > 0
                      ? "Worsens in this run"
                      : "Protected receptor"}
                  </Badge>
                ) : (
                  "Not designated"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function ReportView({ report }: { report: Report }) {
  const [downloadError, setDownloadError] = useState<unknown>();
  const [downloading, setDownloading] = useState(false);
  const download = async (format: "html" | "json") => {
    setDownloading(true);
    setDownloadError(undefined);
    try { await downloadProtected(`/reports/${report.id}/download?format=${format}`, `urban-air-report-${report.id}.${format}`); }
    catch (error) { setDownloadError(error); }
    finally { setDownloading(false); }
  };
  return (
    <>
      <div className="report-actions">
        <Badge tone="amber">Draft · research preview</Badge>
        <span>
          Frozen {new Date(report.created_at).toLocaleString("en-IN")}
        </span>
        <button
          className="button"
          data-testid="download-report-html"
          disabled={downloading}
          onClick={() => void download("html")}
        >
          <FileText size={15} />
          Download print-ready report
        </button>
        <button
          className="button"
          data-testid="download-report-json"
          disabled={downloading}
          onClick={() => void download("json")}
        >
          <Download size={15} />
          Report JSON
        </button>
      </div>
      {downloadError ? <ErrorBox error={downloadError} /> : null}
      <article className="report-preview">
        <span className="eyebrow">URBAN AIR STUDIO / DECISION DRAFT</span>
        <h1>{report.title}</h1>
        <p className="report-intro">
          A bounded passive-tracer comparison under frozen conditions. This
          report records actual numerical outputs, exact input identity and
          their research limitations. Values are transient end-state
          concentrations, not averaged exposure or AQI.
        </p>
        <p className="numerical-caveat">The default mesh is not convergence-qualified. Small differences can be caused by numerical or native-cell sampling effects; this preview cannot establish a real planning benefit.</p>
        <div className="report-identities">
          <div>
            <span>BASELINE RUN</span>
            <code>{report.baseline_run_id}</code>
          </div>
          <div>
            <span>ALTERNATIVE RUN</span>
            <code>{report.alternative_run_id}</code>
          </div>
        </div>
        <h2>Receptor comparison</h2>
        <ComparisonTable data={report.comparison} />
        <h2>Limitations & unresolved evidence</h2>
        <ul>
          {report.limitations.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
        <h2>Reproducibility record</h2>
        <p>
          Report checksum: <code>{report.checksum}</code>. Exact provenance,
          method and scenario inputs are included in the downloadable manifest.
        </p>
        <JsonViewer data={report.manifest} title="Frozen report manifest" />
        <p className="report-disclaimer">
          DRAFT · NOT INDEPENDENTLY VALIDATED · NO PROFESSIONAL APPROVAL
        </p>
      </article>
    </>
  );
}
