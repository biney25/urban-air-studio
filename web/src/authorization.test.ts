import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SourcePermissionCard } from "./components/EvidenceWorkspace";
import type { AuthorizationMethod } from "./evidence";

const method: AuthorizationMethod = {
  id: "cedar-phase-b-permissions-v1",
  state: "active",
  available: true,
  engine: "cedar-policy",
  engine_version: "4.12.0",
  binding: "cedarpy",
  binding_version: "4.12.0",
  binding_maintainer: "k9securityio",
  binding_support:
    "Community binding; not officially supported by AWS or the Cedar Policy team",
  license: "Apache-2.0",
  aws_origin: "AWS-origin open-source Cedar policy engine",
  policy_id: "phase-b-same-project-source-permission",
  policy_sha256: "sha256:" + "1".repeat(64),
  schema_sha256: "sha256:" + "2".repeat(64),
  provenance_sha256: "sha256:" + "3".repeat(64),
  actions: ["derive", "solve", "export"],
  fail_closed: true,
  error_diagnostics_blocked: true,
  local_evaluation: true,
  cloud_service: false,
  scope:
    "Same-project declared source permissions including frozen dependencies",
  source_urls: {
    engine: "https://github.com/cedar-policy/cedar",
    binding: "https://github.com/k9securityio/cedar-py",
    aws_origin:
      "https://aws.amazon.com/about-aws/whats-new/2023/05/cedar-open-source-language-access-control/",
  },
};
const render = (value?: AuthorizationMethod) =>
  renderToStaticMarkup(createElement(SourcePermissionCard, { method: value }));
describe("server-reported source permission status", () => {
  it("shows active native Cedar identity and source/dependency scope without granting scientific or hosting status", () => {
    const html = render(method);
    expect(html).toContain("Cedar · active locally");
    expect(html).toContain("Cedar 4.12.0");
    expect(html).toContain("cedarpy 4.12.0");
    expect(html).toContain("community binding by k9securityio");
    expect(html).toContain(
      "Frozen source and campaign dependencies are checked too",
    );
    expect(html).toContain(
      "does not verify source ownership, establish model validity or demonstrate live AWS hosting",
    );
    expect(html).toContain(method.policy_sha256);
  });
  it("does not present an unavailable or inconsistent engine state as active", () => {
    for (const change of [
      { available: false },
      { state: "unavailable_fail_closed" as const },
    ]) {
      const html = render({ ...method, ...change });
      expect(html).not.toContain("Cedar · active locally");
      expect(html).toContain("Permission engine unavailable");
      expect(html).toContain(
        "An unavailable engine rejects these gated actions",
      );
    }
  });
  it("shows an absent readiness field as missing server status rather than inventing an active engine", () => {
    const html = render();
    expect(html).toContain("server has not supplied");
    expect(html).not.toContain("Cedar · active locally");
  });
});
