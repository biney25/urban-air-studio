import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Layers,
  LocateFixed,
  MapPin,
  Search,
  ShieldCheck,
  Wind,
  X,
} from "lucide-react";
import { api } from "./api";
import { workflowPath } from "./workflowContext";
import type { Project } from "./types";
import type {
  ExploreCatalogue,
  ExploreContext,
  RegionCenter,
  RegionSelection,
} from "./explore";
import {
  boundsForCenter,
  contextMatchesSelection,
  coordinateLabel,
  initialSelection,
  PLACE_PRESETS,
} from "./explore";
import { AuthStatus } from "./components/AuthGate";
import { Badge, ErrorBox, Loading } from "./components/UI";
import { ContextSummary, ProviderReview } from "./components/ExploreReview";
import { RegionDiagram } from "./components/RegionDiagram";
import { WorkspaceNav, TaskTrail } from "./components/WorkspaceNav";
import SitePreparationPanel from "./components/SitePreparationPanel";

const ExploreMap = lazy(() => import("./components/ExploreMap"));

export default function ExplorePage() {
  const [params, setParams] = useSearchParams();
  const { region: requestedRegion, widthKm: requestedWidth } = useMemo(
    () => initialSelection(params),
    [params],
  );
  const projectId = params.get("project") ?? "";
  const contextId = params.get("context") ?? "";
  const readback = useQuery({
    queryKey: ["explore-context", contextId],
    queryFn: ({ signal }) =>
      api<ExploreContext>(
        `/explore/contexts/${encodeURIComponent(contextId)}`,
        undefined,
        signal,
      ),
    enabled: !!contextId,
    retry: false,
  });
  const restoredArea =
    contextId && readback.data && !params.has("lat")
      ? readback.data.region
      : null;
  const region = restoredArea ?? requestedRegion;
  const widthKm = restoredArea
    ? Math.round(
        Math.sqrt(
          (restoredArea.bounds.north - restoredArea.bounds.south) *
            (restoredArea.bounds.east - restoredArea.bounds.west) *
            111.32 ** 2 *
            Math.cos((restoredArea.center.latitude * Math.PI) / 180),
        ) * 1000,
      ) / 1000
    : requestedWidth;
  const [latitude, setLatitude] = useState(String(region.center.latitude));
  const [longitude, setLongitude] = useState(String(region.center.longitude));
  const [zoom, setZoom] = useState(14);
  const [cameraRevision, setCameraRevision] = useState(0);
  const [selectionError, setSelectionError] = useState<Error | null>(null);
  const [buildings, setBuildings] = useState(true),
    [roads, setRoads] = useState(true);
  const [includeGeometry, setIncludeGeometry] = useState(true),
    [includeWeather, setIncludeWeather] = useState(true);
  const [showDiagram, setShowDiagram] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const camera = useMemo(
    () => ({
      center: {
        longitude: region.center.longitude,
        latitude: region.center.latitude,
      },
      zoom,
      revision: cameraRevision,
    }),
    [region.center.longitude, region.center.latitude, zoom, cameraRevision],
  );

  useEffect(() => {
    setLatitude(String(region.center.latitude));
    setLongitude(String(region.center.longitude));
  }, [region.center.latitude, region.center.longitude]);
  useEffect(() => () => controller.current?.abort(), []);

  const catalogue = useQuery({
    queryKey: ["explore-providers"],
    queryFn: ({ signal }) =>
      api<ExploreCatalogue>("/explore/providers", undefined, signal),
    staleTime: 60_000,
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => api<Project[]>("/projects", undefined, signal),
  });

  const inspection = useMutation({
    mutationFn: async (selected: RegionSelection) => {
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      try {
        return await api<ExploreContext>(
          "/explore/region-context",
          {
            ...selected,
            include_geometry: includeGeometry,
            include_weather: includeWeather,
            ...(projectId ? { project_id: projectId } : {}),
          },
          abort.signal,
        );
      } finally {
        if (controller.current === abort) controller.current = null;
      }
    },
    onSuccess: (context) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("context", context.id);
          return next;
        },
        { replace: true },
      );
    },
  });
  const latestContext = contextId
    ? inspection.data?.id === contextId
      ? inspection.data
      : readback.data
    : inspection.data;
  const context = contextMatchesSelection(latestContext, region)
    ? latestContext
    : undefined;
  const project = projects.data?.find((item) => item.id === projectId);
  const coordinatesApplied =
    latitude.trim() !== "" &&
    longitude.trim() !== "" &&
    Number(latitude) === region.center.latitude &&
    Number(longitude) === region.center.longitude;
  const newProjectParams = new URLSearchParams({
    lat: String(region.center.latitude),
    lng: String(region.center.longitude),
    width: String(widthKm),
  });
  if (context) newProjectParams.set("context", context.id);
  if (restoredArea) {
    newProjectParams.delete("lat");
    newProjectParams.delete("lng");
    newProjectParams.delete("width");
  }

  const selectArea = useCallback(
    (
      center: RegionCenter,
      nextWidth = [0.25, 0.5, 1].includes(widthKm) ? widthKm : 0.25,
      nextZoom?: number,
    ) => {
      if (inspection.isPending) return;
      try {
        boundsForCenter(center, nextWidth);
        setLatitude(String(center.latitude));
        setLongitude(String(center.longitude));
        inspection.reset();
        setCancelled(false);
        setSelectionError(null);
        setParams(
          (current) => {
            const next = new URLSearchParams(current);
            next.set("lat", String(center.latitude));
            next.set("lng", String(center.longitude));
            next.set("width", String(nextWidth));
            next.delete("context");
            return next;
          },
          { replace: true },
        );
        if (nextZoom !== undefined) setZoom(nextZoom);
        setCameraRevision((value) => value + 1);
      } catch (error) {
        setSelectionError(error as Error);
      }
    },
    [inspection.isPending, inspection.reset, setParams, widthKm],
  );

  return (
    <div className="explore-page">
      <header className="explore-header">
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
        <WorkspaceNav
          projectId={projectId}
          contextId={contextId}
          searchParams={params}
        />
        <details className="session-disclosure">
          <summary>Session</summary>
          <AuthStatus />
        </details>
      </header>
      <main id="main-content" className="explore-main">
        <div className="explore-title">
          <div>
            <span className="eyebrow">
              {project?.name ?? "URBAN AIR PLANNING"}
            </span>
            <h1>Explore your study area</h1>
            <p>
              Choose a place on the map. Check buildings, roads and available
              weather.
            </p>
          </div>
          <Badge tone="cyan">Geographic context</Badge>
        </div>
        {projectId ? (
          <TaskTrail
            projectId={projectId}
            active="explore"
            site={{ ...region.center, width_km: widthKm }}
            contextId={contextId}
            scenarioId={params.get("scenario") ?? undefined}
            searchParams={params}
          />
        ) : null}
        <div className="explore-layout">
          <aside
            className="explore-controls"
            aria-label="Selected area and layer controls"
          >
            <div className="explore-control-heading">
              <MapPin size={18} />
              <h2>Your study area</h2>
            </div>
            <div className="explore-presets" aria-label="Location shortcuts">
              {PLACE_PRESETS.map((place) => (
                <button
                  key={place.name}
                  className="button small"
                  disabled={inspection.isPending}
                  onClick={() =>
                    selectArea(
                      place.center,
                      [0.25, 0.5, 1].includes(widthKm) ? widthKm : 0.25,
                      place.zoom,
                    )
                  }
                >
                  {place.name}
                </button>
              ))}
            </div>
            <details className="location-disclosure">
              <summary>Coordinates · advanced location</summary>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  selectArea(
                    {
                      latitude: Number(latitude),
                      longitude: Number(longitude),
                    },
                    [0.25, 0.5, 1].includes(widthKm) ? widthKm : 0.25,
                    13,
                  );
                }}
              >
                <div className="explore-coordinate-fields">
                  <label className="form-label">
                    Latitude
                    <input
                      required
                      type="number"
                      min="-80"
                      max="80"
                      step="any"
                      value={latitude}
                      disabled={inspection.isPending}
                      onChange={(event) => setLatitude(event.target.value)}
                    />
                  </label>
                  <label className="form-label">
                    Longitude
                    <input
                      required
                      type="number"
                      min="-180"
                      max="180"
                      step="any"
                      value={longitude}
                      disabled={inspection.isPending}
                      onChange={(event) => setLongitude(event.target.value)}
                    />
                  </label>
                </div>
                <button
                  className="button explore-go"
                  disabled={inspection.isPending}
                >
                  <Search size={15} /> Go to coordinates
                </button>
              </form>
              <p className="micro">
                Latitude and longitude use WGS84. City shortcuts navigate to
                examples; other places are supported.
              </p>
            </details>
            {!coordinatesApplied ? (
              <p className="micro" role="status">
                Choose “Go to coordinates” to apply these values before
                inspecting.
              </p>
            ) : null}
            {selectionError ? <ErrorBox error={selectionError} /> : null}
            <label className="form-label explore-extent">
              Area width
              <select
                value={widthKm}
                disabled={inspection.isPending}
                onChange={(event) =>
                  selectArea(region.center, Number(event.target.value))
                }
              >
                {![0.25, 0.5, 1].includes(widthKm) ? (
                  <option value={widthKm}>
                    Saved source extent · {widthKm * 1000} m square
                  </option>
                ) : null}
                <option value={0.25}>250m × 250m</option>
                <option value={0.5}>500m × 500m</option>
                <option value={1}>1km × 1km</option>
              </select>
            </label>
            <p className="micro">Choose up to 1 km² to check site data.</p>
            <button
              className="text-button"
              disabled={inspection.isPending}
              onClick={() => {
                setZoom(15 - Math.log2(widthKm));
                setCameraRevision((value) => value + 1);
              }}
            >
              <LocateFixed size={15} /> Focus selected area
            </button>
            <details className="explore-layer-controls">
              <summary>
                <Layers size={16} /> Map layers and display
              </summary>
              <h3>
                <Layers size={16} /> Context layers
              </h3>
              <label>
                <input
                  type="checkbox"
                  checked={buildings}
                  onChange={(event) => setBuildings(event.target.checked)}
                />
                <span>
                  <i className="layer-key buildings" /> Building footprints
                </span>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={roads}
                  onChange={(event) => setRoads(event.target.checked)}
                />
                <span>
                  <i className="layer-key roads" /> Road segments
                </span>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={showDiagram}
                  onChange={(event) => setShowDiagram(event.target.checked)}
                />
                <span>Coordinate preview (no WebGL)</span>
              </label>
            </details>
            <div className="explore-fetch-controls">
              <h3>Check site data</h3>
              <label>
                <input
                  type="checkbox"
                  checked={includeGeometry}
                  disabled={inspection.isPending}
                  onChange={(event) => setIncludeGeometry(event.target.checked)}
                />{" "}
                OSM geometry
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={includeWeather}
                  disabled={inspection.isPending}
                  onChange={(event) => setIncludeWeather(event.target.checked)}
                />{" "}
                Grid weather forecast
              </label>
              <button
                data-testid="inspect-region"
                className="button primary"
                disabled={
                  inspection.isPending ||
                  !catalogue.data ||
                  !coordinatesApplied ||
                  (!includeGeometry && !includeWeather)
                }
                onClick={() => {
                  setCancelled(false);
                  inspection.mutate(region);
                }}
              >
                {inspection.isPending
                  ? "Checking site data…"
                  : context
                    ? "Check site data again"
                    : "Check site data"}
                <ArrowRight size={16} />
              </button>
              {inspection.isPending ? (
                <button
                  className="text-button"
                  onClick={() => {
                    setCancelled(true);
                    controller.current?.abort();
                  }}
                >
                  <X size={14} /> Cancel request
                </button>
              ) : null}
              {context ? (
                <a className="text-button" href="#site-preparation">
                  {projectId
                    ? "Prepare this site"
                    : "Create a project for this site"}
                  <ArrowRight size={16} />
                </a>
              ) : null}
            </div>
          </aside>
          <section
            className="explore-map-section"
            aria-label="Geographic map and selected area"
          >
            <div className="explore-map-topline">
              <span>
                <MapPin size={14} />
                {coordinateLabel(region.center)}
              </span>
              <span>{widthKm}km square · WGS84 navigation extent</span>
            </div>
            {showDiagram ? (
              <RegionDiagram
                region={region}
                context={context}
                buildings={buildings}
                roads={roads}
              />
            ) : (
              <Suspense
                fallback={
                  <div className="explore-map-loading">
                    <Loading label="Loading map viewer…" />
                    <RegionDiagram region={region} />
                  </div>
                }
              >
                <ExploreMap
                  region={region}
                  camera={camera}
                  context={context}
                  buildings={buildings}
                  roads={roads}
                  onSelect={(center) => selectArea(center)}
                />
              </Suspense>
            )}
            <div className="explore-map-caption">
              <ShieldCheck size={16} />
              <span>
                Basemap is visual context. Independently fetched footprints and
                roads remain unverified; no pollution field or environmental
                benefit is implied.
              </span>
            </div>
            <div className="explore-map-attribution">
              <a
                href="https://openfreemap.org/"
                target="_blank"
                rel="noreferrer"
              >
                OpenFreeMap
              </a>{" "}
              ·{" "}
              <a
                href="https://openmaptiles.org/"
                target="_blank"
                rel="noreferrer"
              >
                © OpenMapTiles
              </a>{" "}
              ·{" "}
              <a
                href="https://www.openstreetmap.org/copyright"
                target="_blank"
                rel="noreferrer"
              >
                © OpenStreetMap contributors
              </a>
            </div>
          </section>
        </div>
        <section
          className="explore-evidence"
          aria-labelledby="region-evidence-heading"
        >
          <div className="explore-evidence-heading">
            <div>
              <span className="eyebrow">INPUT REVIEW</span>
              <h2 id="region-evidence-heading">Site data</h2>
            </div>
            {context ? (
              <Badge tone="cyan">
                <Check size={12} /> Context saved privately
              </Badge>
            ) : (
              <Badge>Area not inspected</Badge>
            )}
          </div>
          {inspection.isPending ? (
            <Loading label="Requesting bounded provider data. Public provider limits may apply…" />
          ) : null}
          {cancelled ? (
            <p role="status" className="explore-notice">
              Request cancelled in this browser. Any server-side committed
              context is retained; no existing study was changed.
            </p>
          ) : inspection.error ? (
            <ErrorBox error={inspection.error} />
          ) : null}
          {readback.isPending && contextId ? (
            <Loading label="Reading saved regional context…" />
          ) : null}
          {readback.error ? <ErrorBox error={readback.error} /> : null}
          {latestContext && !context ? (
            <p role="status" className="explore-notice">
              The saved context covers another area. Its data has not been shown
              over this selection; inspect this area to create a matching
              record.
            </p>
          ) : null}
          {context ? (
            <ContextSummary context={context} />
          ) : (
            <p className="explore-context-note">
              Inspect the selected area to review real provider responses. An
              available connector does not guarantee local coverage. Monitoring,
              terrain and background adapters stay visibly unconfigured until
              their access and permissions are qualified.
            </p>
          )}
          {catalogue.isPending ? (
            <Loading label="Loading the provider catalogue…" />
          ) : catalogue.error ? (
            <ErrorBox
              error={catalogue.error}
              onRetry={() => void catalogue.refetch()}
            />
          ) : (
            <details className="source-disclosure">
              <summary>Source coverage, permissions and evidence</summary>
              <ProviderReview
                providers={catalogue.data?.providers ?? []}
                context={context}
              />
            </details>
          )}
          {context ? (
            <details className="explore-manifest">
              <summary>
                Saved context identity and warnings <ChevronDown size={14} />
              </summary>
              <dl>
                <dt>Context ID</dt>
                <dd>
                  <code>{context.id}</code>
                </dd>
                <dt>Content hash</dt>
                <dd>
                  <code>{context.content_hash}</code>
                </dd>
                <dt>Created</dt>
                <dd>{context.created_at}</dd>
              </dl>
              <ul>
                {context.warnings.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </section>
        <section
          id="site-preparation"
          className="explore-next-step"
          aria-labelledby="next-step-heading"
        >
          <div>
            <span className="eyebrow">FROM AREA TO STUDY</span>
            <h2 id="next-step-heading">Prepare this site</h2>
            <p>
              Review each building's height and assumptions before saving a
              baseline. Roads are context; emissions and assessment locations
              need their own inputs.
            </p>
          </div>
          <div className="explore-project-action">
            <label className="form-label">
              Project for this area
              <select
                disabled={inspection.isPending || projects.isPending}
                value={projectId}
                onChange={(event) => {
                  inspection.reset();
                  setParams(
                    (current) => {
                      const next = new URLSearchParams(current);
                      if (event.target.value)
                        next.set("project", event.target.value);
                      else next.delete("project");
                      for (const key of [
                        "scenario",
                        "run",
                        "base",
                        "alt",
                        "assistant_session",
                        "report",
                      ])
                        next.delete(key);
                      if (
                        context?.project_id !== null &&
                        context?.project_id !== event.target.value
                      )
                        next.delete("context");
                      return next;
                    },
                    { replace: true },
                  );
                }}
              >
                <option value="">Choose an existing project</option>
                {projects.data
                  ?.filter(
                    (item) =>
                      item.category !== "internal_test" ||
                      item.id === projectId,
                  )
                  .map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
              </select>
            </label>
            {projects.error ? <ErrorBox error={projects.error} /> : null}
            {projectId ? (
              <Link
                className="button primary"
                to={workflowPath(projectId, "imports", params, {
                  explore_context: context?.id,
                })}
              >
                Add an owned site model
                <ArrowRight size={16} />
              </Link>
            ) : (
              <Link className="button" to={`/projects/new?${newProjectParams}`}>
                Create a project
                <ArrowRight size={16} />
              </Link>
            )}
            <Link className="text-button" to="/projects">
              <ArrowLeft size={14} /> Back to projects
            </Link>
          </div>
        </section>
        {context && projectId ? (
          <SitePreparationPanel
            key={`${projectId}-${context.id}`}
            projectId={projectId}
            contextId={context.id}
            hasBaseline={!!project?.baseline_scenario_id}
          />
        ) : null}
      </main>
    </div>
  );
}
