import type { Page } from "@playwright/test";

import type { AuthRole, AuthSession } from "../../types/api";

// The Go API is a separate origin/port from the Next.js `web` app (see
// infra/compose/docker-compose.dev.yml: web publishes 3000, api
// publishes 8080), so a Playwright browser navigated to baseURL
// (http://localhost:3000) still needs an absolute URL to reach it.
// Defaults to the same http://127.0.0.1:8080 fallback web/lib/api.ts's
// client-side bundle resolves to, since no NEXT_PUBLIC_FL_API_BASE_URL
// is baked into the Docker build.
const API_BASE_URL = process.env.PLAYWRIGHT_API_BASE_URL ?? "http://127.0.0.1:8080";

const SESSION_STORAGE_KEY = "fl-platform-session";

export const DEMO_ACCOUNTS: Record<AuthRole, { email: string; password: string }> = {
  admin: { email: "admin@fl-platform.dev", password: "admin-demo" },
  researcher: { email: "researcher@fl-platform.dev", password: "research-demo" },
  viewer: { email: "viewer@fl-platform.dev", password: "viewer-demo" },
  service: { email: "service@fl-platform.dev", password: "service-demo" },
};

// Logs in via the real POST /api/v1/auth/login endpoint (no mocked
// response -- see login-console.tsx for the production request this
// mirrors) and seeds the resulting session into localStorage the same
// way the login console itself does, before any navigation happens on
// this page. Faster and less flaky than driving the login form through
// the UI on every test, while still exercising a real authenticated
// backend session rather than a fabricated token.
export async function loginAs(page: Page, role: AuthRole): Promise<AuthSession> {
  const { email, password } = DEMO_ACCOUNTS[role];
  const response = await page.request.post(`${API_BASE_URL}/api/v1/auth/login`, {
    data: { email, password },
  });
  if (!response.ok()) {
    throw new Error(`login as ${role} failed: HTTP ${response.status()} ${await response.text()}`);
  }
  const session = (await response.json()) as AuthSession;
  await page.addInitScript(
    ({ key, value }) => {
      window.localStorage.setItem(key, value);
    },
    { key: SESSION_STORAGE_KEY, value: JSON.stringify(session) },
  );
  return session;
}
