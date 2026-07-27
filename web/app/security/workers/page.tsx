import { AppShell } from "@/components/app-shell";
import { SecurityWorkersConsole } from "@/features/security/security-workers-console";

export const dynamic = "force-dynamic";

export default function SecurityWorkersPage() {
  return (
    <AppShell
      eyebrow="Security operations"
      title="Worker identities and signing keys"
      description="Registered worker certificates, signing-key bindings, and lifecycle status across the fleet."
    >
      <SecurityWorkersConsole />
    </AppShell>
  );
}
