export const gatedViews = [
  "assistant",
  "optimisations",
  "review",
  "settings",
];
const availableViews = [
  "explore",
  "data",
  "imports",
  "baseline",
  "design",
  "sources",
  "receptors",
  "study",
  "runs",
  "result",
  "compare",
  "reports",
  "exports",
  "evidence",
  "investigate",
];
export function projectView(path: string) {
  const sub = path.split("/").slice(3);
  const raw = sub[0] || "explore";
  const view =
    raw === "scenarios"
      ? "design"
      : raw === "runs" && sub[1]
        ? "result"
        : raw === "experiments"
          ? "study"
          : raw === "validation"
            ? "evidence"
            : raw;
  return {
    raw,
    sub,
    view:
      availableViews.includes(view) || gatedViews.includes(view)
        ? view
        : "unknown",
  };
}
