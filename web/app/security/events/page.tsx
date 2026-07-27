import { AppShell } from "@/components/app-shell";
import { SecurityEventsConsole } from "@/features/security/security-events-console";

export const dynamic = "force-dynamic";

export default function SecurityEventsPage() {
  return (
    <AppShell
      eyebrow="Security operations"
      title="Security event explorer"
      description="Live-polled, filterable view of signed-message, privacy-record, and coordinator-task security events."
    >
      <SecurityEventsConsole />
    </AppShell>
  );
}
