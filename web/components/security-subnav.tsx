import Link from "next/link";

// Shared secondary nav across the five Security Center routes -- kept as
// a plain server-renderable component (no client state) since it only
// ever needs the current path, supplied by each page.
const SECURITY_ROUTES: Array<{ href: string; label: string }> = [
  { href: "/security", label: "Overview" },
  { href: "/security/workers", label: "Worker Identities" },
  { href: "/security/coordinator-keys", label: "Coordinator Keys" },
  { href: "/security/events", label: "Event Explorer" },
  { href: "/security/audit", label: "Audit Explorer" },
  { href: "/security/secure-aggregation/privacy", label: "Secure User-Level DP" },
];

export function SecuritySubNav({ current }: { current: string }) {
  return (
    <nav className="pill-row" aria-label="Security Center sections">
      {SECURITY_ROUTES.map((route) => (
        <Link key={route.href} className={route.href === current ? "pill inline-link" : "pill"} href={route.href}>
          {route.label}
        </Link>
      ))}
    </nav>
  );
}
