import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { getSession, initializeAuth, signIn, signOut, subscribeSession } from "../auth";
import { Badge, ErrorBox, Loading } from "./UI";

export function AuthStatus() {
  const session = useSyncExternalStore(subscribeSession, getSession);
  return session.mode === "local" ? <Badge>Local development identity</Badge> :
    <span className="auth-status"><span>{session.label}</span><button className="text-button" onClick={() => void signOut()}>Sign out</button></span>;
}
export default function AuthGate({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const session = useSyncExternalStore(subscribeSession, getSession);
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(true);
  const [retry, setRetry] = useState(0);
  const location = useLocation();
  useEffect(() => {
    let live = true;
    setPending(true);
    setError(undefined);
    void fetch("/api/auth/config", { cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error("Access configuration is unavailable. The workspace stays locked until the server confirms access.");
      await initializeAuth(await response.json());
    }).catch(e => { if (live) setError(e); }).finally(() => { if (live) setPending(false); });
    return () => { live = false; };
  }, [retry]);
  useEffect(() => {
    // A new identity must never inherit another member's project data or ongoing query.
    void client.cancelQueries();
    client.clear();
  }, [client, session]);
  useEffect(() => {
    const id = window.setTimeout(() => {
      const main = document.getElementById("main-content");
      if (main && !main.contains(document.activeElement)) {
        main.setAttribute("tabindex", "-1");
        main.focus({ preventScroll: true });
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, [location.pathname]);
  if (pending) return <main id="main-content" className="content-page"><Loading label="Checking workspace access…" /></main>;
  if (error || !session.authenticated) return (
    <main id="main-content" className="auth-page">
      <span className="eyebrow">URBAN AIR STUDIO</span>
      <h1>{error ? "Workspace access unavailable" : "Sign in to your studio"}</h1>
      <p>Your saved studies belong to your workspace. Sign in through the configured organisation account to continue.</p>
      {error ? <ErrorBox error={error} onRetry={() => setRetry(v => v + 1)} /> : null}
      {!pending && session.mode === "cognito" ? <button className="button primary" onClick={() => void signIn().catch(setError)}>Sign in securely</button> : null}
    </main>
  );
  return <>{children}</>;
}
