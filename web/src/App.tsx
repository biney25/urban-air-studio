import { lazy, Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  Box,
  FolderOpen,
  Map,
  Plus,
  ShieldCheck,
  Sparkles,
  Wind,
} from "lucide-react";
import { api } from "./api";
import type { Project } from "./types";
import { Badge, Empty, ErrorBox, Loading } from "./components/UI";
import Studio from "./Studio";
import ReviewPage from "./components/ReviewPage";
import { AuthStatus } from "./components/AuthGate";
import { WorkspaceNav } from "./components/WorkspaceNav";
const Explore = lazy(() => import("./ExplorePage"));
export default function App() {
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to workspace
      </a>
      <Routes>
        <Route
          path="/explore"
          element={
            <Suspense
              fallback={
                <main id="main-content" className="content-page">
                  <Loading label="Opening geographic Explore…" />
                </main>
              }
            >
              <Explore />
            </Suspense>
          }
        />
        <Route path="/projects" element={<Projects />} />
        <Route path="/review" element={<ReviewPage />} />
        <Route path="/projects/new" element={<Projects create />} />
        <Route path="/projects/:projectId/*" element={<Studio />} />
        <Route path="/settings" element={<SettingsGate />} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </>
  );
}
function SettingsGate() {
  return (
    <main id="main-content" className="content-page">
      <Empty title="Production settings are unavailable">
        Roles, provider configurations, budgets and retention require a
        configured deployment. This local research workspace uses an explicit
        local identity.
      </Empty>
      <Link className="button" to="/projects">
        Return to projects
        <ArrowRight size={16} />
      </Link>
    </main>
  );
}
function Projects({ create = false }: { create?: boolean }) {
  const navigate = useNavigate(),
    client = useQueryClient();
  const [params] = useSearchParams();
  const selectedArea = params.has("lat") && params.has("lng");
  const [name, setName] = useState("");
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState("user");
  const [entry, setEntry] = useState<"place" | "import">("place");
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: ({ signal }) =>
      api<{ operator_views_available?: boolean }>(
        "/capabilities",
        undefined,
        signal,
      ),
    staleTime: 60000,
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => api<Project[]>("/projects", undefined, signal),
  });
  const mutation = useMutation({
    mutationFn: () =>
      api<Project>("/projects", {
        name: name.trim(),
        category: "user",
        planning_question: question.trim(),
        ...(selectedArea
          ? {
              site: {
                latitude: Number(params.get("lat")),
                longitude: Number(params.get("lng")),
                width_km: Number(params.get("width") ?? "1"),
              },
            }
          : {}),
      }),
    onSuccess: async (project) => {
      await client.invalidateQueries({ queryKey: ["projects"] });
      if (selectedArea || params.has("context")) {
        const area = new URLSearchParams(
          selectedArea
            ? {
                lat: params.get("lat")!,
                lng: params.get("lng")!,
                width: params.get("width") ?? "1",
                project: project.id,
              }
            : { project: project.id },
        );
        if (params.get("context")) area.set("context", params.get("context")!);
        navigate(`/explore?${area}`);
      } else
        navigate(
          entry === "place"
            ? `/explore?project=${encodeURIComponent(project.id)}`
            : `/projects/${project.id}/imports`,
        );
    },
  });
  const categorize = useMutation({
    mutationFn: ({ id, category }: { id: string; category: string }) =>
      api<Project>(`/projects/${encodeURIComponent(id)}/category`, {
        category,
      }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  return (
    <div className="projects-page">
      <header className="landing-header">
        <Link
          className="brand"
          to="/projects"
          aria-label="Urban Air Studio projects"
        >
          <span className="brand-mark">
            <Wind size={21} />
          </span>
          <span>
            URBAN AIR <span>STUDIO</span>
          </span>
        </Link>
        <WorkspaceNav />
        <div className="landing-access">
          <Badge tone="cyan">Research workspace</Badge>
          <details className="session-disclosure">
            <summary>Session</summary>
            <AuthStatus />
          </details>
        </div>
      </header>
      <main id="main-content" className="projects-main">
        <div className="project-intro">
          <span className="eyebrow">URBAN AIR PLANNING</span>
          <h1>
            Explore a site.
            <br />
            Compare a better design.
          </h1>
          <p>
            Review a site's buildings and sources, test a design alternative,
            and keep a clear record of the comparison.
          </p>
          <Link className="button primary explore-entry" to="/explore">
            <Map size={17} /> Choose a place
            <ArrowRight size={16} />
          </Link>
          <div className="intro-proof">
            <ShieldCheck size={18} />
            <span>
              Your baseline stays fixed. Every alternative and study can be
              revisited.
            </span>
          </div>
          <p className="quiet micro">
            Studies are research previews. They do not yet establish real-world
            pollution reduction or validated AQI.
          </p>
        </div>
        <section className="project-library">
          <div className="library-heading">
            <div>
              <span className="eyebrow">YOUR WORKSPACE</span>
              <h2>{create ? "Create a project" : "Projects"}</h2>
            </div>
            {!create ? (
              <Link className="button primary" to="/projects/new">
                <Plus size={16} />
                New project
              </Link>
            ) : (
              <Link className="text-button" to="/projects">
                Back to projects
              </Link>
            )}
          </div>
          {create ? (
            <form
              className="create-project"
              onSubmit={(e) => {
                e.preventDefault();
                mutation.mutate();
              }}
            >
              <label className="form-label">
                Project name
                <input
                  data-testid="project-name"
                  autoFocus
                  required
                  minLength={2}
                  maxLength={120}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Roadside development comparison"
                />
              </label>
              <label className="form-label">
                What decision are you comparing?
                <textarea
                  required
                  minLength={8}
                  maxLength={1000}
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="e.g. Compare two building layouts while protecting the school entrance and neighbouring homes."
                  rows={3}
                />
              </label>
              <p>
                {selectedArea
                  ? "Your chosen area will be saved with this project. Review its data before freezing a baseline."
                  : "Choose a place next, add your own site model, or open a fictional sample."}
              </p>
              {!selectedArea && !params.has("context") ? (
                <fieldset className="project-entry-options">
                  <legend>How will you start?</legend>
                  <label className="check-label">
                    <input
                      type="radio"
                      name="project-entry"
                      value="place"
                      checked={entry === "place"}
                      onChange={() => setEntry("place")}
                    />
                    Choose a place and review its buildings
                  </label>
                  <label className="check-label">
                    <input
                      type="radio"
                      name="project-entry"
                      value="import"
                      checked={entry === "import"}
                      onChange={() => setEntry("import")}
                    />
                    Import a site model you own or may use
                  </label>
                </fieldset>
              ) : null}
              <div className="capability-strip">
                <Box size={16} />
                <span>Buildings · sources · receptors</span>
              </div>
              {mutation.error ? <ErrorBox error={mutation.error} /> : null}
              <button
                data-testid="create-project"
                className="button primary"
                disabled={
                  mutation.isPending ||
                  name.trim().length < 2 ||
                  question.trim().length < 8
                }
              >
                {mutation.isPending
                  ? "Creating…"
                  : selectedArea
                    ? "Create project for this area"
                    : "Create project"}
                <ArrowRight size={16} />
              </button>
            </form>
          ) : projects.isPending ? (
            <Loading />
          ) : projects.error ? (
            <ErrorBox
              error={projects.error}
              onRetry={() => void projects.refetch()}
            />
          ) : projects.data?.length ? (
            <>
              <nav className="project-scope" aria-label="Project collections">
                {[
                  ["user", "My projects"],
                  ["sample", "Samples"],
                  ["unclassified", "Earlier projects"],
                  ...(capabilities.data?.operator_views_available
                    ? [["internal_test", "Internal testing"]]
                    : []),
                ].map(([value, label]) => (
                  <button
                    key={value}
                    className={scope === value ? "active" : ""}
                    aria-pressed={scope === value}
                    onClick={() => setScope(value)}
                  >
                    {label}
                    <span>
                      {
                        projects.data.filter(
                          (p) => (p.category ?? "unclassified") === value,
                        ).length
                      }
                    </span>
                  </button>
                ))}
              </nav>
              {categorize.error ? <ErrorBox error={categorize.error} /> : null}
              <div className="project-items">
                {projects.data
                  .filter(
                    (p) =>
                      ((p as Project & { category?: string | null }).category ??
                        "unclassified") === scope,
                  )
                  .map((p) => (
                    <div key={p.id} className="project-record">
                      <Link
                        className="project-item"
                        to={
                          p.baseline_scenario_id
                            ? `/projects/${p.id}/design`
                            : p.site
                              ? `/explore?${new URLSearchParams({ project: p.id, lat: String(p.site.latitude), lng: String(p.site.longitude), width: String(p.site.width_km) })}`
                              : `/projects/${p.id}/imports`
                        }
                      >
                        <div className="project-thumb">
                          <Box size={24} />
                        </div>
                        <div>
                          <h3>{p.name}</h3>
                          {p.planning_question ? (
                            <p className="project-question">
                              {p.planning_question}
                            </p>
                          ) : null}
                          <span>
                            {p.baseline_scenario_id
                              ? "Resume this site's baseline and alternatives"
                              : "Next: review site inputs and fix the baseline"}
                          </span>
                          <small>
                            {new Date(p.created_at).toLocaleDateString(
                              "en-IN",
                              {
                                day: "numeric",
                                month: "short",
                                year: "numeric",
                              },
                            )}
                          </small>
                          {p.site ? (
                            <small>
                              Selected site · {p.site.width_km} km across
                            </small>
                          ) : null}
                        </div>
                        <ArrowUpRight size={20} />
                      </Link>
                      <details className="project-category">
                        <summary>Project collection</summary>
                        <label className="form-label">
                          Assign explicitly
                          <select
                            value={p.category ?? ""}
                            disabled={categorize.isPending}
                            onChange={(event) => {
                              if (event.target.value)
                                categorize.mutate({
                                  id: p.id,
                                  category: event.target.value,
                                });
                            }}
                          >
                            <option value="" disabled>
                              Earlier project · unclassified
                            </option>
                            <option value="user">My projects</option>
                            <option value="sample">Sample</option>
                            {capabilities.data?.operator_views_available ? (
                              <option value="internal_test">
                                Internal testing
                              </option>
                            ) : null}
                          </select>
                        </label>
                        <p className="micro">
                          This changes only the collection. The project and its
                          evidence are retained.
                        </p>
                      </details>
                    </div>
                  ))}
                {!projects.data.some(
                  (p) =>
                    ((p as Project & { category?: string | null }).category ??
                      "unclassified") === scope,
                ) ? (
                  <Empty
                    title={
                      scope === "user"
                        ? "Start your first site comparison"
                        : "No projects in this collection"
                    }
                  >
                    Create a project or choose another collection. Earlier work
                    is retained under Earlier projects until its category is
                    explicitly assigned.
                  </Empty>
                ) : null}
              </div>
            </>
          ) : (
            <Empty title="Your first planning study">
              Bring a proposal into a project, establish its baseline, and build
              a comparison that can be traced back to its inputs.
            </Empty>
          )}
          <div className="library-footer">
            <FolderOpen size={17} />
            <span>
              Start from a place or an owned site model. Keep assumptions and
              research limitations with every study.
            </span>
            <Sparkles size={16} />
          </div>
        </section>
      </main>
      <footer className="landing-footer">
        <span>URBAN AIR STUDIO / PLANNING WORKBENCH</span>
        <span>Research previews · source permissions enforced</span>
      </footer>
    </div>
  );
}
