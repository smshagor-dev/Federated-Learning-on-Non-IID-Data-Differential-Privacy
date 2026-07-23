"use client";

import { useEffect, useState, useTransition } from "react";

import { loginWithPassword } from "@/lib/api";
import type { AuthRole, AuthSession } from "@/types/api";

const demoAccounts = [
  {
    role: "admin" as const,
    email: "admin@fl-platform.dev",
    password: "admin-demo",
    emphasis: 100,
    note: "Full platform governance, write access, and future tenant controls.",
  },
  {
    role: "researcher" as const,
    email: "researcher@fl-platform.dev",
    password: "research-demo",
    emphasis: 88,
    note: "Experiment authoring, project creation, and run operations.",
  },
  {
    role: "viewer" as const,
    email: "viewer@fl-platform.dev",
    password: "viewer-demo",
    emphasis: 46,
    note: "Read-only portfolio visibility for stakeholders and reviewers.",
  },
  {
    role: "service" as const,
    email: "service@fl-platform.dev",
    password: "service-demo",
    emphasis: 62,
    note: "Automation identity for future orchestration and integrations.",
  },
];

const roleBands: Array<{ role: AuthRole; access: number; label: string }> = [
  { role: "admin", access: 100, label: "Platform authority" },
  { role: "researcher", access: 82, label: "Research execution" },
  { role: "service", access: 64, label: "Automation rails" },
  { role: "viewer", access: 38, label: "Read-only review" },
];

export function LoginConsole() {
  const [email, setEmail] = useState("researcher@fl-platform.dev");
  const [password, setPassword] = useState("research-demo");
  const [session, setSession] = useState<AuthSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    const cached = window.localStorage.getItem("fl-platform-session");
    if (!cached) {
      return;
    }
    try {
      setSession(JSON.parse(cached) as AuthSession);
    } catch {
      window.localStorage.removeItem("fl-platform-session");
    }
  }, []);

  function handleDemoFill(nextEmail: string, nextPassword: string) {
    setEmail(nextEmail);
    setPassword(nextPassword);
    setError(null);
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    startTransition(async () => {
      try {
        const nextSession = await loginWithPassword(email, password);
        setSession(nextSession);
        window.localStorage.setItem("fl-platform-session", JSON.stringify(nextSession));
      } catch (submitError) {
        const message = submitError instanceof Error ? submitError.message : "Unable to sign in";
        setError(message);
      }
    });
  }

  return (
    <div className="content-stack">
      <div className="double-grid auth-hero-grid">
        <article className="card auth-hero-card">
          <div className="eyebrow">Identity control</div>
          <h2 className="card-title auth-title">Role-aware platform access with demo-ready sessions</h2>
          <p className="card-copy">
            The Go control plane now exposes real login and bearer-auth endpoints, and this console lets us test
            those roles with a UX that feels closer to an operations cockpit than a plain form.
          </p>
          <div className="bar-stack">
            {roleBands.map((band) => (
              <div className="bar-row" key={band.role}>
                <span>{band.role}</span>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${band.access}%` }} />
                </div>
                <strong>{band.label}</strong>
              </div>
            ))}
          </div>
        </article>

        <article className="card alt">
          <div className="eyebrow">Demo identities</div>
          <div className="credential-list">
            {demoAccounts.map((account) => (
              <button
                className="credential-card"
                key={account.role}
                onClick={() => handleDemoFill(account.email, account.password)}
                type="button"
              >
                <div className="credential-card-top">
                  <strong>{account.role}</strong>
                  <span>{account.email}</span>
                </div>
                <div className="bar-track compact">
                  <div className="bar-fill" style={{ width: `${account.emphasis}%` }} />
                </div>
                <p className="muted">{account.note}</p>
              </button>
            ))}
          </div>
        </article>
      </div>

      <div className="double-grid">
        <article className="card">
          <div className="eyebrow">Password sign-in</div>
          <form className="content-stack" onSubmit={handleSubmit}>
            <label className="field-card">
              <span className="field-label">Email</span>
              <input className="input" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
            </label>
            <label className="field-card">
              <span className="field-label">Password</span>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            {error ? <div className="notice">{error}</div> : null}
            <div className="action-row">
              <button className="button-primary" disabled={isPending} type="submit">
                {isPending ? "Signing in..." : "Sign in to control plane"}
              </button>
              <button
                className="button-secondary"
                onClick={() => handleDemoFill("viewer@fl-platform.dev", "viewer-demo")}
                type="button"
              >
                Load read-only account
              </button>
            </div>
          </form>
        </article>

        <article className="card auth-session-card">
          <div className="eyebrow">Session state</div>
          {session ? (
            <div className="content-stack">
              <div className="pill-row">
                <span className="pill">Role: {session.user.role}</span>
                <span className="pill">User: {session.user.display_name}</span>
              </div>
              <div className="auth-banner">
                <strong>Bearer token</strong>
                <code className="mono-token">{session.token}</code>
              </div>
              <div className="section-grid auth-meta-grid">
                <div className="field-card">
                  <span className="field-label">Session expiry</span>
                  <div>{new Date(session.expires_at).toLocaleString()}</div>
                </div>
                <div className="field-card">
                  <span className="field-label">Last login</span>
                  <div>{new Date(session.user.last_login_at).toLocaleString()}</div>
                </div>
              </div>
              <div>
                <div className="field-label">Capabilities</div>
                <div className="pill-row">
                  {session.capabilities.map((capability) => (
                    <span className="pill" key={capability}>
                      {capability}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="card-copy">
              No active browser session yet. Sign in with one of the seeded accounts to inspect the role policy and
              token payload returned by the Go API.
            </div>
          )}
        </article>
      </div>
    </div>
  );
}
