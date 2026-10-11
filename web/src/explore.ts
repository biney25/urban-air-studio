import type { FeatureCollection, Geometry } from "geojson";

export interface RegionCenter {
  longitude: number;
  latitude: number;
}
export interface RegionBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}
export interface RegionSelection {
  center: RegionCenter;
  bounds: RegionBounds;
}
export interface ExploreCatalogue {
  schema_version: "explore-providers-v1";
  mode: string;
  max_area_km2: number;
  providers: ExploreProvider[];
}
export interface ExploreProvider {
  id: string;
  name: string;
  category: string;
  status: string;
  source_url: string;
  licence: string;
  attribution: string;
  warnings: string[];
  rights: {
    display: boolean;
    solver_input: false;
    export_data: false;
    use_scope: string;
  };
  limits: Record<string, unknown>;
}
export interface ProviderSnapshot {
  provider_id: string;
  status: string;
  id: string;
  fetched_at: string | null;
  source_url: string;
  licence: string;
  attribution: string;
  raw_sha256: string | null;
  cached: boolean;
  missing_fields: string[];
  retry_after_seconds: number | null;
  warnings: string[];
}
export interface ExploreContext {
  schema_version: string;
  id: string;
  project_id: string | null;
  content_hash: string;
  created_at: string;
  warnings: string[];
  region: RegionSelection;
  snapshots: ProviderSnapshot[];
  geometry:
    | ({
        building_count: number;
        road_count: number;
        missing_height_count: number;
        excluded_relation_count: number;
        invalid_object_count: number;
      } & FeatureCollection<Geometry>)
    | null;
  weather: {
    valid_at_utc: string;
    temperature_2m_c: number | null;
    wind_speed_10m_m_s: number | null;
    wind_from_10m_deg: number | null;
    model: string;
    spatial_support: string;
    returned_grid_center: RegionCenter;
  } | null;
  scientific_readiness: {
    status: string;
    blockers: string[];
    qualified: false;
  };
}

export const DELHI = { longitude: 77.209, latitude: 28.6139 };
export const INDIA = { longitude: 78.9629, latitude: 20.5937 };
export const PLACE_PRESETS = [
  { name: "Delhi", center: DELHI, zoom: 12 },
  { name: "India", center: INDIA, zoom: 4 },
  {
    name: "Mumbai",
    center: { longitude: 72.8777, latitude: 19.076 },
    zoom: 12,
  },
  {
    name: "Bengaluru",
    center: { longitude: 77.5946, latitude: 12.9716 },
    zoom: 12,
  },
] as const;

/** Navigation extent only. Scientific geometry uses the checked server metre transform. */
export function boundsForCenter(
  center: RegionCenter,
  widthKm: number,
): RegionBounds {
  if (
    !Number.isFinite(center.longitude) ||
    !Number.isFinite(center.latitude) ||
    center.longitude < -180 ||
    center.longitude > 180 ||
    Math.abs(center.latitude) > 80
  )
    throw new Error(
      "Enter a longitude from −180 to 180 and a latitude from −80 to 80.",
    );
  if (![0.25, 0.5, 1].includes(widthKm))
    throw new Error("Choose a supported bounded extent.");
  const latitudeOffset = widthKm / (2 * 111.32);
  const longitudeOffset =
    latitudeOffset / Math.cos((center.latitude * Math.PI) / 180);
  const bounds = {
    west: center.longitude - longitudeOffset,
    east: center.longitude + longitudeOffset,
    south: center.latitude - latitudeOffset,
    north: center.latitude + latitudeOffset,
  };
  if (
    bounds.west < -180 ||
    bounds.east > 180 ||
    bounds.south < -80 ||
    bounds.north > 80
  )
    throw new Error(
      "This area crosses the supported coordinate boundary. Move its centre inward.",
    );
  return bounds;
}

export function initialSelection(params: URLSearchParams): {
  region: RegionSelection;
  widthKm: number;
} {
  const latitude = params.get("lat"),
    longitude = params.get("lng");
  const widthKm = Number(params.get("width") ?? 0.25);
  try {
    if (
      (latitude !== null && latitude.trim() === "") ||
      (longitude !== null && longitude.trim() === "")
    )
      throw new Error("Missing coordinate");
    const center =
      latitude !== null && longitude !== null
        ? { latitude: Number(latitude), longitude: Number(longitude) }
        : DELHI;
    return {
      region: { center, bounds: boundsForCenter(center, widthKm) },
      widthKm,
    };
  } catch {
    return {
      region: { center: DELHI, bounds: boundsForCenter(DELHI, 0.25) },
      widthKm: 0.25,
    };
  }
}

export function contextMatchesSelection(
  context: ExploreContext | undefined,
  region: RegionSelection,
): boolean {
  return !!context && selectionKey(context.region) === selectionKey(region);
}

export function coordinateLabel(center: RegionCenter): string {
  return `${Math.abs(center.latitude).toFixed(5)}° ${center.latitude < 0 ? "S" : "N"}, ${Math.abs(center.longitude).toFixed(5)}° ${center.longitude < 0 ? "W" : "E"}`;
}

export function safeSourceUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

export function selectionKey(region: RegionSelection): string {
  return JSON.stringify([
    region.center.longitude,
    region.center.latitude,
    region.bounds.west,
    region.bounds.south,
    region.bounds.east,
    region.bounds.north,
  ]);
}

export function sourceStatus(status: string): {
  label: string;
  tone: "cyan" | "amber" | "red" | "quiet";
} {
  switch (status) {
    case "available":
    case "ok":
    case "ready":
      return { label: "Available", tone: "cyan" };
    case "no_coverage":
    case "empty":
      return { label: "No coverage", tone: "amber" };
    case "unconfigured":
    case "not_configured":
      return { label: "Access not configured", tone: "quiet" };
    case "rate_limited":
      return { label: "Provider rate limit", tone: "amber" };
    case "timeout":
      return { label: "Provider timed out", tone: "amber" };
    case "error":
    case "unavailable":
    case "provider_error":
      return { label: "Unavailable", tone: "red" };
    case "invalid_response":
      return { label: "Response failed checks", tone: "red" };
    case "stale":
      return { label: "Saved · stale", tone: "amber" };
    case "not_requested":
      return { label: "Not requested", tone: "quiet" };
    default:
      return { label: status.replaceAll("_", " "), tone: "quiet" };
  }
}

export function geometryPath(geometry: Geometry, bounds: RegionBounds): string {
  const position = (point: number[]) =>
    `${(((point[0] - bounds.west) / (bounds.east - bounds.west)) * 600).toFixed(2)},${(400 - ((point[1] - bounds.south) / (bounds.north - bounds.south)) * 400).toFixed(2)}`;
  const line = (points: number[][], closed = false) =>
    points.length
      ? `M${points.map(position).join(" L")}${closed ? " Z" : ""}`
      : "";
  if (geometry.type === "Polygon")
    return geometry.coordinates.map((ring) => line(ring, true)).join(" ");
  if (geometry.type === "MultiPolygon")
    return geometry.coordinates
      .flatMap((polygon) => polygon.map((ring) => line(ring, true)))
      .join(" ");
  if (geometry.type === "LineString") return line(geometry.coordinates);
  if (geometry.type === "MultiLineString")
    return geometry.coordinates.map((points) => line(points)).join(" ");
  return "";
}
