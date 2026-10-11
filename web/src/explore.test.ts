import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  boundsForCenter,
  contextMatchesSelection,
  coordinateLabel,
  DELHI,
  geometryPath,
  initialSelection,
  safeSourceUrl,
  sourceStatus,
} from "./explore";
import type { ExploreContext, ExploreProvider } from "./explore";
import { ContextSummary, ProviderReview } from "./components/ExploreReview";
import { RegionDiagram } from "./components/RegionDiagram";

const region = { center: DELHI, bounds: boundsForCenter(DELHI, 1) };
const provider: ExploreProvider = {
  id: "osm_overpass",
  name: "OpenStreetMap / Overpass",
  category: "geometry",
  status: "available",
  source_url: "https://overpass-api.de/api/interpreter",
  licence: "ODbL 1.0",
  attribution: "© OpenStreetMap contributors",
  warnings: ["Coverage and heights are unverified."],
  rights: {
    display: true,
    solver_input: false,
    export_data: false,
    use_scope: "Manual bounded evaluation only",
  },
  limits: {},
};
const context: ExploreContext = {
  id: "context-test",
  project_id: "project-test",
  schema_version: "explore-context-v1",
  created_at: "2026-10-10T12:00:00Z",
  content_hash: "sha256:" + "3".repeat(64),
  region,
  snapshots: [
    {
      id: "snapshot-osm",
      provider_id: provider.id,
      status: "available",
      fetched_at: "2026-10-10T12:00:00Z",
      source_url: provider.source_url,
      licence: provider.licence,
      attribution: provider.attribution,
      raw_sha256: "sha256:" + "1".repeat(64),
      cached: false,
      missing_fields: ["building_height"],
      retry_after_seconds: null,
      warnings: ["Missing heights were not invented."],
    },
  ],
  geometry: {
    type: "FeatureCollection",
    building_count: 2,
    road_count: 1,
    missing_height_count: 2,
    excluded_relation_count: 1,
    invalid_object_count: 0,
    features: [
      {
        type: "Feature",
        properties: { height_m: null },
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [77.208, 28.613],
              [77.21, 28.613],
              [77.21, 28.615],
              [77.208, 28.613],
            ],
          ],
        },
      },
    ],
  },
  weather: null,
  warnings: ["WGS84 navigation extent only"],
  scientific_readiness: {
    status: "blocked",
    qualified: false,
    blockers: [
      "Source activity and receptors are missing.",
      "Scientific qualification remains incomplete.",
    ],
  },
};

describe("bounded geographic selection", () => {
  it.each([0.25, 0.5, 1])(
    "retains a bounded %s km square with longitude/latitude axis order",
    (width) => {
      const bounds = boundsForCenter(DELHI, width);
      const h = (bounds.north - bounds.south) * 111.32;
      const w =
        (bounds.east - bounds.west) *
        111.32 *
        Math.cos((DELHI.latitude * Math.PI) / 180);
      expect(h).toBeCloseTo(width, 8);
      expect(w).toBeCloseTo(width, 8);
      expect(bounds.west).toBeLessThan(DELHI.longitude);
      expect(bounds.south).toBeLessThan(DELHI.latitude);
    },
  );
  it.each([
    { longitude: NaN, latitude: 20 },
    { longitude: 200, latitude: 20 },
    { longitude: 0, latitude: Infinity },
    { longitude: 0, latitude: 81 },
    { longitude: 180, latitude: 0 },
    { longitude: -180, latitude: 0 },
  ])(
    "rejects invalid or boundary-crossing area %j without provider requests",
    (center) => expect(() => boundsForCenter(center, 1)).toThrow(),
  );
  it("rejects bulk area sizes instead of silently expanding provider queries", () => {
    expect(() => boundsForCenter(DELHI, 2)).toThrow();
  });
  it("supports a separate real geography instead of forcing all studies to Delhi", () => {
    const selected = initialSelection(
      new URLSearchParams("lat=19.076&lng=72.8777&width=0.5"),
    );
    expect(selected.region.center).toEqual({
      latitude: 19.076,
      longitude: 72.8777,
    });
    expect(selected.widthKm).toBe(0.5);
  });
  it.each([
    "lat=&lng=12",
    "lat=NaN&lng=77",
    "lat=0&lng=180",
    "lat=10&lng=20&width=100",
  ])("safely resets malformed navigation %s", (query) =>
    expect(initialSelection(new URLSearchParams(query)).region.center).toEqual(
      DELHI,
    ),
  );
  it("does not show retained provider geometry over another selected area", () => {
    expect(contextMatchesSelection(context, region)).toBe(true);
    const other = {
      center: { longitude: 72.8777, latitude: 19.076 },
      bounds: boundsForCenter({ longitude: 72.8777, latitude: 19.076 }, 1),
    };
    expect(contextMatchesSelection(context, other)).toBe(false);
    expect(contextMatchesSelection(undefined, region)).toBe(false);
  });
  it("formats signed coordinates with the correct hemisphere", () =>
    expect(coordinateLabel({ longitude: -72, latitude: -19 })).toBe(
      "19.00000° S, 72.00000° W",
    ));
});

describe("actual source state and scientific limits", () => {
  it("does not confuse connector availability with local coverage", () => {
    const html = renderToStaticMarkup(
      createElement(ProviderReview, { providers: [provider] }),
    );
    expect(html).toContain("Ready to inspect");
    expect(html).toContain("No request made for this area");
    expect(html).not.toContain("Context saved");
  });
  it("marks a skipped layer as not requested in a weather-only record", () => {
    const html = renderToStaticMarkup(
      createElement(ProviderReview, {
        providers: [provider],
        context: { ...context, snapshots: [], geometry: null },
      }),
    );
    expect(html).toContain("Not requested");
    expect(html).not.toContain("Ready to inspect");
  });
  it("retains attribution, response hash, freshness, missing fields and use limitations", () => {
    const html = renderToStaticMarkup(
      createElement(ProviderReview, { providers: [provider], context }),
    );
    for (const expected of [
      "OpenStreetMap contributors",
      "ODbL 1.0",
      context.snapshots[0].raw_sha256!,
      "Manual bounded evaluation only",
      "Missing: building height",
      "Provider response retained",
    ])
      expect(html).toContain(expected);
  });
  it.each([
    "no_coverage",
    "not_configured",
    "rate_limited",
    "timeout",
    "invalid_response",
    "provider_error",
    "stale",
  ])("provides an explicit %s state", (status) => {
    const html = renderToStaticMarkup(
      createElement(ProviderReview, {
        providers: [provider],
        context: {
          ...context,
          snapshots: [
            {
              ...context.snapshots[0],
              status,
              retry_after_seconds: status === "rate_limited" ? 30 : null,
            },
          ],
        },
      }),
    );
    expect(html).toContain(sourceStatus(status).label);
    if (status === "rate_limited")
      expect(html).toContain("Wait at least 30 seconds");
  });
  it("shows incomplete heights and no qualified PM or AQI instead of fabricating usable values", () => {
    const html = renderToStaticMarkup(
      createElement(ContextSummary, { context }),
    );
    expect(html).toContain("Missing building heights");
    expect(html).toContain("Not available");
    expect(html).toContain(
      "does not establish a runnable or scientifically qualified baseline",
    );
    expect(html).toContain("Source activity and receptors are missing.");
  });
  it("keeps zero weather values and grid metadata distinct from observations", () => {
    const html = renderToStaticMarkup(
      createElement(ContextSummary, {
        context: {
          ...context,
          weather: {
            valid_at_utc: "2026-10-10T12:00:00Z",
            temperature_2m_c: 0,
            wind_speed_10m_m_s: 0,
            wind_from_10m_deg: 0,
            model: "gfs_global",
            spatial_support: "0.25° grid",
            returned_grid_center: { longitude: 77.25, latitude: 28.5 },
          },
        },
      }),
    );
    expect(html).toContain("0.0 °C");
    expect(html).toContain("0.0 m/s");
    expect(html).toContain("0.25° grid");
    expect(html).toContain("not an observed site inlet");
  });
  it("rejects source link scripts, credential URLs and unsupported protocols", () => {
    for (const url of [
      "javascript:alert(1)",
      "http://example.com",
      "https://name:secret@example.com",
      "not-a-url",
    ])
      expect(safeSourceUrl(url)).toBeUndefined();
    expect(safeSourceUrl(provider.source_url)).toBe(provider.source_url);
  });
});

describe("usable coordinate fallback", () => {
  it("renders real retained footprints with geographic identity and explicit non-basemap label", () => {
    const html = renderToStaticMarkup(
      createElement(RegionDiagram, { region, context }),
    );
    expect(html).toContain("this diagram is not a geographic basemap");
    expect(html).toContain("28.61390° N, 77.20900° E");
    expect(html).toContain('fill-rule="evenodd"');
    expect(
      geometryPath(context.geometry!.features[0].geometry, region.bounds),
    ).toMatch(/^M[\d., -]+ L/);
  });
  it("keeps holes and multiple polygons separate in the fallback", () => {
    const bounds = { west: 0, south: 0, east: 10, north: 10 };
    const path = geometryPath(
      {
        type: "Polygon",
        coordinates: [
          [
            [0, 0],
            [10, 0],
            [10, 10],
            [0, 0],
          ],
          [
            [2, 2],
            [4, 2],
            [4, 4],
            [2, 2],
          ],
        ],
      },
      bounds,
    );
    expect(path.match(/M/g)).toHaveLength(2);
    expect(path.match(/Z/g)).toHaveLength(2);
  });
});
