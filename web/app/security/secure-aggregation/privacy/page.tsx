import { AppShell } from "@/components/app-shell";
import { SecureUserLevelDPConsole } from "@/features/security/secure-user-level-dp-console";

export const dynamic = "force-dynamic";

export default function SecureUserLevelDPPrivacyPage() {
  return (
    <AppShell
      eyebrow="Security operations"
      title="Secure user-level DP privacy runtime"
      description="Operational observability for the honest-client-dependent secure user-level differential privacy mechanism -- capability, mechanism, budget, and runtime health, never a clear update or noise value."
    >
      <SecureUserLevelDPConsole />
    </AppShell>
  );
}
