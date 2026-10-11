import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, useLocation } from "react-router-dom";
import App from "./App";
import AuthGate from "./components/AuthGate";
import "./styles.css";
window.CESIUM_BASE_URL = "/cesium/";
const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 5000, refetchOnWindowFocus: false },
  },
});
function AccessBoundary() {
  // Report capabilities use their dedicated scoped endpoints and do not need
  // (or inherit) the planner login. All other workspaces retain the auth gate.
  const { pathname } = useLocation();
  return pathname === "/review" ? <App /> : <AuthGate><App /></AuthGate>;
}
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AccessBoundary />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
