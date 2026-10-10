import { useEffect, useRef } from "react";
import {
  Cartesian2,
  Cartesian3,
  Color,
  EllipsoidTerrainProvider,
  HeadingPitchRange,
  Ion,
  Math as CesiumMath,
  Matrix4,
  PolygonHierarchy,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  Transforms,
  Viewer,
  ImageMaterialProperty,
  Entity,
} from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";
import { sceneExtent, fieldColor } from "../geometry";
import type { SceneProps } from "./SceneView";
import { sceneResources } from "../sceneResources";
export default function CesiumScene(props: SceneProps) {
  const host = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<Viewer | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const originRef = useRef("");
  useEffect(() => {
    if (!host.current) return;
    let viewer: Viewer;
    let handler: ScreenSpaceEventHandler;
    Ion.defaultAccessToken = "";
    try {
      viewer = new Viewer(host.current, {
        baseLayer: false,
        terrainProvider: new EllipsoidTerrainProvider(),
        animation: false,
        timeline: false,
        baseLayerPicker: false,
        geocoder: false,
        homeButton: false,
        sceneModePicker: false,
        navigationHelpButton: false,
        fullscreenButton: false,
        infoBox: false,
        selectionIndicator: false,
        skyBox: false,
        skyAtmosphere: false,
        globe: false,
        requestRenderMode: true,
        maximumRenderTimeChange: Infinity,
        contextOptions: { webgl: { alpha: false, antialias: true } },
      });
      viewerRef.current = viewer;
      viewer.scene.backgroundColor = Color.fromCssColorString("#080d1b");
      viewer.scene.sun = undefined;
      viewer.scene.moon = undefined;
      viewer.scene.fog.enabled = false;
      viewer.resolutionScale = Math.min(1, 1 / window.devicePixelRatio);
      viewer.scene.screenSpaceCameraController.enableCollisionDetection = false;
      handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
      sceneResources.activeViewers++;
      sceneResources.activeHandlers++;
      sceneResources.mounts++;
      const rendered = viewer.scene.postRender.addEventListener(() => {
        if (host.current) host.current.dataset.ready = "true";
      });
      handler.setInputAction((event: { position: Cartesian2 }) => {
        const picked = viewer.scene.pick(event.position);
        const entity = picked?.id;
        if (entity instanceof Entity) {
          const kind = entity.properties?.kind?.getValue();
          if (["building", "source", "receptor"].includes(kind))
            propsRef.current.onSelect({ kind, id: entity.id });
        }
      }, ScreenSpaceEventType.LEFT_CLICK);
      const lost = (e: Event) => {
        e.preventDefault();
        propsRef.current.onFailure?.("The graphics context was interrupted");
      };
      viewer.scene.canvas.addEventListener("webglcontextlost", lost);
      return () => {
        viewerRef.current = null;
        rendered();
        handler.destroy();
        viewer.scene.canvas.removeEventListener("webglcontextlost", lost);
        if (!viewer.isDestroyed()) viewer.destroy();
        sceneResources.activeViewers--;
        sceneResources.activeHandlers--;
        sceneResources.activeEntities = 0;
        sceneResources.activeFieldBytes = 0;
        sceneResources.unmounts++;
      };
    } catch (error) {
      const current = viewerRef.current;
      if (current && !current.isDestroyed()) current.destroy();
      viewerRef.current = null;
      propsRef.current.onFailure?.(
        error instanceof Error
          ? "3D graphics are unavailable"
          : "This device does not support the 3D scene",
      );
    }
  }, []);
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;
    const { scenario, field, selected, scaleMax } = props;
    viewer.entities.removeAll();
    const origin = Cartesian3.fromDegrees(
      scenario.origin.longitude,
      scenario.origin.latitude,
      0,
    );
    const transform = Transforms.eastNorthUpToFixedFrame(origin);
    const point = (x: number, y: number, z = 0) =>
      Matrix4.multiplyByPoint(
        transform,
        new Cartesian3(x, y, z),
        new Cartesian3(),
      );
    const [xmin, ymin, xmax, ymax] = field?.extent ?? sceneExtent(scenario);
    const groundCorners = [
      point(xmin, ymin, -0.2),
      point(xmax, ymin, -0.2),
      point(xmax, ymax, -0.2),
      point(xmin, ymax, -0.2),
    ];
    viewer.entities.add({
      polygon: {
        hierarchy: new PolygonHierarchy(groundCorners),
        perPositionHeight: true,
        material: Color.fromCssColorString("#142137"),
      },
    });
    const gridColor = Color.fromCssColorString("#526077").withAlpha(0.18);
    for (let x = Math.ceil(xmin / 20) * 20; x <= xmax; x += 20)
      viewer.entities.add({
        polyline: {
          positions: [point(x, ymin), point(x, ymax)],
          width: 1,
          material: gridColor,
        },
      });
    for (let y = Math.ceil(ymin / 20) * 20; y <= ymax; y += 20)
      viewer.entities.add({
        polyline: {
          positions: [point(xmin, y), point(xmax, y)],
          width: 1,
          material: gridColor,
        },
      });
    viewer.entities.add({
      polyline: {
        positions: [
          point(xmin, ymin),
          point(xmax, ymin),
          point(xmax, ymax),
          point(xmin, ymax),
          point(xmin, ymin),
        ],
        width: 1,
        material: Color.fromCssColorString("#697594"),
      },
    });
    if (field) {
      const canvas = document.createElement("canvas");
      canvas.width = field.nx;
      canvas.height = field.ny;
      const ctx = canvas.getContext("2d");
      if (ctx) {
        for (let y = 0; y < field.ny; y++)
          for (let x = 0; x < field.nx; x++) {
            const i = y * field.nx + x,
              value = field.values[i];
            ctx.fillStyle =
              field.mask[i] && value !== null
                ? fieldColor(value, scaleMax)
                : "rgba(0,0,0,0)";
            ctx.fillRect(x, field.ny - y - 1, 1, 1);
          }
        viewer.entities.add({
          polygon: {
            hierarchy: new PolygonHierarchy([
              point(xmin, ymin, field.height_m),
              point(xmax, ymin, field.height_m),
              point(xmax, ymax, field.height_m),
              point(xmin, ymax, field.height_m),
            ]),
            perPositionHeight: true,
            material: new ImageMaterialProperty({
              image: canvas,
              transparent: true,
              color: Color.WHITE.withAlpha(0.8),
            }),
          },
        });
      }
    }
    for (const b of scenario.buildings) {
      const focused = selected?.id === b.id;
      viewer.entities.add({
        id: b.id,
        name: b.name,
        properties: { kind: "building" },
        polygon: {
          hierarchy: new PolygonHierarchy(
            b.footprint.map((p) => point(p[0], p[1])),
          ),
          height: 0,
          extrudedHeight: b.height_m,
          material: Color.fromCssColorString(focused ? "#8d83c0" : "#a79ada"),
          outline: true,
          outlineColor: Color.fromCssColorString(
            focused ? "#45dce5" : "#c5b8ef",
          ),
          closeTop: true,
          closeBottom: true,
        },
      });
    }
    for (const s of scenario.sources)
      viewer.entities.add({
        id: s.id,
        name: s.name,
        properties: { kind: "source" },
        position: point(s.x, s.y, s.z),
        point: {
          pixelSize: selected?.id === s.id ? 14 : 10,
          color: Color.fromCssColorString("#edba72"),
          outlineColor: Color.fromCssColorString("#191f30"),
          outlineWidth: 3,
        },
        label: {
          text: s.name,
          font: "12px sans-serif",
          fillColor: Color.fromCssColorString("#f6d7a9"),
          showBackground: true,
          backgroundColor: Color.fromCssColorString("#111a2d").withAlpha(0.9),
          pixelOffset: { x: 0, y: -22 } as never,
        },
      });
    for (const r of scenario.receptors)
      viewer.entities.add({
        id: r.id,
        name: r.name,
        properties: { kind: "receptor" },
        position: point(r.x, r.y, r.z),
        point: {
          pixelSize: selected?.id === r.id ? 14 : 9,
          color: Color.fromCssColorString("#87e5d9"),
          outlineColor: Color.fromCssColorString("#162235"),
          outlineWidth: 3,
        },
        label: {
          text: r.name,
          font: "12px sans-serif",
          fillColor: Color.fromCssColorString("#d6f7f2"),
          showBackground: true,
          backgroundColor: Color.fromCssColorString("#111a2d").withAlpha(0.9),
          pixelOffset: { x: 0, y: -20 } as never,
        },
      });
    const key = `${scenario.origin.longitude},${scenario.origin.latitude}`;
    if (originRef.current !== key) {
      originRef.current = key;
      const span = Math.max(xmax - xmin, ymax - ymin);
      viewer.camera.lookAt(
        point((xmin + xmax) / 2, (ymin + ymax) / 2, 0),
        new HeadingPitchRange(
          CesiumMath.toRadians(-24),
          CesiumMath.toRadians(-40),
          span * 1.6,
        ),
      );
      viewer.camera.lookAtTransform(Matrix4.IDENTITY);
    }
    viewer.scene.requestRender();
    sceneResources.activeEntities = viewer.entities.values.length;
    sceneResources.activeFieldBytes = field ? field.nx * field.ny * 4 : 0;
    sceneResources.updates++;
  }, [props.scenario, props.field, props.selected, props.scaleMax]);
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;
    const s = props.scenario;
    const [xmin, ymin, xmax, ymax] = sceneExtent(s);
    const origin = Cartesian3.fromDegrees(
      s.origin.longitude,
      s.origin.latitude,
      0,
    );
    const transform = Transforms.eastNorthUpToFixedFrame(origin);
    const focus = Matrix4.multiplyByPoint(
      transform,
      new Cartesian3((xmin + xmax) / 2, (ymin + ymax) / 2, 0),
      new Cartesian3(),
    );
    const span = Math.max(xmax - xmin, ymax - ymin);
    viewer.camera.lookAt(
      focus,
      new HeadingPitchRange(
        CesiumMath.toRadians(props.camera === "plan" ? 0 : -24),
        CesiumMath.toRadians(props.camera === "plan" ? -90 : -40),
        span * (props.camera === "overview" ? 2.2 : 1.6),
      ),
    );
    viewer.camera.lookAtTransform(Matrix4.IDENTITY);
    viewer.scene.requestRender();
  }, [props.camera, props.reset]);
  return (
    <div
      ref={host}
      className="cesium-host"
      data-testid="cesium-map"
      aria-label="3D city model. Use the object list for keyboard selection."
    />
  );
}
