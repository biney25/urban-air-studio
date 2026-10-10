import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  Box,
  FolderOpen,
  Plus,
  ShieldCheck,
  Sparkles,
  Wind,
} from "lucide-react";
import { api } from "./api";
import type { Project } from "./types";
import { Badge, Empty, ErrorBox, Loading } from "./components/UI";
import Studio from "./Studio";
import { AuthStatus } from "./components/AuthGate";
export default function App() {
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to workspace
      </a>
      <Routes>
        <Route path="/projects" element={<Projects />} />
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
        configured deployment. This local Phase A workspace uses an explicit
        development identity.
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
  const [name, setName] = useState("");
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => api<Project[]>("/projects", undefined, signal),
  });
  const mutation = useMutation({
    mutationFn: () => api<Project>("/projects", { name: name.trim() }),
    onSuccess: async (project) => {
      await client.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${project.id}/imports`);
    },
  });
  return (
    <div className="projects-page">
      <header className="landing-header">
        <Link className="brand" to="/projects" aria-label="Urban Air Studio projects">
          <span className="brand-mark">
            <Wind size={21} />
          </span>
          <span>
            URBAN AIR{" "}<span>STUDIO</span>
          </span>
        </Link>
        <div className="landing-access"><Badge tone="cyan">Phase A · research workbench</Badge><AuthStatus /></div>
      </header>
      <main id="main-content" className="projects-main">
        <div className="project-intro">
          <span className="eyebrow">DESIGN WITH EVIDENCE</span>
          <h1>
            Make room for
            <br />
            <em>better air.</em>
          </h1>
          <p>
            Compare urban design proposals through a clear, reproducible
            planning workflow. Start with your geometry. Keep every assumption
            in view.
          </p>
          <div className="intro-proof">
            <ShieldCheck size={18} />
            <span>
              Immutable scenarios. Traceable studies.
              <br />
              Honest limits, at every step.
            </span>
          </div>
          <div className="mini-city" aria-hidden="true">
            {[18, 34, 25, 46, 17, 38, 30, 14, 42].map((v, i) => (
              <span key={i} style={{ height: `${v * 3}px` }} />
            ))}
            <div className="city-line" />
          </div>
          <p className="quiet micro">
            The studio uses independent imported geometry.
            <br />
            No city or campus is assumed to have been surveyed or validated.
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
              <p>
                Import WGS84 GeoJSON for any supported bounded area, or try the
                clearly labelled synthetic campus fixture. Your project begins
                without a baseline.
              </p>
              <div className="capability-strip">
                <Box size={16} />
                <span>Buildings · sources · receptors</span>
              </div>
              {mutation.error ? <ErrorBox error={mutation.error} /> : null}
              <button
                data-testid="create-project"
                className="button primary"
                disabled={mutation.isPending || name.trim().length < 2}
              >
                {mutation.isPending ? "Creating…" : "Create & import geometry"}
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
            <div className="project-items">
              {projects.data.map((p) => (
                <Link
                  key={p.id}
                  className="project-item"
                  to={`/projects/${p.id}/${p.baseline_scenario_id ? "explore" : "imports"}`}
                >
                  <div className="project-thumb">
                    <Box size={24} />
                  </div>
                  <div>
                    <h3>{p.name}</h3>
                    <span>
                      {p.baseline_scenario_id
                        ? "Baseline established"
                        : "Awaiting geometry import"}
                    </span>
                    <small>
                      {new Date(p.created_at).toLocaleDateString("en-IN", {
                        day: "numeric",
                        month: "short",
                        year: "numeric",
                      })}
                    </small>
                  </div>
                  <ArrowUpRight size={20} />
                </Link>
              ))}
            </div>
          ) : (
            <Empty title="Your first planning study">
              Bring a proposal into a project, establish its baseline, and build
              a comparison that can be traced back to its inputs.
            </Empty>
          )}
          <div className="library-footer">
            <FolderOpen size={17} />
            <span>
              Local research workspace. Professional validation, AI planning and
              enterprise sharing have later evidence gates.
            </span>
            <Sparkles size={16} />
          </div>
        </section>
      </main>
      <footer className="landing-footer">
        <span>URBAN AIR STUDIO / PLANNING WORKBENCH</span>
        <span>Blueprint preserved · Outdoor exposure research</span>
      </footer>
    </div>
  );
}
