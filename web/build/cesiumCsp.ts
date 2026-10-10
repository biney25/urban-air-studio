/** Legacy Knockout discovers the global through eval in strict ESM. Use its standard equivalent. */
export function rewriteCesiumGlobal(code: string): string {
  const globalLookup = /this\s*\|\|\s*\(0,\s*eval\)\(['"]this['"]\)/g;
  const matches = code.match(globalLookup);
  if (matches?.length !== 1) {
    throw new Error("Cesium's pinned legacy global lookup changed. Review CSP compatibility before updating this transform.");
  }
  return code.replace(globalLookup, "globalThis");
}
