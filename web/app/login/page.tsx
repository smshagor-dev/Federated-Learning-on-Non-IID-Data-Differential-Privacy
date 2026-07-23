import { AppShell } from "@/components/app-shell";
import { LoginConsole } from "@/features/auth/login-console";

export default function LoginPage() {
  return (
    <AppShell
      eyebrow="Authentication control"
      title="Secure the federated control plane"
      description="Role-aware sign-in is now wired to the Go API, with seeded demo identities that help us verify viewer, researcher, service, and admin access patterns."
    >
      <LoginConsole />
    </AppShell>
  );
}
