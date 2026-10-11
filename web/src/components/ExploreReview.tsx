import { ArrowUpRight, Database, ShieldAlert, Wind } from "lucide-react";
import type { ExploreContext, ExploreProvider } from "../explore";
import { coordinateLabel, safeSourceUrl, sourceStatus } from "../explore";
import { Badge } from "./UI";

function SourceLink({ url, children }: { url: string; children: string }) {
  const safe = safeSourceUrl(url);
  return safe ? (
    <a
      className="provider-source-link"
      href={safe}
      target="_blank"
      rel="noreferrer"
    >
      {children}
      <ArrowUpRight size={13} />
    </a>
  ) : (
    <span>{children}</span>
  );
}

export function ProviderReview({
  providers,
  context,
}: {
  providers: ExploreProvider[];
  context?: ExploreContext;
}) {
  return (
    <div
      className="explore-provider-table-wrap"
      tabIndex={0}
      role="region"
      aria-label="Provider coverage, access and provenance table"
    >
      <table className="explore-provider-table">
        <caption>Source coverage for the selected area</caption>
        <thead>
          <tr>
            <th scope="col">Provider / layer</th>
            <th scope="col">Coverage / access</th>
            <th scope="col">Freshness and retained evidence</th>
            <th scope="col">Use and missing inputs</th>
          </tr>
        </thead>
        <tbody>
          {providers.map((provider) => {
            const snapshot = context?.snapshots.find(
              (item) => item.provider_id === provider.id,
            );
            const status = sourceStatus(snapshot?.status ?? provider.status);
            const awaitingInspection =
              !snapshot && provider.status === "available";
            return (
              <tr key={provider.id}>
                <th scope="row">
                  <SourceLink
                    url={provider.source_url}
                    children={provider.name}
                  />
                  <small>{provider.category.replaceAll("_", " ")}</small>
                </th>
                <td>
                  <Badge tone={awaitingInspection ? "quiet" : status.tone}>
                    {awaitingInspection
                      ? context
                        ? "Not requested"
                        : "Ready to inspect"
                      : status.label}
                  </Badge>
                  {snapshot?.retry_after_seconds ? (
                    <small>
                      Wait at least {snapshot.retry_after_seconds} seconds
                      before another request.
                    </small>
                  ) : null}
                </td>
                <td>
                  {snapshot?.fetched_at ? (
                    <>
                      <time dateTime={snapshot.fetched_at}>
                        {new Date(snapshot.fetched_at).toLocaleString("en-IN", {
                          dateStyle: "medium",
                          timeStyle: "short",
                        })}
                      </time>
                      <small>
                        {snapshot.cached
                          ? "Reused saved provider response"
                          : "Provider response retained"}
                      </small>
                    </>
                  ) : (
                    <span className="quiet">
                      {awaitingInspection
                        ? "No request made for this area"
                        : "No qualified regional record"}
                    </span>
                  )}
                  {snapshot?.raw_sha256 ? (
                    <details>
                      <summary>Response identity</summary>
                      <code>{snapshot.raw_sha256}</code>
                      <small>
                        Exact raw-response SHA256. Retained privately.
                      </small>
                    </details>
                  ) : null}
                </td>
                <td>
                  <span className="provider-licence">{provider.licence}</span>
                  <small>{provider.attribution}</small>
                  <details>
                    <summary>Permissions and limitations</summary>
                    <p>{provider.rights.use_scope}</p>
                    <ul>
                      {[
                        ...provider.warnings,
                        ...(snapshot?.warnings ?? []),
                        ...(snapshot?.missing_fields.map(
                          (field) => `Missing: ${field.replaceAll("_", " ")}`,
                        ) ?? []),
                      ]
                        .filter(
                          (item, index, list) => list.indexOf(item) === index,
                        )
                        .map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                    </ul>
                  </details>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ContextSummary({ context }: { context: ExploreContext }) {
  const geometry = context.geometry,
    weather = context.weather;
  return (
    <>
      <div className="explore-context-stats">
        <div>
          <Database size={17} />
          <span>Building footprints</span>
          <strong>
            {geometry
              ? geometry.building_count.toLocaleString("en-IN")
              : "Unavailable"}
          </strong>
        </div>
        <div>
          <span>Road segments</span>
          <strong>
            {geometry
              ? geometry.road_count.toLocaleString("en-IN")
              : "Unavailable"}
          </strong>
        </div>
        <div
          className={geometry?.missing_height_count ? "has-missing-inputs" : ""}
        >
          <span>Missing building heights</span>
          <strong>
            {geometry
              ? geometry.missing_height_count.toLocaleString("en-IN")
              : "Not checked"}
          </strong>
        </div>
        <div>
          <span>Study readiness</span>
          <strong>Inputs need review</strong>
        </div>
      </div>
      {geometry ? (
        <p className="explore-context-note">
          Community footprints and height tags are unverified.{" "}
          {geometry.excluded_relation_count} multipolygon relations excluded;{" "}
          {geometry.invalid_object_count} objects failed geometry checks. Road
          geometry contains no measured traffic or emissions.
        </p>
      ) : null}
      {weather ? (
        <section
          className="explore-weather"
          aria-labelledby="weather-context-heading"
        >
          <div>
            <Wind size={20} />
            <h3 id="weather-context-heading">Grid forecast context</h3>
            <Badge tone="quiet">{weather.model.replaceAll("_", " ")}</Badge>
          </div>
          <dl>
            <div>
              <dt>Temperature at 2m</dt>
              <dd>
                {weather.temperature_2m_c === null
                  ? "Missing"
                  : `${weather.temperature_2m_c.toFixed(1)} °C`}
              </dd>
            </div>
            <div>
              <dt>Wind speed at 10m</dt>
              <dd>
                {weather.wind_speed_10m_m_s === null
                  ? "Missing"
                  : `${weather.wind_speed_10m_m_s.toFixed(1)} m/s`}
              </dd>
            </div>
            <div>
              <dt>Wind from</dt>
              <dd>
                {weather.wind_from_10m_deg === null
                  ? "Missing"
                  : `${weather.wind_from_10m_deg.toFixed(0)}°`}
              </dd>
            </div>
            <div>
              <dt>Valid time (UTC)</dt>
              <dd>{weather.valid_at_utc}</dd>
            </div>
          </dl>
          <p>
            {weather.spatial_support} Returned grid centre:{" "}
            {coordinateLabel(weather.returned_grid_center)}. Model cycle and
            lead time remain missing; this is not an observed site inlet.
          </p>
        </section>
      ) : null}
      <section
        className="explore-readiness"
        aria-labelledby="readiness-heading"
      >
        <ShieldAlert size={20} />
        <div>
          <h3 id="readiness-heading">
            Review required before a scientific study
          </h3>
          <p>
            This saved context does not establish a runnable or scientifically
            qualified baseline.
          </p>
          <p>Qualified PM or AQI prediction: Not available.</p>
          <ul>
            {context.scientific_readiness.blockers.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </section>
    </>
  );
}
