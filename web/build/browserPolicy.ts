/** Versioned local production-preview policy; providers are contacted by the API. */
export const EXPLORE_POLICY_VERSION = "explore-browser-v1";
export const EXPLORE_TILE_ORIGIN = "https://tiles.openfreemap.org";
export const LOCAL_PREVIEW_CSP = [
  "default-src 'self'",
  // Cesium imports meshoptimizer's embedded WASM modules. Permit that binary
  // compilation while JavaScript eval/Function and inline scripts stay blocked.
  "script-src 'self' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${EXPLORE_TILE_ORIGIN}`,
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join("; ");
