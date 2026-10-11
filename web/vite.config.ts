import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { rewriteCesiumGlobal } from "./build/cesiumCsp";
import { LOCAL_PREVIEW_CSP } from "./build/browserPolicy";
export default defineConfig({
  plugins: [react(), {
    name: "cesium-csp-global",
    enforce: "pre",
    transform(code, id) {
      if (!id.replaceAll("\\", "/").split("?")[0].endsWith("/@cesium/widgets/Source/ThirdParty/knockout-3.5.1.js")) return null;
      return { code: rewriteCesiumGlobal(code), map: null };
    },
  }],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.UAS_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  preview: {
    port: 5180,
    strictPort: true,
    headers: {
      "Content-Security-Policy": LOCAL_PREVIEW_CSP,
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "strict-origin-when-cross-origin",
    },
  },
  build: { target: "es2022", chunkSizeWarningLimit: 1800 },
});
