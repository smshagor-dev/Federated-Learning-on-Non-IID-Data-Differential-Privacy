import { AppShell } from "@/components/app-shell";
import { AuditConsole } from "@/features/audit/audit-console";
import { getOverviewData } from "@/lib/api";

export default async function AuditPage() {
  const overview = await getOverviewData();

  return (
    <AppShell
      eyebrow="Audit workspace"
      title="Inspect governance and operations history"
      description="A dedicated review surface for login activity, project changes, experiment updates, and run lifecycle events."
    >
      <AuditConsole seedEvents={overview.activity_feed} />
    </AppShell>
  );
}
