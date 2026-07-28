"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo, useState, type ReactNode } from "react";

import { useShellState } from "@/components/use-shell-state";
import type { CoordinatorAvailability } from "@/lib/api";

type NavItem = {
  href: string;
  label: string;
  icon: IconName;
  matches?: string[];
  disabled?: boolean;
};

type SearchItem = {
  key: string;
  label: string;
  meta: string;
  href?: string;
};

type IconName =
  | "overview"
  | "experiments"
  | "runs"
  | "registry"
  | "privacy"
  | "compare"
  | "audit"
  | "security"
  | "settings"
  | "search"
  | "bell"
  | "status"
  | "shield"
  | "artifact"
  | "menu"
  | "close";

export function AppShell({
  title,
  eyebrow,
  description,
  actions,
  children,
  rail,
}: {
  title: string;
  eyebrow: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
  rail?: ReactNode;
}) {
  const pathname = usePathname();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const { session, overview, projects, auditEvents, researchExperiments, coordinatorAvailability } = useShellState();

  const latestRunId = overview?.runs[0]?.id;
  const projectName = projects?.[0]?.name ?? overview?.projects[0]?.name ?? "No live projects loaded";
  const environmentLabel = overview?.source === "live" ? "Live Backend" : "Backend Unavailable";
  const notificationCount = auditEvents?.length ?? 0;
  const currentDate = formatCurrentDate();
  const primaryNav: NavItem[] = [
    { href: "/", label: "Overview", icon: "overview" },
    { href: "/experiments/new", label: "Experiments", icon: "experiments" },
    {
      href: latestRunId ? `/runs/${latestRunId}` : "/",
      label: "Runs",
      icon: "runs",
      matches: ["/runs/"],
      disabled: !latestRunId,
    },
    {
      href: "/models",
      label: "Research Registry",
      icon: "registry",
      matches: ["/models", "/datasets"],
    },
    {
      href: "/security/secure-aggregation/privacy",
      label: "Privacy",
      icon: "privacy",
      matches: ["/security/secure-aggregation/privacy"],
    },
    { href: "/compare", label: "Metrics", icon: "compare" },
    { href: "/audit", label: "Artifacts", icon: "artifact" },
    { href: "/security", label: "Security Center", icon: "security", matches: ["/security"] },
  ];
  const utilityNav: NavItem[] = [
    { href: "/audit", label: "Reports", icon: "audit" },
    { href: "/login", label: "Settings", icon: "settings", matches: ["/login"] },
  ];

  const searchResults = useMemo(
    () => buildSearchResults(searchQuery, overview, researchExperiments, latestRunId),
    [latestRunId, overview, researchExperiments, searchQuery],
  );

  return (
    <div className={`app-frame ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      {mobileOpen ? <button aria-label="Close navigation" className="sidebar-backdrop" onClick={() => setMobileOpen(false)} type="button" /> : null}

      <aside className={`sidebar ${sidebarCollapsed ? "collapsed" : ""} ${mobileOpen ? "mobile-open" : ""}`}>
        <div className="sidebar-header-row">
          <div className="sidebar-brand">
            <div className="brand-mark" aria-hidden="true">
              <BrandGlyph />
            </div>
            <div className="brand-copy">
              <div className="brand-title">FLRP</div>
              <div className="brand-subtitle">Federated Learning Research Platform</div>
            </div>
          </div>

          <button
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="sidebar-toggle desktop-only"
            onClick={() => setSidebarCollapsed((current) => !current)}
            type="button"
          >
            {sidebarCollapsed ? ">" : "<"}
          </button>
        </div>

        <nav className="sidebar-nav" aria-label="Primary navigation">
          {primaryNav.map((item) => (
            <NavLink
              key={item.label}
              active={matchesPath(pathname, item)}
              collapsed={sidebarCollapsed}
              disabled={item.disabled}
              href={item.href}
              icon={item.icon}
              label={item.label}
              onNavigate={() => setMobileOpen(false)}
            />
          ))}
        </nav>

        <nav className="sidebar-nav utility" aria-label="Utility navigation">
          {utilityNav.map((item) => (
            <NavLink
              key={item.label}
              active={matchesPath(pathname, item)}
              collapsed={sidebarCollapsed}
              disabled={item.disabled}
              href={item.href}
              icon={item.icon}
              label={item.label}
              onNavigate={() => setMobileOpen(false)}
            />
          ))}
        </nav>

        <div className="sidebar-project-card">
          <div className="sidebar-card-label">Current Project</div>
          <div className="sidebar-project-name">{projectName}</div>
          <div className="sidebar-project-meta">
            <span className="surface-chip emphasis">{session?.user.role ?? "guest"}</span>
            <span className="surface-chip">{environmentLabel}</span>
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="sidebar-card-label">Platform version</div>
          <div className="sidebar-footer-status">
            <span className={`status-dot ${availabilityToClass(coordinatorAvailability)}`} />
            <span>{availabilityLabel(coordinatorAvailability)}</span>
          </div>
        </div>
      </aside>

      <div className="workspace-shell">
        <header className="workspace-topbar">
          <div className="topbar-leading">
            <button aria-label="Open navigation" className="sidebar-toggle mobile-only" onClick={() => setMobileOpen(true)} type="button">
              <Icon name="menu" />
            </button>

            <div className="topbar-control topbar-select">
              <span className="topbar-icon">
                <Icon name="status" />
              </span>
              <div>
                <div className="topbar-label">Environment</div>
                <div className="topbar-value">{environmentLabel}</div>
              </div>
              <span className="topbar-chevron" aria-hidden="true">
                v
              </span>
            </div>
          </div>

          <div className="topbar-search-wrap">
            <label className="topbar-search" aria-label="Search loaded platform data">
              <span className="topbar-search-icon">
                <Icon name="search" />
              </span>
              <input
                aria-label="Search loaded experiments, runs, models, and datasets"
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search loaded experiments, runs, models, datasets..."
                type="search"
                value={searchQuery}
              />
              <span className="topbar-keycap">Loaded data</span>
            </label>
            {searchQuery.trim() ? (
              <div className="search-results-panel">
                {searchResults.length > 0 ? (
                  searchResults.map((item) =>
                    item.href ? (
                      <Link className="search-result" href={item.href} key={item.key} onClick={() => setSearchQuery("")}>
                        <strong>{item.label}</strong>
                        <span>{item.meta}</span>
                      </Link>
                    ) : (
                      <div className="search-result static" key={item.key}>
                        <strong>{item.label}</strong>
                        <span>{item.meta}</span>
                      </div>
                    ),
                  )
                ) : (
                  <div className="search-result static">
                    <strong>No loaded matches</strong>
                    <span>Frontend search is scoped to data already returned by the current APIs.</span>
                  </div>
                )}
              </div>
            ) : null}
          </div>

          <div className="topbar-actions">
            <button className="icon-button" type="button" aria-label="Recent audit activity">
              <Icon name="bell" />
              {notificationCount > 0 ? <span className="notification-badge">{notificationCount}</span> : null}
            </button>

            <div className="profile-chip">
              <div className="profile-avatar" aria-hidden="true">
                {initials(session?.user.display_name ?? "Guest Viewer")}
              </div>
              <div className="profile-copy">
                <div className="profile-name">{session?.user.display_name ?? "Guest Viewer"}</div>
                <div className="profile-role">{session?.user.role ?? "guest"}</div>
              </div>
            </div>
          </div>
        </header>

        <div className="workspace-body">
          <main className="main-panel">
            <header className="page-header">
              <div>
                <div className="page-eyebrow">{eyebrow}</div>
                <h1 className="page-title">{title}</h1>
                <p className="page-description">{description}</p>
              </div>

              <div className="page-header-tools">
                {actions ? <div className="page-actions">{actions}</div> : null}
                <div className="header-utility">{currentDate}</div>
              </div>
            </header>

            <div className="status-strip" aria-label="Operational context">
              <StatusToken color={availabilityColor(coordinatorAvailability)} text={availabilityLabel(coordinatorAvailability)} />
              <StatusToken
                color="indigo"
                text={`Privacy Mode: ${String(overview?.runs[0]?.config.privacy_mode ?? "Unavailable")}`}
              />
              <StatusToken color="cyan" text={`Execution Mode: ${String(overview?.runs[0]?.config.mode ?? "Unavailable")}`} />
            </div>

            <section className="content-stack">{children}</section>
          </main>

          <aside className="status-rail">{rail ?? <DefaultRail coordinatorAvailability={coordinatorAvailability} latestRunId={latestRunId} overview={overview} auditCount={auditEvents?.length ?? 0} researchExperiments={researchExperiments} />}</aside>
        </div>
      </div>
    </div>
  );
}

function NavLink({
  href,
  label,
  icon,
  active,
  collapsed,
  disabled,
  onNavigate,
}: {
  href: string;
  label: string;
  icon: IconName;
  active: boolean;
  collapsed: boolean;
  disabled?: boolean;
  onNavigate: () => void;
}) {
  if (disabled) {
    return (
      <span aria-disabled="true" className="nav-link disabled" title={label}>
        <span className="nav-icon" aria-hidden="true">
          <Icon name={icon} />
        </span>
        <span className={`nav-label ${collapsed ? "hidden" : ""}`}>{label}</span>
      </span>
    );
  }

  return (
    <Link className={`nav-link ${active ? "active" : ""}`} href={href} onClick={onNavigate} title={label}>
      <span className="nav-icon" aria-hidden="true">
        <Icon name={icon} />
      </span>
      <span className={`nav-label ${collapsed ? "hidden" : ""}`}>{label}</span>
    </Link>
  );
}

function DefaultRail({
  overview,
  researchExperiments,
  coordinatorAvailability,
  auditCount,
  latestRunId,
}: {
  overview?: { projects: unknown[]; runs: Array<{ id: string }>; metrics: { running_runs: number; active_projects: number } };
  researchExperiments?: Array<{ degraded?: boolean }>;
  coordinatorAvailability?: CoordinatorAvailability;
  auditCount: number;
  latestRunId?: string;
}) {
  const degradedCount = researchExperiments?.filter((item) => item.degraded).length ?? 0;

  return (
    <>
      <RailPanel title="Runtime Health" tone={coordinatorAvailability === "connected" ? "success" : "neutral"}>
        <RailList
          items={[
            ["Dashboard API", overview ? "Reachable" : "Unavailable"],
            ["Coordinator", availabilityLabel(coordinatorAvailability)],
            ["Active Runs", String(overview?.metrics.running_runs ?? 0)],
            ["Projects", String(overview?.metrics.active_projects ?? 0)],
          ]}
        />
      </RailPanel>

      <RailPanel title="Command Integrity" tone={auditCount > 0 ? "success" : "neutral"}>
        <RailList
          items={[
            ["Audit feed", auditCount > 0 ? "Reachable" : "Unavailable"],
            ["Records loaded", String(auditCount)],
            ["Replay protection", "API-enforced"],
          ]}
        />
      </RailPanel>

      <RailPanel title="Research Writer Status" tone={researchExperiments ? "success" : "warning"}>
        <RailList
          items={[
            ["Registry read", researchExperiments ? "Reachable" : "Unavailable"],
            ["Records loaded", String(researchExperiments?.length ?? 0)],
            ["Degraded records", String(degradedCount)],
          ]}
        />
      </RailPanel>

      <RailPanel title="Research Guardrails" tone="warning">
        <ul className="rail-note-list">
          <li>No combined hybrid epsilon is shown.</li>
          <li>Dropout recovery remains unavailable.</li>
          <li>Only live API responses are rendered. Unavailable data stays unavailable.</li>
        </ul>
      </RailPanel>

      <RailPanel title="Quick Actions" tone="action">
        <div className="quick-action-grid">
          <Link className="quick-action" href="/experiments/new">
            <Icon name="experiments" />
            <span>New Experiment</span>
          </Link>
          {latestRunId ? (
            <Link className="quick-action" href={`/runs/${latestRunId}`}>
              <Icon name="runs" />
              <span>Open Latest Run</span>
            </Link>
          ) : (
            <span className="quick-action disabled">
              <Icon name="runs" />
              <span>No Live Run</span>
            </span>
          )}
          <Link className="quick-action" href="/security">
            <Icon name="shield" />
            <span>Security Center</span>
          </Link>
          <Link className="quick-action" href="/audit">
            <Icon name="audit" />
            <span>Audit Feed</span>
          </Link>
        </div>
      </RailPanel>
    </>
  );
}

function RailPanel({
  title,
  tone,
  children,
}: {
  title: string;
  tone: "neutral" | "success" | "warning" | "action";
  children: ReactNode;
}) {
  return (
    <section className={`rail-panel ${tone}`}>
      <div className="rail-panel-header">
        <h2 className="rail-title">{title}</h2>
        <span className={`rail-badge ${tone}`}>{railToneLabel(tone)}</span>
      </div>
      {children}
    </section>
  );
}

function RailList({ items }: { items: Array<[string, string]> }) {
  return (
    <div className="rail-list">
      {items.map(([label, value]) => (
        <div className="rail-list-row" key={`${label}-${value}`}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}

function StatusToken({ color, text }: { color: "success" | "indigo" | "cyan" | "neutral"; text: string }) {
  return (
    <span className={`status-token ${color}`}>
      <span className="status-dot" />
      {text}
    </span>
  );
}

function matchesPath(pathname: string | null, item: NavItem) {
  if (!pathname) {
    return false;
  }
  if (pathname === item.href) {
    return true;
  }
  return item.matches?.some((prefix) => pathname.startsWith(prefix)) ?? false;
}

function initials(name: string) {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

function railToneLabel(tone: "neutral" | "success" | "warning" | "action") {
  switch (tone) {
    case "success":
      return "Healthy";
    case "warning":
      return "Bounded";
    case "action":
      return "Live";
    default:
      return "Observed";
  }
}

function availabilityLabel(availability?: CoordinatorAvailability) {
  switch (availability) {
    case "connected":
      return "Coordinator Connected";
    case "unavailable":
      return "Coordinator Unavailable";
    case "unauthorized":
      return "Coordinator Unauthorized";
    default:
      return "Coordinator Unknown";
  }
}

function availabilityColor(availability?: CoordinatorAvailability): "success" | "indigo" | "cyan" | "neutral" {
  switch (availability) {
    case "connected":
      return "success";
    case "unavailable":
      return "neutral";
    case "unauthorized":
      return "neutral";
    default:
      return "neutral";
  }
}

function availabilityToClass(availability?: CoordinatorAvailability) {
  return availability === "connected" ? "success" : "neutral";
}

function formatCurrentDate() {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date());
}

function buildSearchResults(
  query: string,
  overview?: {
    projects: Array<{ id: string; name: string }>;
    experiments: Array<{ id: string; name: string }>;
    runs: Array<{ id: string; experiment_id: string }>;
  },
  researchExperiments?: Array<{ experiment_id: string; display_name: string; dataset_id: string; model_id: string }>,
  latestRunId?: string,
): SearchItem[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    return [];
  }

  const items: SearchItem[] = [];

  for (const run of overview?.runs ?? []) {
    if (`${run.id} ${run.experiment_id}`.toLowerCase().includes(normalized)) {
      items.push({
        key: `run-${run.id}`,
        label: run.id,
        meta: `Run for experiment ${run.experiment_id}`,
        href: `/runs/${run.id}`,
      });
    }
  }

  for (const experiment of researchExperiments ?? []) {
    if (`${experiment.experiment_id} ${experiment.display_name} ${experiment.dataset_id} ${experiment.model_id}`.toLowerCase().includes(normalized)) {
      items.push({
        key: `research-${experiment.experiment_id}`,
        label: experiment.display_name,
        meta: `Experiment ${experiment.experiment_id} • dataset ${experiment.dataset_id} • model ${experiment.model_id}`,
      });
    }
  }

  for (const experiment of overview?.experiments ?? []) {
    if (`${experiment.id} ${experiment.name}`.toLowerCase().includes(normalized)) {
      items.push({
        key: `overview-experiment-${experiment.id}`,
        label: experiment.name,
        meta: `Experiment ${experiment.id} is loaded on the overview feed.`,
      });
    }
  }

  for (const project of overview?.projects ?? []) {
    if (`${project.id} ${project.name}`.toLowerCase().includes(normalized)) {
      items.push({
        key: `project-${project.id}`,
        label: project.name,
        meta: `Project ${project.id} is loaded on the overview feed.`,
      });
    }
  }

  if ("metrics".includes(normalized) || normalized.includes("metric")) {
    items.push({ key: "metrics-route", label: "Metrics", meta: "Open the current metrics workspace.", href: "/compare" });
  }
  if ("artifacts".includes(normalized) || normalized.includes("artifact")) {
    items.push({ key: "artifacts-route", label: "Artifacts", meta: "Open the current artifact workspace.", href: "/audit" });
  }
  if ((normalized.includes("latest") || normalized.includes("run")) && latestRunId) {
    items.push({ key: "latest-run", label: "Latest Run", meta: latestRunId, href: `/runs/${latestRunId}` });
  }

  return items.slice(0, 8);
}

function BrandGlyph() {
  return (
    <svg fill="none" viewBox="0 0 44 44">
      <path
        d="M22 4 35.5 11.5V26.5L22 34 8.5 26.5V11.5L22 4Z"
        stroke="currentColor"
        strokeWidth="1.8"
      />
      <path d="M22 4V34M8.5 11.5 35.5 26.5M35.5 11.5 8.5 26.5" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="22" cy="4" r="2.3" fill="currentColor" />
      <circle cx="35.5" cy="11.5" r="2.3" fill="currentColor" />
      <circle cx="35.5" cy="26.5" r="2.3" fill="currentColor" />
      <circle cx="22" cy="34" r="2.3" fill="currentColor" />
      <circle cx="8.5" cy="26.5" r="2.3" fill="currentColor" />
      <circle cx="8.5" cy="11.5" r="2.3" fill="currentColor" />
      <circle cx="22" cy="19" r="2.5" fill="currentColor" />
    </svg>
  );
}

function Icon({ name }: { name: IconName }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.8,
    viewBox: "0 0 24 24",
  };

  switch (name) {
    case "overview":
      return (
        <svg {...common}>
          <path d="M4 12.5 12 5l8 7.5" />
          <path d="M6.5 10.5V19h11v-8.5" />
        </svg>
      );
    case "experiments":
      return (
        <svg {...common}>
          <path d="M8 3v4l-4 7a5 5 0 0 0 4.3 7h7.4A5 5 0 0 0 20 14l-4-7V3" />
          <path d="M8 7h8" />
        </svg>
      );
    case "runs":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="m10 8 6 4-6 4V8Z" />
        </svg>
      );
    case "registry":
      return (
        <svg {...common}>
          <path d="M5 5h14v14H5z" />
          <path d="M9 5v14M15 5v14M5 9h14M5 15h14" />
        </svg>
      );
    case "privacy":
    case "shield":
      return (
        <svg {...common}>
          <path d="M12 3 6 5.5v5.8c0 4.3 2.6 7.2 6 9.7 3.4-2.5 6-5.4 6-9.7V5.5L12 3Z" />
          <path d="m9.5 12 1.8 1.8 3.4-3.7" />
        </svg>
      );
    case "compare":
      return (
        <svg {...common}>
          <path d="M5 19V9M12 19V5M19 19v-7" />
        </svg>
      );
    case "audit":
      return (
        <svg {...common}>
          <path d="M8 4h8l3 3v13H5V4h3Z" />
          <path d="M9 11h6M9 15h6" />
        </svg>
      );
    case "security":
      return (
        <svg {...common}>
          <path d="M4 12c0-4.4 3.6-8 8-8s8 3.6 8 8" />
          <path d="M7 12v6h10v-6" />
          <circle cx="12" cy="15" r="1.2" />
        </svg>
      );
    case "settings":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2.8v2.1M12 19.1v2.1M4.9 4.9l1.5 1.5M17.6 17.6l1.5 1.5M2.8 12h2.1M19.1 12h2.1M4.9 19.1l1.5-1.5M17.6 6.4l1.5-1.5" />
        </svg>
      );
    case "search":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="6.5" />
          <path d="m16 16 4 4" />
        </svg>
      );
    case "bell":
      return (
        <svg {...common}>
          <path d="M15.5 17H8.5l1-2V11a4.5 4.5 0 1 1 9 0v4l1 2Z" />
          <path d="M10 18.5a2 2 0 0 0 4 0" />
        </svg>
      );
    case "status":
      return (
        <svg {...common}>
          <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
          <path d="M7.5 9.5h9M7.5 14.5h5" />
        </svg>
      );
    case "artifact":
      return (
        <svg {...common}>
          <path d="M12 3 20 7.5v9L12 21l-8-4.5v-9L12 3Z" />
          <path d="M4.5 7.8 12 12l7.5-4.2M12 12v9" />
        </svg>
      );
    case "menu":
      return (
        <svg {...common}>
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      );
    case "close":
      return (
        <svg {...common}>
          <path d="M6 6 18 18M18 6 6 18" />
        </svg>
      );
  }
}
