import type { Comparison } from "./types";

export const REVIEW_SESSION_KEY = "urban-air-report-review-v1";
export const REVIEW_TOKEN_PATTERN = /^uarv1_[A-Za-z0-9_-]{64}$/;
const MAX_SESSION_MS = 7 * 24 * 60 * 60 * 1000;

export interface ReviewGrant {
  id: string;
  label: string;
  report_id: string;
  report_checksum: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  state: "active" | "expired" | "revoked";
  comment_count: number;
}
export interface ReviewComment {
  id: string;
  grant_id: string;
  label: string;
  report_id: string;
  report_checksum: string;
  anchor: { kind: "report" | "receptor"; receptor_id?: string };
  body: string;
  created_at: string;
}
export interface ReviewPackage {
  grant: ReviewGrant;
  report: {
    id: string;
    title: string;
    status: string;
    created_at: string;
    checksum: string;
    comparison: Comparison;
    limitations: string[];
    planning_question: string | null;
    source_notices: { notice: string; licence_url: string; attribution_url: string }[];
  };
  permissions: { read_report: true; comment: true; download_html: true };
  comments: ReviewComment[];
}
export interface ReviewCommentInput {
  report_checksum: string;
  anchor: ReviewComment["anchor"];
  body: string;
}

export class ReviewError extends Error {
  constructor(message: string, public code: string, public status: number) {
    super(message);
    this.name = "ReviewError";
  }
}

export function reviewLink(origin: string, token: string): string {
  if (!REVIEW_TOKEN_PATTERN.test(token)) throw new Error("Invalid report review credential.");
  const url = new URL(origin);
  if (url.username || url.password || !["http:", "https:"].includes(url.protocol)) throw new Error("Unsupported report review origin.");
  return `${url.origin}/review#token=${encodeURIComponent(token)}`;
}

function availableStorage(): Storage | null {
  try { return typeof sessionStorage === "undefined" ? null : sessionStorage; } catch { return null; }
}

export function clearReviewSession(storage: Pick<Storage, "removeItem"> | null = availableStorage()): void {
  try { storage?.removeItem(REVIEW_SESSION_KEY); } catch { /* A blocked storage surface retains no credential. */ }
}

export function rememberReviewSession(token: string, expiresAt: string, storage: Pick<Storage, "setItem" | "removeItem"> | null = availableStorage(), now = Date.now()): void {
  const expiry = Date.parse(expiresAt);
  if (!REVIEW_TOKEN_PATTERN.test(token) || !Number.isFinite(expiry) || expiry <= now) {
    clearReviewSession(storage);
    return;
  }
  try { storage?.setItem(REVIEW_SESSION_KEY, JSON.stringify({ token, expires: Math.min(expiry, now + MAX_SESSION_MS) })); } catch { /* In-memory review still works in this tab. */ }
}

/** Consume a fragment before any fetch. Bad new links also clear an older session. */
export function takeReviewCredential(
  location: Pick<Location, "hash" | "pathname" | "search"> = window.location,
  history: Pick<History, "replaceState"> = window.history,
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null = availableStorage(),
  now = Date.now(),
): string | null {
  if (location.hash) {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const token = fragment.get("token");
    history.replaceState(null, "", location.pathname + location.search);
    clearReviewSession(storage);
    if (fragment.size !== 1 || !token || !REVIEW_TOKEN_PATTERN.test(token)) return null;
    // Until the server checks the grant, keep a short reload window only.
    rememberReviewSession(token, new Date(now + 5 * 60 * 1000).toISOString(), storage, now);
    return token;
  }
  try {
    const saved = JSON.parse(storage?.getItem(REVIEW_SESSION_KEY) || "null") as { token?: unknown; expires?: unknown } | null;
    if (saved && typeof saved.token === "string" && REVIEW_TOKEN_PATTERN.test(saved.token)
        && typeof saved.expires === "number" && Number.isFinite(saved.expires)
        && saved.expires > now && saved.expires <= now + MAX_SESSION_MS) return saved.token;
  } catch { /* Corrupt or disabled session storage is closed. */ }
  clearReviewSession(storage);
  return null;
}

async function checkResponse(response: Response): Promise<void> {
  if (response.ok && (response.status === 200 || response.status === 201)) return;
  const payload = await response.json().catch(() => null) as { detail?: { code?: string; message?: string } } | null;
  if (response.status === 401 || response.status === 403) clearReviewSession();
  throw new ReviewError(payload?.detail?.message || "The report could not be checked. Open a current review link or try again.",
    payload?.detail?.code || "REVIEW_UNAVAILABLE", response.status);
}

function credentialHeaders(token: string): Headers {
  if (!REVIEW_TOKEN_PATTERN.test(token)) throw new ReviewError("A valid report review link is required.", "REVIEW_TOKEN_REQUIRED", 401);
  return new Headers({ Authorization: `Bearer ${token}` });
}

/** Fixed review destinations only; never calls the planner identity/session API. */
export async function reviewRequest<T>(token: string, resource: "report" | "comments", body?: ReviewCommentInput, signal?: AbortSignal): Promise<T> {
  if ((resource !== "report" && resource !== "comments") || (body !== undefined && resource !== "comments")) throw new Error("Unsupported report review action.");
  const headers = credentialHeaders(token);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(resource === "report" ? "/api/review/report" : "/api/review/report/comments", {
    method: body === undefined ? "GET" : "POST", headers, credentials: "omit", cache: "no-store", redirect: "error",
    body: body === undefined ? undefined : JSON.stringify(body), signal,
  });
  await checkResponse(response);
  if (response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") throw new ReviewError("The review response has an unexpected format.", "REVIEW_RESPONSE_INVALID", 422);
  return response.json() as Promise<T>;
}

export async function downloadReview(token: string): Promise<void> {
  const response = await fetch("/api/review/report/download", {
    headers: credentialHeaders(token), credentials: "omit", cache: "no-store", redirect: "error",
  });
  await checkResponse(response);
  if (response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "text/html") throw new ReviewError("The checked document has an unexpected format.", "REVIEW_RESPONSE_INVALID", 422);
  const blob = await response.blob();
  if (!blob.size) throw new ReviewError("The checked document is empty.", "REVIEW_RESPONSE_INVALID", 422);
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = "urban-air-reviewed-report.html";
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
