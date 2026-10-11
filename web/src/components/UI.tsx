import {
  AlertCircle,
  ArrowRight,
  Check,
  ChevronRight,
  LoaderCircle,
} from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "../api";
export function Badge({
  children,
  tone = "quiet",
}: {
  children: ReactNode;
  tone?: "quiet" | "cyan" | "amber" | "violet" | "red";
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Loading({
  label = "Loading project evidence…",
}: {
  label?: string;
}) {
  return (
    <div className="loading-state" role="status">
      <LoaderCircle size={20} className="spin" />
      {label}
    </div>
  );
}
export function ErrorBox({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const e = error as Error;
  return (
    <div className="error-box" role="alert">
      <AlertCircle size={18} />
      <div>
        <strong>
          {error instanceof ApiError
            ? error.code.replaceAll("_", " ")
            : "Request unavailable"}
        </strong>
        <p>
          {e.message ??
            "The request did not complete. Your saved versions are retained."}
        </p>
        {error instanceof ApiError && error.issues ? (
          <details>
            <summary>Affected inputs</summary>
            <pre>{JSON.stringify(error.issues, null, 2)}</pre>
          </details>
        ) : null}
        {onRetry ? (
          <button className="text-button" onClick={onRetry}>
            Try again <ArrowRight size={14} />
          </button>
        ) : null}
      </div>
    </div>
  );
}
export function SectionHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {action}
    </div>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-orbit">
        <ChevronRight size={28} />
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function EvidenceNote({
  synthetic = false,
  illustrative = true,
}: {
  synthetic?: boolean;
  illustrative?: boolean;
}) {
  return (
    <div className="evidence-note">
      <span className="status-dot" />
      <div>
        <strong>
          {synthetic
            ? "Fictional sample geometry"
            : `Imported input geometry${illustrative ? " · illustrative assumptions" : ""}`}
        </strong>
        <span>
          Research inputs · no measured air-quality observations or site
          validation.
        </span>
        <details>
          <summary>Model suitability</summary>
          <p>
            T1 research preview. Default mesh is not convergence-qualified;
            small differences can be numerical or native-cell sampling effects.
            Use this to investigate, not approve a design.
          </p>
        </details>
      </div>
    </div>
  );
}
export function JsonViewer({
  data,
  title = "Manifest",
}: {
  data: unknown;
  title?: string;
}) {
  return (
    <details className="json-viewer">
      <summary>
        {title}
        <ChevronRight size={14} />
      </summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}
export function CheckRow({
  label,
  detail,
  good = true,
}: {
  label: string;
  detail: string;
  good?: boolean;
}) {
  return (
    <div className="check-row">
      <span className={good ? "check-icon" : "warn-icon"}>
        {good ? <Check size={15} /> : <AlertCircle size={15} />}
      </span>
      <div>
        <strong>{label}</strong>
        <span>{detail}</span>
      </div>
    </div>
  );
}
export function NumericInput({
  label,
  value,
  onChange,
  min,
  max,
  step = "any",
  unit,
  id,
  disabled = false,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number | "any";
  unit?: string;
  id?: string;
  disabled?: boolean;
}) {
  return (
    <label className="form-label">
      {label}
      <span className="unit-input">
        <input
          data-testid={id}
          type="number"
          required
          value={Number.isFinite(value) ? value : ""}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onChange={(e) =>
            onChange(e.target.value === "" ? NaN : Number(e.target.value))
          }
        />
        {unit ? <span>{unit}</span> : null}
      </span>
    </label>
  );
}
