import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { rewriteCesiumGlobal } from "../build/cesiumCsp";
describe("Cesium strict browser policy compatibility", () => {
  it("replaces only the pinned legacy global discovery in the installed dependency", () => {
    const source = readFileSync(new URL("../node_modules/@cesium/widgets/Source/ThirdParty/knockout-3.5.1.js", import.meta.url), "utf8");
    const transformed = rewriteCesiumGlobal(source);
    expect(transformed).not.toMatch(/this\s*\|\|\s*\(0,\s*eval\)\(['"]this['"]\)/);
    expect(transformed).toContain("globalThis");
    expect(transformed).toContain("Knockout JavaScript library v3.5.1");
  });
  it("fails the build if the dependency expression changes or appears twice", () => {
    expect(() => rewriteCesiumGlobal("other source")).toThrow();
    expect(() => rewriteCesiumGlobal("this||(0,eval)('this');this||(0,eval)('this')")).toThrow();
  });
});
