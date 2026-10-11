import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { MapPin } from "lucide-react";
import { api } from "../api";
import type { ExploreContext } from "../explore";
import { coordinateLabel } from "../explore";
import { Badge, ErrorBox, Loading } from "./UI";

export default function ExploreHandoff({
  contextId,
  projectId,
}: {
  contextId: string;
  projectId: string;
}) {
  const query = useQuery({
    queryKey: ["explore-context", contextId],
    queryFn: ({ signal }) =>
      api<ExploreContext>(
        `/explore/contexts/${encodeURIComponent(contextId)}`,
        undefined,
        signal,
      ),
    retry: false,
  });
  if (query.isPending)
    return <Loading label="Reading selected-area context…" />;
  if (query.error) return <ErrorBox error={query.error} />;
  const context = query.data;
  if (context.project_id && context.project_id !== projectId)
    return (
      <p className="explore-notice">
        This regional context is not linked to the current project. No provider
        geometry or permissions have been imported.
      </p>
    );
  const widthKm = Math.sqrt(
    (context.region.bounds.north - context.region.bounds.south) *
      (context.region.bounds.east - context.region.bounds.west) *
      111.32 ** 2 *
      Math.cos((context.region.center.latitude * Math.PI) / 180),
  );
  const nearestWidth = [0.25, 0.5, 1].reduce(
    (best, width) =>
      Math.abs(width - widthKm) < Math.abs(best - widthKm) ? width : best,
    1,
  );
  const params = new URLSearchParams({
    context: context.id,
    project: projectId,
    lat: String(context.region.center.latitude),
    lng: String(context.region.center.longitude),
    width: String(nearestWidth),
  });
  return (
    <section
      className="explore-handoff"
      aria-labelledby="explore-handoff-heading"
    >
      <MapPin size={21} />
      <div>
        <h2 id="explore-handoff-heading">Selected area retained for review</h2>
        <p>
          {coordinateLabel(context.region.center)} ·{" "}
          {context.geometry?.missing_height_count ?? "Unreviewed"} missing
          building heights
        </p>
        <p>
          Review the selected area's buildings and missing heights in site
          preparation, or add an independently permitted model below. Fetched
          context does not by itself grant solver or export rights.
        </p>
        <Badge tone="amber">Unverified geographic context</Badge>
        <Link className="text-button" to={`/explore?${params}`}>
          Review buildings and prepare this site
        </Link>
      </div>
    </section>
  );
}
