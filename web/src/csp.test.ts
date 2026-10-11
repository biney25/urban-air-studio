import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { rewriteCesiumGlobal } from "../build/cesiumCsp";
import { LOCAL_PREVIEW_CSP } from "../build/browserPolicy";
describe("Cesium strict browser policy compatibility", () => {
  it("admits only same-origin scripts and WASM while rejecting JavaScript eval, inline scripts and arbitrary external origins", () => {
    const script = LOCAL_PREVIEW_CSP.split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith("script-src "))!
      .split(/\s+/)
      .slice(1);
    expect(script).toEqual(["'self'", "'wasm-unsafe-eval'"]);
    expect(script).not.toContain("'unsafe-eval'");
    expect(script).not.toContain("'unsafe-inline'");
    const loader = readFileSync(
      new URL(
        "../node_modules/@cesium/engine/Source/Scene/GltfBufferViewLoader.js",
        import.meta.url,
      ),
      "utf8",
    );
    expect(loader).toContain('from "meshoptimizer"');
    const binary = readFileSync(
      new URL(
        "../node_modules/meshoptimizer/meshopt_encoder.js",
        import.meta.url,
      ),
      "utf8",
    );
    expect(binary).toContain("WebAssembly.instantiate");
  });
  it("replaces only the pinned legacy global discovery in the installed dependency", () => {
    const source = readFileSync(
      new URL(
        "../node_modules/@cesium/widgets/Source/ThirdParty/knockout-3.5.1.js",
        import.meta.url,
      ),
      "utf8",
    );
    const transformed = rewriteCesiumGlobal(source);
    expect(transformed).not.toMatch(
      /this\s*\|\|\s*\(0,\s*eval\)\(['"]this['"]\)/,
    );
    expect(transformed).toContain("globalThis");
    expect(transformed).toContain("Knockout JavaScript library v3.5.1");
  });
  it("fails the build if the dependency expression changes or appears twice", () => {
    expect(() => rewriteCesiumGlobal("other source")).toThrow();
    expect(() =>
      rewriteCesiumGlobal("this||(0,eval)('this');this||(0,eval)('this')"),
    ).toThrow();
  });
});
