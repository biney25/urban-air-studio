import { useEffect, useRef, useState } from "react";
import {
  Map,
  NavigationControl,
  ScaleControl,
  setWorkerUrl,
} from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { LocateFixed, RefreshCw } from "lucide-react";
import type { ExploreContext, RegionCenter, RegionSelection } from "../explore";
import { RegionDiagram } from "./RegionDiagram";

setWorkerUrl(workerUrl);
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/fiord";
interface ExploreMapProps {
  region: RegionSelection;
  camera: { center: RegionCenter; zoom: number; revision: number };
  context?: ExploreContext;
  buildings: boolean;
  roads: boolean;
  onSelect: (center: RegionCenter) => void;
}

export default function ExploreMap({
  region,
  camera,
  context,
  buildings,
  roads,
  onSelect,
}: ExploreMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const onSelectRef = useRef(onSelect);
  const initialCamera = useRef(camera);
  const [state, setState] = useState<
    "loading" | "ready" | "unavailable" | "degraded"
  >("loading");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    if (!container.current) return;
    let map: Map | null = null;
    let active = true;
    setState("loading");
    const timeout = window.setTimeout(() => {
      if (active && !map?.isStyleLoaded()) setState("unavailable");
    }, 15_000);
    try {
      map = new Map({
        container: container.current,
        style: BASEMAP_STYLE,
        center: [
          initialCamera.current.center.longitude,
          initialCamera.current.center.latitude,
        ],
        zoom: initialCamera.current.zoom,
        maxZoom: 18,
        attributionControl: { compact: false },
        renderWorldCopies: false,
        cooperativeGestures: true,
      });
      mapRef.current = map;
      map.addControl(
        new NavigationControl({ visualizePitch: false }),
        "top-right",
      );
      map.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");
      map
        .getCanvas()
        .setAttribute(
          "aria-label",
          "Geographic context map. Use coordinate controls for a keyboard equivalent, or click to select an area centre.",
        );
      map.on("load", () => {
        if (active) {
          clearTimeout(timeout);
          setState("ready");
        }
      });
      map.on("error", () => {
        if (active) setState(map?.isStyleLoaded() ? "degraded" : "unavailable");
      });
      map.on("click", (event) => {
        const point = event.lngLat;
        if (Math.abs(point.lat) <= 80 && point.lng >= -180 && point.lng <= 180)
          onSelectRef.current({ longitude: point.lng, latitude: point.lat });
      });
      map.on("webglcontextlost", () => {
        if (active) setState("unavailable");
      });
    } catch {
      setState("unavailable");
    }
    const observer = new ResizeObserver(() => map?.resize());
    observer.observe(container.current);
    return () => {
      active = false;
      clearTimeout(timeout);
      observer.disconnect();
      mapRef.current = null;
      map?.remove();
    };
  }, [attempt]);

  useEffect(() => {
    mapRef.current?.jumpTo({
      center: [camera.center.longitude, camera.center.latitude],
      zoom: camera.zoom,
    });
  }, [camera]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || (state !== "ready" && state !== "degraded")) return;
    const b = region.bounds;
    const data = {
      type: "FeatureCollection" as const,
      features: [
        {
          type: "Feature" as const,
          properties: {},
          geometry: {
            type: "Polygon" as const,
            coordinates: [
              [
                [b.west, b.south],
                [b.east, b.south],
                [b.east, b.north],
                [b.west, b.north],
                [b.west, b.south],
              ],
            ],
          },
        },
      ],
    };
    const selected = map.getSource("selected-area") as
      GeoJSONSource | undefined;
    if (selected) selected.setData(data);
    else {
      map.addSource("selected-area", { type: "geojson", data });
      map.addLayer({
        id: "selected-area-fill",
        type: "fill",
        source: "selected-area",
        paint: { "fill-color": "#45dce5", "fill-opacity": 0.08 },
      });
      map.addLayer({
        id: "selected-area-outline",
        type: "line",
        source: "selected-area",
        paint: {
          "line-color": "#45dce5",
          "line-width": 2,
          "line-dasharray": [3, 2],
        },
      });
    }
    const geometryData = context?.geometry
      ? {
          type: "FeatureCollection" as const,
          features: context.geometry.features,
        }
      : { type: "FeatureCollection" as const, features: [] };
    const geometry = map.getSource("provider-geometry") as
      GeoJSONSource | undefined;
    if (geometry) geometry.setData(geometryData);
    else {
      map.addSource("provider-geometry", {
        type: "geojson",
        data: geometryData,
      });
      map.addLayer({
        id: "provider-buildings",
        type: "fill",
        source: "provider-geometry",
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: {
          "fill-color": "#ab9cf2",
          "fill-opacity": 0.6,
          "fill-outline-color": "#ded4ff",
        },
      });
      map.addLayer({
        id: "provider-roads",
        type: "line",
        source: "provider-geometry",
        filter: ["==", ["geometry-type"], "LineString"],
        paint: { "line-color": "#e7c092", "line-width": 2 },
      });
    }
    map.setLayoutProperty(
      "provider-buildings",
      "visibility",
      buildings ? "visible" : "none",
    );
    map.setLayoutProperty(
      "provider-roads",
      "visibility",
      roads ? "visible" : "none",
    );
  }, [region, context, buildings, roads, state]);

  return (
    <div className="explore-map-frame">
      <div className="explore-map-canvas" ref={container} />
      {state === "unavailable" ? (
        <div className="explore-map-fallback">
          <RegionDiagram
            region={region}
            context={context}
            buildings={buildings}
            roads={roads}
          />
          <div className="map-fallback-message" role="status">
            <strong>Geographic basemap unavailable</strong>
            <p>
              Graphics or the map connection could not load. Your selected
              coordinates and provider review remain usable below.
            </p>
            <button
              className="button small"
              onClick={() => {
                initialCamera.current = camera;
                setAttempt((value) => value + 1);
              }}
            >
              <RefreshCw size={14} /> Retry basemap
            </button>
          </div>
        </div>
      ) : null}
      {state === "loading" ? (
        <div className="explore-map-status" role="status">
          Loading geographic basemap…
        </div>
      ) : null}
      {state === "degraded" ? (
        <div className="explore-map-status" role="status">
          Some map tiles could not load. Provider evidence is reviewed
          separately.
        </div>
      ) : null}
      {state === "ready" || state === "degraded" ? (
        <button
          className="map-select-center button small"
          onClick={() => {
            const center = mapRef.current?.getCenter();
            if (center && Math.abs(center.lat) <= 80)
              onSelect({ longitude: center.lng, latitude: center.lat });
          }}
        >
          <LocateFixed size={14} /> Use map centre
        </button>
      ) : null}
    </div>
  );
}
