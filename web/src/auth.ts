import type { User, UserManager } from "oidc-client-ts";

export interface AuthConfig {
  mode: "local" | "cognito";
  configured: boolean;
  issuer?: string;
  client_id?: string;
  authorization_endpoint?: string;
  token_endpoint?: string;
  logout_endpoint?: string;
  redirect_uri?: string;
  logout_uri?: string;
  scopes?: string[];
}
export interface Session {
  mode: "loading" | "local" | "cognito";
  authenticated: boolean;
  label: string;
}
let config: AuthConfig | undefined;
let manager: UserManager | undefined;
let user: User | null = null;
let generation = 0;
let startup: Promise<void> | undefined;
const listeners = new Set<() => void>();
let session: Session = { mode: "loading", authenticated: false, label: "Checking access" };
function publish(next: Session) {
  generation++;
  session = next;
  for (const notify of listeners) notify();
}
export const subscribeSession = (notify: () => void) => {
  listeners.add(notify);
  return () => { listeners.delete(notify); };
};
export const getSession = () => session;
export const sessionGeneration = () => generation;

export function safeReturnPath(value: unknown): string {
  return typeof value === "string" && value.startsWith("/") &&
    !value.startsWith("//") && !value.includes("\\") &&
    !value.startsWith("/auth/") ? value : "/projects";
}
export function validateAuthConfig(value: unknown, origin: string): AuthConfig {
  const c = value as AuthConfig | undefined;
  if (!c || !["local", "cognito"].includes(c.mode) || typeof c.configured !== "boolean")
    throw new Error("The server has not supplied a valid access configuration.");
  if (!c.configured) throw new Error("Access is not configured for this deployment. Contact its administrator.");
  if (c.mode === "local") return c;
  if (!c.client_id || !Array.isArray(c.scopes) || !c.scopes.includes("openid"))
    throw new Error("The sign-in configuration is incomplete.");
  for (const item of [c.issuer, c.authorization_endpoint, c.token_endpoint, c.logout_endpoint]) {
    const url = new URL(item ?? "");
    if (url.protocol !== "https:" || url.username || url.password || url.hash)
      throw new Error("Sign-in endpoints must be secure URLs.");
  }
  const redirect = new URL(c.redirect_uri ?? "");
  if (redirect.origin !== origin || redirect.pathname !== "/auth/callback" || redirect.search || redirect.hash)
    throw new Error("The configured sign-in callback must match this application exactly.");
  if (c.logout_uri) {
    const logout = new URL(c.logout_uri);
    if (logout.origin !== origin || logout.search || logout.hash)
      throw new Error("The configured sign-out destination must match this application.");
  }
  return c;
}
export function validateIdentityClaims(profile: Record<string, unknown>, c: AuthConfig, now = Date.now() / 1000) {
  const audiences = Array.isArray(profile.aud) ? profile.aud : [profile.aud];
  if (profile.iss !== c.issuer || !audiences.includes(c.client_id) ||
    typeof profile.sub !== "string" || !profile.sub || profile.token_use !== "id" ||
    typeof profile.exp !== "number" || profile.exp <= now ||
    typeof profile.iat !== "number" || profile.iat > now + 60)
    throw new Error("The identity response did not match this application's issuer, client or validity window.");
}
function clearIdentity() {
  user = null;
  void manager?.removeUser();
  publish({ mode: "cognito", authenticated: false, label: "Sign in required" });
}
export function expireSession() {
  if (config?.mode === "cognito") clearIdentity();
}
export function requestHeaders(): Headers {
  const headers = new Headers();
  if (!config) throw new Error("Access configuration has not loaded. Try again after it loads.");
  if (config.mode === "cognito") {
    if (!user || user.expired || !user.access_token) {
      clearIdentity();
      throw new Error("Your session has ended. Sign in to continue; saved project versions are retained.");
    }
    headers.set("Authorization", `Bearer ${user.access_token}`);
  }
  return headers;
}
export async function initializeAuth(value: unknown): Promise<void> {
  config = validateAuthConfig(value, window.location.origin);
  if (startup) return startup;
  startup = (async () => {
    if (config!.mode === "local") {
      publish({ mode: "local", authenticated: true, label: "Local development identity" });
      return;
    }
    const { UserManager, WebStorageStateStore, InMemoryWebStorage } = await import("oidc-client-ts");
    const c = config!;
    manager = new UserManager({
      authority: c.issuer!, client_id: c.client_id!, redirect_uri: c.redirect_uri!,
      response_type: "code", scope: c.scopes!.join(" "),
      automaticSilentRenew: false, loadUserInfo: false,
      revokeTokenTypes: ["refresh_token"],
      // Only one-time PKCE/nonce/state survives the redirect. Tokens never enter browser storage.
      stateStore: new WebStorageStateStore({ store: window.sessionStorage, prefix: "ua.oidc.state." }),
      userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
      staleStateAgeInSeconds: 600,
      metadata: { issuer: c.issuer!, authorization_endpoint: c.authorization_endpoint!,
        token_endpoint: c.token_endpoint!, end_session_endpoint: c.logout_endpoint! },
    });
    manager.events.addAccessTokenExpired(clearIdentity);
    await manager.clearStaleState();
    if (window.location.pathname === "/auth/callback") {
      try {
        user = await manager.signinRedirectCallback(window.location.href);
        if (user.expired || !user.access_token) throw new Error("The returned session is expired.");
        validateIdentityClaims(user.profile, c);
        const state = user.state as { returnTo?: unknown } | undefined;
        // Remove the single-use authorization code and state from history immediately.
        window.history.replaceState(null, "", safeReturnPath(state?.returnTo));
        window.dispatchEvent(new PopStateEvent("popstate"));
        publish({ mode: "cognito", authenticated: true,
          label: String(user.profile.email ?? user.profile.name ?? "Signed-in member") });
      } catch {
        window.history.replaceState(null, "", "/projects");
        clearIdentity();
        throw new Error("Sign-in could not be verified. Restart sign-in from this application; an unmatched or expired callback is never accepted.");
      }
    } else clearIdentity();
  })();
  return startup;
}
export async function signIn() {
  if (!manager) throw new Error("Sign-in is not configured.");
  await manager.signinRedirect({ nonce: crypto.randomUUID(), state: { returnTo: safeReturnPath(window.location.pathname + window.location.search) } });
}
export async function signOut() {
  if (!config || config.mode !== "cognito") return;
  // Remove local access before provider navigation, even if provider sign-out fails.
  const c = config;
  clearIdentity();
  await manager?.removeUser();
  const endpoint = new URL(c.logout_endpoint!);
  endpoint.searchParams.set("client_id", c.client_id!);
  endpoint.searchParams.set("logout_uri", c.logout_uri ?? `${window.location.origin}/projects`);
  window.location.assign(endpoint.toString());
}
