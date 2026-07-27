import { AppShell } from "@/components/app-shell";
import { SecurityCoordinatorKeysConsole } from "@/features/security/security-coordinator-keys-console";

export const dynamic = "force-dynamic";

export default function SecurityCoordinatorKeysPage() {
  return (
    <AppShell
      eyebrow="Security operations"
      title="Coordinator signing keys"
      description="Rotate and revoke the coordinator's own task-signing identity, with mandatory reason, confirmation, and idempotency-key protection."
    >
      <SecurityCoordinatorKeysConsole />
    </AppShell>
  );
}
