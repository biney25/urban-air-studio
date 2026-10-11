import type { ExploreContext, RegionSelection } from "../explore";
import { coordinateLabel, geometryPath } from "../explore";

export function RegionDiagram({
  region,
  context,
  buildings = true,
  roads = true,
}: {
  region: RegionSelection;
  context?: ExploreContext;
  buildings?: boolean;
  roads?: boolean;
}) {
  const id = useId().replaceAll(":", ""),
    gridId = `region-grid-${id}`,
    clipId = `region-clip-${id}`;
  return (
    <div className="region-diagram">
      <svg
        viewBox="0 0 600 400"
        role="img"
        aria-label="Coordinate diagram of the selected area. Provider geometry is shown only when successfully fetched; this diagram is not a geographic basemap."
      >
        <defs>
          <pattern
            id={gridId}
            width="50"
            height="50"
            patternUnits="userSpaceOnUse"
          >
            <path d="M50 0H0V50" fill="none" stroke="#26344d" strokeWidth="1" />
          </pattern>
          <clipPath id={clipId}>
            <rect x="0" y="0" width="600" height="400" />
          </clipPath>
        </defs>
        <rect width="600" height="400" fill="#0c1424" />
        <rect width="600" height="400" fill={`url(#${gridId})`} />
        <g clipPath={`url(#${clipId})`}>
          {context?.geometry?.features.map((feature, index) => {
            const polygon =
              feature.geometry?.type === "Polygon" ||
              feature.geometry?.type === "MultiPolygon";
            if (!feature.geometry || (polygon ? !buildings : !roads))
              return null;
            return (
              <path
                key={feature.id ?? index}
                d={geometryPath(feature.geometry, region.bounds)}
                fill={polygon ? "#ab9cf2" : "none"}
                fillOpacity="0.6"
                fillRule="evenodd"
                stroke={polygon ? "#ded4ff" : "#e7c092"}
                strokeWidth={polygon ? "1" : "2"}
              />
            );
          })}
        </g>
        <rect
          x="1"
          y="1"
          width="598"
          height="398"
          stroke="#45dce5"
          strokeWidth="2"
          strokeDasharray="7 5"
          fill="none"
        />
        <path d="M289 200h22M300 189v22" stroke="#45dce5" strokeWidth="2" />
        <circle
          cx="300"
          cy="200"
          r="17"
          stroke="#45dce5"
          strokeOpacity="0.5"
          fill="none"
        />
        <text x="575" y="30" textAnchor="middle" fill="#edf3ff" fontSize="16">
          N
        </text>
        <path d="M575 40v25m0-25-5 8m5-8 5 8" stroke="#edf3ff" fill="none" />
      </svg>
      <div className="diagram-caption">
        <strong>Selected-area coordinate preview</strong>
        <span>WGS84 · {coordinateLabel(region.center)}</span>
      </div>
    </div>
  );
}
import { useId } from "react";
