import { BarChart3, Box, Compass, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { workflowPath, workflowSearch } from "../workflowContext";

export function WorkspaceNav({
  projectId,
  contextId,
  active = "explore",
  searchParams,
}: {
  projectId?: string;
  contextId?: string;
  active?: string;
  searchParams?: URLSearchParams;
}) {
  const workspaces = [
    ["explore", "Explore", Compass],
    ["design", "Design", Box],
    ["compare", "Compare", BarChart3],
    ["investigate", "Investigate", Sparkles],
  ] as const;
  return (
    <nav className="workspace-links" aria-label="Workspaces">
      {workspaces.map(([id, label, Icon]) =>
        projectId || id === "explore" ? (
          <Link
            key={id}
            to={
              id === "explore"
                ? `/explore${projectId ? `?${workflowSearch(searchParams, { project: projectId, ...(contextId ? { context: contextId, lat: null, lng: null, width: null } : {}) })}` : ""}`
                : workflowPath(projectId!, id, searchParams)
            }
            aria-current={active === id ? "page" : undefined}
          >
            <Icon size={16} />
            {label}
          </Link>
        ) : (
          <span
            key={id}
            aria-disabled="true"
            title="Choose or create a project to continue"
          >
            <Icon size={16} />
            {label}
          </span>
        ),
      )}
    </nav>
  );
}

export function TaskTrail({
  projectId,
  active,
  scenarioId,
  site,
  contextId,
  searchParams,
}: {
  projectId: string;
  active: string;
  scenarioId?: string;
  site?: { latitude: number; longitude: number; width_km: number } | null;
  contextId?: string;
  searchParams?: URLSearchParams;
}) {
  const steps = [
    ["explore", "Site"],
    ["data", "Inputs"],
    ["baseline", "Baseline"],
    ["design", "Alternative"],
    ["study", "Study"],
    ["compare", "Compare"],
    ["reports", "Report"],
  ];
  return (
    <nav className="task-trail" aria-label="Study progress">
      {steps.map(([route, label], index) => (
        <Link
          key={route}
          to={
            route === "explore"
              ? `/explore?${workflowSearch(searchParams, { project: projectId, ...(contextId ? { context: contextId, lat: null, lng: null, width: null } : site ? { lat: String(site.latitude), lng: String(site.longitude), width: String(site.width_km) } : {}) })}`
              : workflowPath(projectId, route, searchParams, {
                  scenario: scenarioId,
                })
          }
          aria-current={route === active ? "step" : undefined}
        >
          <span aria-hidden="true">{index + 1}</span>
          {label}
        </Link>
      ))}
    </nav>
  );
}
