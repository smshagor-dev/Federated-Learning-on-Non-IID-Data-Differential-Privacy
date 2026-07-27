import { AppShell } from "@/components/app-shell";
import { SecurityOverviewConsole } from "@/features/security/security-overview-console";

// Every /api/v1/security/* route requires a Bearer token (no cookie
// auth -- see lib/api.ts), and the token only exists in the browser's
// localStorage session, so unlike app/audit/page.tsx this page has no
// unauthenticated seed data to fetch server-side. dynamic="force-dynamic"
// still applies for consistency with every other dashboard route (see
// app/page.tsx) even though this particular page renders no server-side
// backend call itself.
export const dynamic = "force-dynamic";

export default function SecurityOverviewPage() {
  return (
    <AppShell
      eyebrow="Security operations"
      title="Web Security Center"
      description="Aggregate view of transport security, worker and coordinator signing-key health, signed-message and privacy-record verification outcomes, and durable security/audit journal status."
    >
      <SecurityOverviewConsole />
    </AppShell>
  );
}
