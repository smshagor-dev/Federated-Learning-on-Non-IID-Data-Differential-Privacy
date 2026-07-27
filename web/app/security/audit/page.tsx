import { AppShell } from "@/components/app-shell";
import { SecurityAuditConsole } from "@/features/security/security-audit-console";

export const dynamic = "force-dynamic";

export default function SecurityAuditPage() {
  return (
    <AppShell
      eyebrow="Security operations"
      title="Security audit explorer"
      description="Durable, append-only audit trail for every security-relevant administrative action."
    >
      <SecurityAuditConsole />
    </AppShell>
  );
}
