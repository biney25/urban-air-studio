import type { Result } from "./types";
import { expireSession, requestHeaders, sessionGeneration } from "./auth";
import { controlledDownloadPath } from "./evidence";
export class ApiError extends Error {
  code: string;
  issues: unknown;
  status: number;
  constructor(message: string, code: string, status: number, issues?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.issues = issues;
  }
}
export function decodeResult(value: unknown): Result {
  const result = value as Result | undefined;
  const f = result?.field;
  const fail = (): never => {
    throw new ApiError(
      "The field schema, units, dimensions, time support or valid-cell mask did not pass checks. The field has not been rendered.",
      "FIELD_CONTRACT_INVALID",
      422,
    );
  };
  if (!f) return fail();
  if (
    f.schema_version !== "fields-v1" ||
    f.units !== "ug/m3" ||
    !Number.isInteger(f.nx) ||
    !Number.isInteger(f.ny) ||
    f.nx < 1 ||
    f.ny < 1 ||
    f.nx * f.ny > 1_000_000 ||
    !Array.isArray(f.extent) ||
    f.extent.length !== 4 ||
    !Array.from(f.extent).every(Number.isFinite) ||
    f.extent[0] >= f.extent[2] ||
    f.extent[1] >= f.extent[3] ||
    !Number.isFinite(f.height_m) ||
    f.height_m < 0 ||
    f.layout !== "row_major_y_then_x" ||
    f.support !== "native_cell_centres; piecewise_constant" ||
    f.time_support !== "end_state_at_duration_s" ||
    !Array.isArray(f.values) ||
    !Array.isArray(f.mask) ||
    f.values.length !== f.nx * f.ny ||
    f.mask.length !== f.values.length ||
    !Array.isArray(result?.metrics) ||
    result?.evidence_kind !== "simulated" ||
    result?.qualification !== "research_preview" ||
    !Array.isArray(result?.limitations) ||
    !Array.from(result.limitations).every((l) => typeof l === "string")
  )
    fail();
  // Reject sparse arrays as well as null/zero ambiguity before any GPU upload.
  for (let i = 0; i < f.values.length; i++) {
    const v = f.values[i],
      valid = f.mask[i];
    if (
      typeof valid !== "boolean" ||
      (valid
        ? typeof v !== "number" || !Number.isFinite(v) || v < 0
        : v !== null)
    )
      fail();
  }
  for (const m of result!.metrics) {
    if (
      !m ||
      typeof m.receptor_id !== "string" ||
      typeof m.name !== "string" ||
      typeof m.valid !== "boolean" ||
      (m.valid
        ? typeof m.value_ug_m3 !== "number" ||
          !Number.isFinite(m.value_ug_m3) ||
          m.value_ug_m3 < 0
        : m.value_ug_m3 !== null)
    )
      fail();
  }
  return result!;
}
export async function resultApi(
  runId: string,
  signal?: AbortSignal,
): Promise<Result> {
  return decodeResult(
    await api<unknown>(`/runs/${runId}/result`, undefined, signal),
  );
}
export async function api<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers = requestHeaders();
  const identity = sessionGeneration();
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const payload: unknown = await response.json().catch(() => null);
  if (identity !== sessionGeneration()) throw new ApiError("The signed-in identity changed while loading. Open the study again.", "SESSION_CHANGED", 401);
  if (response.status === 401) expireSession();
  if (!response.ok) {
    const p = payload as { detail?: unknown } | null;
    const d = p?.detail;
    if (d && typeof d === "object" && !Array.isArray(d)) {
      const e = d as { message?: string; code?: string; issues?: unknown };
      throw new ApiError(
        e.message ?? "The request could not be completed.",
        e.code ?? "REQUEST_FAILED",
        response.status,
        e.issues,
      );
    }
    throw new ApiError(
      typeof d === "string"
        ? d
        : "Input validation failed. Check the values and try again.",
      "REQUEST_FAILED",
      response.status,
      d,
    );
  }
  return payload as T;
}
export async function downloadProtected(path: string, name: string): Promise<void> {
  if (!controlledDownloadPath(path) || !/^[A-Za-z0-9_.-]+$/.test(name)) throw new Error("Unsupported download destination or filename.");
  const identity = sessionGeneration();
  const response = await fetch(`/api${path}`, { headers: requestHeaders(), cache: "no-store" });
  if (response.status === 401) expireSession();
  if (!response.ok || response.status !== 200) throw new ApiError("The artifact could not be downloaded. Check your access and source permissions, then try again.", "DOWNLOAD_FAILED", response.status);
  const expectedType = /\/(?:detailed-cases|detailed-executions)\//.test(path) ? "application/zip" : path.endsWith("format=html") ? "text/html" : path.endsWith("format=pdf") ? "application/pdf" : "application/json";
  if (response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== expectedType) throw new ApiError("The artifact response has an unexpected file type. No file was downloaded.", "DOWNLOAD_CONTENT_INVALID", 422);
  const blob = await response.blob();
  if (identity !== sessionGeneration()) throw new ApiError("The signed-in identity changed before the artifact arrived.", "SESSION_CHANGED", 401);
  if (!blob.size) throw new ApiError("The artifact response is empty. No file was downloaded; try again after checking the connection.", "DOWNLOAD_CONTENT_INVALID", 422);
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
export function downloadJson(name: string, data: unknown) {
  const href = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
