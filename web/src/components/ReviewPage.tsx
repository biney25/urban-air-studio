import { useEffect, useRef, useState } from "react";
import { clearReviewSession, downloadReview, rememberReviewSession, reviewRequest, takeReviewCredential,
  ReviewError, type ReviewComment, type ReviewPackage } from "../review";
import "../reviewPanels.css";

function number(value: number | null | undefined): string { return value == null || !Number.isFinite(value) ? "No data" : new Intl.NumberFormat("en", { maximumSignificantDigits: 6 }).format(value); }

export default function ReviewPage() {
  const [token, setToken] = useState(() => takeReviewCredential());
  const [value, setValue] = useState<ReviewPackage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(Boolean(token));
  const [busy, setBusy] = useState(false);
  const [body, setBody] = useState("");
  const [anchor, setAnchor] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);
  const requestGeneration = useRef(0);

  function fail(cause: unknown) {
    requestGeneration.current += 1;
    setValue(null); setBody(""); setError(cause);
    if (cause instanceof ReviewError && [401, 403, 409].includes(cause.status)) { clearReviewSession(); setToken(null); }
  }
  async function refresh(signal?: AbortSignal) {
    if (!token) return;
    const generation = ++requestGeneration.current;
    try {
      const checked = await reviewRequest<ReviewPackage>(token, "report", undefined, signal);
      if (!mounted.current || signal?.aborted || generation !== requestGeneration.current) return;
      const expiry = Date.parse(checked.grant.expires_at);
      if (!Number.isFinite(expiry) || expiry <= Date.now()) throw new ReviewError("This report review grant has expired.", "REVIEW_EXPIRED", 403);
      rememberReviewSession(token, checked.grant.expires_at);
      setValue(checked); setError(null);
    } catch (cause) { if (mounted.current && !signal?.aborted && generation === requestGeneration.current) fail(cause); }
    finally { if (mounted.current && !signal?.aborted) setLoading(false); }
  }
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    if (token) void refresh(controller.signal);
    const timer = window.setInterval(() => { if (token) void refresh(controller.signal); }, 15000);
    const onVisible = () => { if (document.visibilityState === "visible" && token) void refresh(controller.signal); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { mounted.current = false; requestGeneration.current += 1; controller.abort(); window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [token]);
  useEffect(() => {
    if (!value) return;
    const remaining = Date.parse(value.grant.expires_at) - Date.now();
    const timer = window.setTimeout(() => { fail(new ReviewError("This report review grant has expired.", "REVIEW_EXPIRED", 403)); }, Math.max(0, remaining));
    return () => window.clearTimeout(timer);
  }, [value?.grant.expires_at]);

  async function comment() {
    if (!token || !value || !body.trim()) return;
    setBusy(true); setNotice("");
    try {
      await reviewRequest<ReviewComment>(token, "comments", { report_checksum: value.report.checksum,
        anchor: anchor ? { kind: "receptor", receptor_id: anchor } : { kind: "report" }, body: body.trim() });
      if (mounted.current) { setBody(""); setNotice("Comment saved against this frozen report."); await refresh(); }
    } catch (cause) { if (mounted.current) fail(cause); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function download() {
    if (!token) return;
    setBusy(true);
    try { await downloadReview(token); }
    catch (cause) { if (mounted.current) fail(cause); }
    finally { if (mounted.current) setBusy(false); }
  }
  const report = value?.report;
  const worsened = report?.comparison.metrics.filter((item) => item.protected && item.valid !== false && item.delta_ug_m3 != null && item.delta_ug_m3 > 0) || [];
  return <main id="main-content" className="report-review-page" tabIndex={-1}>
    <header className="review-page-header"><span className="review-brand">Urban Air Studio</span><span className="badge cyan">Frozen report review</span></header>
    <div className="review-scope-notice"><strong>Local review · private report link</strong><p>{value ? `Read, comment or download this report until ${new Date(value.grant.expires_at).toLocaleString()}.` : "Private review of one frozen report. Use a current link to read, comment or download."}</p>
      <details><summary>About this review link</summary><p>The link grants access to its holder on this computer. Labels do not verify a person's identity. Local machine access retains the development owner's trust boundary. Access is checked every 15 seconds while connected and on returning to this tab. Revocation denies the next checked request; already downloaded files and copied text cannot be recalled.</p></details></div>
    {loading && <p role="status">Checking the frozen report and its source permissions…</p>}
    {error ? <section className="review-error" role="alert"><h1>Report access unavailable</h1><p>{error instanceof Error ? error.message : "The report could not be checked."}</p>{token && <button className="button" onClick={() => { setLoading(true); void refresh(); }}>Check again</button>}</section> : null}
    {!loading && !error && !token && <section className="review-empty"><h1>A private review link is required</h1><p>Open a current link supplied by the report owner. If it has expired or been revoked, request a new one.</p></section>}
    {report && value && <>
      <section className="review-document" aria-labelledby="review-report-title">
        <div className="review-section-title"><div><span className="eyebrow">Research preview · {report.status}</span><h1 id="review-report-title">{report.title}</h1><p>Frozen {new Date(report.created_at).toLocaleString()} · access expires {new Date(value.grant.expires_at).toLocaleString()}</p></div>
          <button className="button secondary" disabled={busy} onClick={() => void download()}>Download checked HTML</button></div>
        <div className="review-evidence-notice"><strong>Inconclusive for a real-world planning decision</strong><p>Executed passive-tracer research preview, measured in ug/m³ at the end of the declared run. It does not establish a reduction in real exposure, regulatory AQI or site safety.</p></div>
        <h2>Planning question</h2><p>{report.planning_question || "Compare the retained baseline and declared alternative under matched study conditions."}</p>
        <h2>Neighbour check</h2><p>{worsened.length ? `Positive preview deltas at protected locations: ${worsened.map((item) => item.name).join(", ")}. Investigate these increases before drawing conclusions.` : "No positive delta at valid protected locations in this preview. Physical no-harm remains unestablished."}</p>
        <h2>Exact comparison</h2><p>Alternative minus baseline; a positive delta means a higher modelled concentration. Native cell samples are shown separately from requested height.</p>
        <div className="review-table-scroll" role="region" aria-label="Exact report comparison" tabIndex={0}><table><thead><tr><th>Assessment location</th><th>Requested / native height</th><th>Baseline ug/m³</th><th>Alternative ug/m³</th><th>Signed delta ug/m³</th><th>Change</th></tr></thead><tbody>{report.comparison.metrics.map((item) => <tr key={item.receptor_id}>
          <th scope="row">{item.name}{item.protected && <span className="review-protected">Protected</span>}</th><td>{number(item.requested_height_m)} m / {number(item.baseline_cell_centre?.[2])} → {number(item.alternative_cell_centre?.[2])} m</td>
          <td>{number(item.baseline_ug_m3)}</td><td>{number(item.alternative_ug_m3)}</td><td>{item.delta_ug_m3 != null && item.delta_ug_m3 > 0 ? "+" : ""}{number(item.delta_ug_m3)}</td><td>{item.percent_change == null ? "Suppressed" : `${number(item.percent_change)}%`}{item.valid === false && <span className="review-protected">Invalid sample</span>}</td>
        </tr>)}</tbody></table></div>
        <p className="review-footnote">Percentages below the 0.005 ug/m³ presentation denominator floor or for invalid samples are suppressed. This is not a health threshold.</p>
        <h2>Limits and unresolved evidence</h2><ul>{report.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>
        {report.source_notices?.length > 0 && <section className="review-source-notice"><h2>Source attribution</h2><p>© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>. Contains data adapted from OpenStreetMap under <a href="https://opendatacommons.org/licenses/odbl/1-0/" target="_blank" rel="noopener noreferrer">ODbL 1.0</a>. The checked HTML report includes the required source, derivative and alteration package.</p></section>}
        <details className="review-version"><summary>Frozen report and paired run identities</summary><dl><dt>Report</dt><dd>{report.id}</dd><dt>Baseline run</dt><dd>{report.comparison.baseline_run_id}</dd><dt>Alternative run</dt><dd>{report.comparison.alternative_run_id}</dd><dt>Report checksum</dt><dd>{report.checksum}</dd></dl></details>
      </section>
      <section className="review-document" aria-labelledby="review-comments-title"><div className="review-section-title"><h2 id="review-comments-title">Comments on this version</h2><button className="button secondary" disabled={busy} onClick={() => void refresh()}>Refresh comments</button></div>
        <p>Comments are appended to this report checksum. They do not change its scientific metrics or constitute approval.</p>
        {value.comments.length ? <ul className="review-comments">{value.comments.map((item) => <li key={item.id}><div><strong>{item.label}</strong><span>{new Date(item.created_at).toLocaleString()}</span></div>
          <p className="review-anchor-label">{item.anchor.kind === "report" ? "Whole report" : `Assessment location: ${report.comparison.metrics.find((metric) => metric.receptor_id === item.anchor.receptor_id)?.name ?? item.anchor.receptor_id}`}</p><p>{item.body}</p></li>)}</ul> : <p>No comments on this frozen report yet.</p>}
        <form className="review-comment-form" onSubmit={(event) => { event.preventDefault(); void comment(); }}><label>Comment location<select value={anchor} onChange={(event) => setAnchor(event.target.value)}><option value="">Whole report</option>{report.comparison.metrics.map((item) => <option key={item.receptor_id} value={item.receptor_id}>{item.name}</option>)}</select></label>
          <label>Comment<textarea value={body} onChange={(event) => setBody(event.target.value)} maxLength={2000} rows={5} required aria-describedby="review-comment-limit" /></label><span id="review-comment-limit">{body.length} / 2000 characters</span><button className="button" disabled={busy || !body.trim()}>{busy ? "Checking access…" : "Add comment"}</button></form>
        {notice && <p role="status">{notice}</p>}
      </section>
      <footer className="review-footnote">Comments stay tied to this frozen report version. They do not constitute scientific or planning approval.</footer>
    </>}
  </main>;
}
