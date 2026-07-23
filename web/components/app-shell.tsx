import Link from "next/link";
import type { ReactNode } from "react";

export function AppShell({
  title,
  eyebrow,
  description,
  actions,
  children,
}: {
  title: string;
  eyebrow: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-title">Federated Research OS</div>
          <div className="brand-copy">
            Milestone dashboard shell for experiments, privacy, scheduling, and platform operations.
          </div>
        </div>
        <nav className="nav-stack">
          <Link className="nav-link" href="/">
            Overview
          </Link>
          <Link className="nav-link" href="/experiments/new">
            Experiment Builder
          </Link>
          <Link className="nav-link" href="/runs/run-demo-1">
            Live Run View
          </Link>
          <Link className="nav-link" href="/audit">
            Audit Feed
          </Link>
          <Link className="nav-link" href="/login">
            Auth Shell
          </Link>
        </nav>
        <div className="sidebar-footnote">
          Current milestone keeps auth and live streaming interfaces ready for future Go-backed integration.
        </div>
      </aside>
      <main className="main-panel">
        <header className="topbar">
          <div>
            <div className="eyebrow">{eyebrow}</div>
            <h1 className="headline">{title}</h1>
            <p className="subhead">{description}</p>
          </div>
          {actions ? <div className="action-row">{actions}</div> : null}
        </header>
        <section className="content-stack">{children}</section>
      </main>
    </div>
  );
}
