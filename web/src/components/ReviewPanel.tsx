import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { reviewLink, type ReviewComment, type ReviewGrant } from "../review";
import type { Report } from "../types";
import { ErrorBox, Loading } from "./UI";
import "../reviewPanels.css";

export default function ReviewPanel({ report }: { report: Report }) {
  const root = `/projects/${encodeURIComponent(report.project_id)}/reports/${encodeURIComponent(report.id)}`;
  const [label, setLabel] = useState("Report reviewer");
  const [ttl, setTtl] = useState(86400);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);
  const grants = useQuery({ queryKey: ["report-review-grants", report.id],
    queryFn: ({ signal }) => api<ReviewGrant[]>(`${root}/review-grants`, undefined, signal), retry: false });
  const comments = useQuery({ queryKey: ["report-review-comments", report.id],
    queryFn: ({ signal }) => api<ReviewComment[]>(`${root}/review-comments`, undefined, signal), retry: false });
  useEffect(() => { setUrl(""); setError(null); setCopied(false); }, [report.id]);

  async function create() {
    setBusy(true); setError(null); setUrl(""); setCopied(false);
    try {
      const value = await api<ReviewGrant & { token: string }>(`${root}/review-grants`, { label: label.trim(), ttl_seconds: ttl });
      setUrl(reviewLink(window.location.origin, value.token));
      await grants.refetch();
    } catch (cause) { setError(cause); }
    finally { setBusy(false); }
  }
  async function revoke(id: string) {
    setBusy(true); setError(null); setUrl("");
    try { await api(`${root}/review-grants/${encodeURIComponent(id)}/revoke`, {}); await grants.refetch(); }
    catch (cause) { setError(cause); }
    finally { setBusy(false); }
  }
  return <section className="report-review-panel" aria-labelledby="report-review-title">
    <div className="review-section-title"><div><span className="eyebrow">Frozen report access</span><h2 id="report-review-title">Invite a report reviewer</h2></div>
      <button className="button secondary" onClick={() => { void grants.refetch(); void comments.refetch(); }} disabled={busy}>Refresh reviews</button></div>
    <p>The link opens this exact report for reading, checked HTML download and comments. Creating a link does not send it to anyone.</p>
    <div className="review-scope-notice"><strong>Local review · private report link</strong><p>Read, comment or download this report until the link expires.</p><details><summary>About this review link</summary><p>The server must be reachable on this computer. Anyone holding the link can use its report access. Reviewer labels do not verify a person's identity; local machine access retains the development owner's trust boundary. Revocation denies the next checked request. Active review pages recheck every 15 seconds while connected. Already downloaded files and copied text cannot be recalled.</p></details></div>
    <form className="review-grant-form" onSubmit={(event) => { event.preventDefault(); void create(); }}>
      <label>Reviewer label<input value={label} maxLength={120} required onChange={(event) => setLabel(event.target.value)} /></label>
      <label>Link lifetime<select value={ttl} onChange={(event) => setTtl(Number(event.target.value))}><option value={3600}>1 hour</option><option value={86400}>1 day</option><option value={604800}>7 days</option></select></label>
      <button className="button" disabled={busy || !label.trim()}>{busy ? "Checking…" : "Create review link"}</button>
    </form>
    {url && <div className="review-link-once"><strong>Save this link now</strong><p>The credential is shown once and cannot be recovered after leaving this view. Keep the full link private.</p>
      <label>Private review link<input aria-label="Private review link" value={url} readOnly onFocus={(event) => event.target.select()} /></label>
      <div className="review-actions"><button className="button secondary" onClick={async () => { try { await navigator.clipboard.writeText(url); setCopied(true); } catch { setError(new Error("Clipboard unavailable. Select and copy the private link above.")); } }}>{copied ? "Copied" : "Copy private link"}</button>
        <button className="text-button" onClick={() => setUrl("")}>Hide link</button></div></div>}
    {error ? <ErrorBox error={error} /> : null}
    {grants.isPending ? <Loading label="Checking review grants…" /> : grants.error ? <ErrorBox error={grants.error} onRetry={() => void grants.refetch()} /> : <>
      <h3>Review links</h3>{!grants.data?.length ? <p>No report review links have been created.</p> : <ul className="review-grant-list">{grants.data.map((item) => <li key={item.id}>
        <div><strong>{item.label}</strong><p>{item.state === "revoked" ? `Revoked ${new Date(item.revoked_at!).toLocaleString()}` : `Expires ${new Date(item.expires_at).toLocaleString()}`} · {item.comment_count} {item.comment_count === 1 ? "comment" : "comments"}</p></div>
        <span className={`badge ${item.state === "active" ? "cyan" : "quiet"}`}>{item.state}</span>
        {item.state !== "revoked" && <button className="button secondary" disabled={busy} onClick={() => void revoke(item.id)}>Revoke link</button>}
      </li>)}</ul>}</>}
    <h3>Report comments</h3>{comments.isPending ? <Loading label="Loading report comments…" /> : comments.error ? <ErrorBox error={comments.error} onRetry={() => void comments.refetch()} /> : !comments.data?.length ? <p>No comments yet.</p> : <ul className="review-comments">{comments.data.map((item) => <li key={item.id}>
      <div><strong>{item.label}</strong><span>{new Date(item.created_at).toLocaleString()}</span></div><p className="review-anchor-label">{item.anchor.kind === "report" ? "Whole report" : `Assessment location: ${report.comparison.metrics.find((metric) => metric.receptor_id === item.anchor.receptor_id)?.name ?? item.anchor.receptor_id}`}</p><p>{item.body}</p>
    </li>)}</ul>}
  </section>;
}
