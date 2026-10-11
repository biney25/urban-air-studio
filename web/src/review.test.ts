import { afterEach, describe, expect, it, vi } from "vitest";
import { REVIEW_SESSION_KEY, REVIEW_TOKEN_PATTERN, clearReviewSession, downloadReview, rememberReviewSession,
  reviewLink, reviewRequest, takeReviewCredential } from "./review";

// Explicit test credential; never a live minted report token.
const token = `uarv1_${"A".repeat(64)}`;
const now = Date.parse("2026-10-11T00:00:00Z");
function storage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); } };
}
const response = (status = 200, body: unknown = { report: "fixture" }) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json" },
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("private fragment review credentials", () => {
  it("creates only a same-origin fragment link with no network query credential", () => {
    const link = new URL(reviewLink("http://127.0.0.1:5173", token));
    expect(link.pathname).toBe("/review"); expect(link.search).toBe("");
    expect(link.hash).toBe(`#token=${token}`);
    for (const value of ["owner-jwt", token + "X", "", `uarv1_${"/".repeat(64)}`]) {
      expect(REVIEW_TOKEN_PATTERN.test(value)).toBe(false);
      expect(() => reviewLink("http://127.0.0.1", value)).toThrow();
    }
    expect(() => reviewLink("javascript:alert(1)", token)).toThrow();
    expect(() => reviewLink("https://user:secret@example.invalid", token)).toThrow();
  });

  it("consumes and strips the fragment before keeping a bounded same-tab reload credential", () => {
    const saved = storage(), history = { replaceState: vi.fn() };
    expect(takeReviewCredential({ hash: `#token=${token}`, pathname: "/review", search: "" }, history, saved, now)).toBe(token);
    expect(history.replaceState).toHaveBeenCalledWith(null, "", "/review");
    expect(takeReviewCredential({ hash: "", pathname: "/review", search: "" }, history, saved, now + 1)).toBe(token);
    expect(takeReviewCredential({ hash: "", pathname: "/review", search: "" }, history, saved, now + 5 * 60 * 1000)).toBeNull();
    expect(saved.getItem(REVIEW_SESSION_KEY)).toBeNull();
  });

  it("bad new links clear a previous valid session instead of opening its other report", () => {
    for (const hash of ["#token=invalid", `#token=${token}&report=foreign`, `#token=${token}&token=${token}`, "#report=foreign"]) {
      const saved = storage(), history = { replaceState: vi.fn() };
      rememberReviewSession(token, new Date(now + 3600000).toISOString(), saved, now);
      expect(takeReviewCredential({ hash, pathname: "/review", search: "" }, history, saved, now)).toBeNull();
      expect(saved.getItem(REVIEW_SESSION_KEY)).toBeNull();
      expect(history.replaceState).toHaveBeenCalledWith(null, "", "/review");
    }
  });

  it("rejects corrupted, expired and arbitrarily extended persisted sessions", () => {
    const saved = storage(), location = { hash: "", pathname: "/review", search: "" }, history = { replaceState: vi.fn() };
    for (const value of ["broken-json", JSON.stringify({ token: "owner-jwt", expires: now + 1 }),
      JSON.stringify({ token, expires: now }), JSON.stringify({ token, expires: now + 8 * 86400000 }),
      JSON.stringify({ token, expires: "tomorrow" })]) {
      saved.setItem(REVIEW_SESSION_KEY, value);
      expect(takeReviewCredential(location, history, saved, now)).toBeNull();
    }
    rememberReviewSession(token, new Date(now + 20 * 86400000).toISOString(), saved, now);
    expect(JSON.parse(saved.getItem(REVIEW_SESSION_KEY)!).expires).toBe(now + 7 * 86400000);
    rememberReviewSession(token, "invalid date", saved, now);
    expect(saved.getItem(REVIEW_SESSION_KEY)).toBeNull();
  });

  it("still consumes a valid link when session storage is disabled", () => {
    const locked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
    const history = { replaceState: vi.fn() };
    expect(takeReviewCredential({ hash: `#token=${token}`, pathname: "/review", search: "" }, history, locked, now)).toBe(token);
    expect(() => clearReviewSession(locked)).not.toThrow();
    expect(takeReviewCredential({ hash: "", pathname: "/review", search: "" }, history, locked, now)).toBeNull();
  });
});

describe("review-only network boundary", () => {
  it("sends the credential only in Authorization to a fixed report endpoint, with no cookies or redirects", async () => {
    const fetch = vi.fn().mockResolvedValue(response()); vi.stubGlobal("fetch", fetch);
    const controller = new AbortController();
    expect(await reviewRequest(token, "report", undefined, controller.signal)).toEqual({ report: "fixture" });
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("/api/review/report"); expect(url).not.toContain(token);
    expect(options.headers.get("Authorization")).toBe(`Bearer ${token}`);
    expect(options.credentials).toBe("omit"); expect(options.cache).toBe("no-store"); expect(options.redirect).toBe("error");
    expect(options.method).toBe("GET"); expect(options.signal).toBe(controller.signal);
  });

  it("posts only version-bound anchored comments to the fixed comment endpoint", async () => {
    const fetch = vi.fn().mockResolvedValue(response(201)); vi.stubGlobal("fetch", fetch);
    const body = { report_checksum: `sha256:${"b".repeat(64)}`, anchor: { kind: "receptor" as const, receptor_id: "receptor_fixture" }, body: "Bound to this exact report" };
    await reviewRequest(token, "comments", body);
    expect(fetch.mock.calls[0][0]).toBe("/api/review/report/comments");
    expect(fetch.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(body);
    expect(fetch.mock.calls[0][1].headers.get("Content-Type")).toBe("application/json");
  });

  it("refuses unknown or owner API destinations and credentials before making any request", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    for (const destination of ["/api/projects", "download", "https://attacker.example", "report?token=secret"]) {
      await expect(reviewRequest(token, destination as "report")).rejects.toThrow();
    }
    await expect(reviewRequest("owner-jwt", "report")).rejects.toThrow();
    await expect(reviewRequest(token, "report", { report_checksum: "fixture", anchor: { kind: "report" }, body: "hidden edit" })).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([401, 403])("clears the private session when the server denies access (%i)", async (status) => {
    const saved = storage(); vi.stubGlobal("sessionStorage", saved);
    saved.setItem(REVIEW_SESSION_KEY, "private cached credential");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(status, { detail: { code: "REVIEW_REVOKED", message: "Access revoked" } })));
    await expect(reviewRequest(token, "report")).rejects.toMatchObject({ code: "REVIEW_REVOKED", status });
    expect(saved.getItem(REVIEW_SESSION_KEY)).toBeNull();
  });

  it("blocks unexpected content rather than rendering an HTML/error response as report data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<script>bad response</script>", { headers: { "content-type": "text/html" } })));
    await expect(reviewRequest(token, "report")).rejects.toMatchObject({ code: "REVIEW_RESPONSE_INVALID" });
  });

  it("downloads only checked HTML using a temporary blob, never a credential-bearing link", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockResolvedValue(new Response("<!doctype html><title>Explicit fixture</title>", { headers: { "content-type": "text/html; charset=utf-8" } }));
    vi.stubGlobal("fetch", fetch);
    const link = { href: "", download: "", click: vi.fn() };
    vi.stubGlobal("document", { createElement: vi.fn().mockReturnValue(link) });
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:explicit-fixture");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    await downloadReview(token);
    expect(fetch.mock.calls[0][0]).toBe("/api/review/report/download");
    expect(fetch.mock.calls[0][1].credentials).toBe("omit");
    expect(create).toHaveBeenCalled(); expect(link.href).toBe("blob:explicit-fixture"); expect(link.download).toBe("urban-air-reviewed-report.html"); expect(link.click).toHaveBeenCalled();
    vi.advanceTimersByTime(1000); expect(revoke).toHaveBeenCalledWith("blob:explicit-fixture");
  });

  it("does not download JSON or an empty successful HTML document", async () => {
    const create = vi.spyOn(URL, "createObjectURL");
    for (const reply of [response(), new Response("", { headers: { "content-type": "text/html" } })]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply));
      await expect(downloadReview(token)).rejects.toMatchObject({ code: "REVIEW_RESPONSE_INVALID" });
    }
    expect(create).not.toHaveBeenCalled();
  });
});
