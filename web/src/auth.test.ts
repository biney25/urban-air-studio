import { describe, expect, it } from "vitest";
import { safeReturnPath, validateAuthConfig, validateIdentityClaims, type AuthConfig } from "./auth";
const origin = "https://studio.example.org";
const good = { mode: "cognito", configured: true, issuer: "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_example",
  client_id: "public-client", authorization_endpoint: "https://auth.example.org/oauth2/authorize",
  token_endpoint: "https://auth.example.org/oauth2/token", logout_endpoint: "https://auth.example.org/logout",
  redirect_uri: `${origin}/auth/callback`, logout_uri: `${origin}/projects`, scopes: ["openid", "email", "profile"] };
describe("deployment access boundary", () => {
  it("fails closed without explicit access configuration", () => {
    for (const value of [undefined, {}, { mode: "local" }, { mode: "cognito", configured: false }])
      expect(() => validateAuthConfig(value, origin)).toThrow();
  });
  it("permits only the explicit server-confirmed local mode", () => {
    expect(validateAuthConfig({ mode: "local", configured: true }, origin).mode).toBe("local");
  });
  it("accepts secure configured PKCE endpoint metadata", () => {
    expect(validateAuthConfig(good, origin).client_id).toBe("public-client");
  });
  it("rejects callback cross-origin, fragments and token endpoints over HTTP", () => {
    for (const change of [{ redirect_uri: "https://attacker.example/auth/callback" }, { redirect_uri: `${origin}/auth/callback?next=/` },
      { token_endpoint: "http://auth.example.org/oauth2/token" }, { logout_uri: "https://attacker.example/" }, { scopes: ["email"] }])
      expect(() => validateAuthConfig({ ...good, ...change }, origin)).toThrow();
  });
  it("prevents return-path open redirects and callback loops", () => {
    for (const path of ["//attacker.example", "/\\attacker.example", "https://attacker.example", "/auth/callback?code=secret", undefined])
      expect(safeReturnPath(path)).toBe("/projects");
    expect(safeReturnPath("/projects/project_123/compare?scenario=scenario_abc")).toContain("/compare?");
  });
  it("rejects mismatched, expired and wrong-use identity claims independently of session duration", () => {
    const profile = { iss: good.issuer, aud: good.client_id, sub: "member", token_use: "id", exp: 2000, iat: 990 };
    expect(() => validateIdentityClaims(profile, good as AuthConfig, 1000)).not.toThrow();
    for (const change of [{ iss: "https://another.example" }, { aud: "other-client" }, { exp: 900 }, { token_use: "access" }, { sub: "" }, { iat: 1200 }])
      expect(() => validateIdentityClaims({ ...profile, ...change }, good as AuthConfig, 1000)).toThrow();
  });
});
