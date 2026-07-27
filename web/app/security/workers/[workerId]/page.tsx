import { AppShell } from "@/components/app-shell";
import { SecurityWorkerDetailConsole } from "@/features/security/security-worker-detail-console";

export const dynamic = "force-dynamic";

export default async function SecurityWorkerDetailPage({
  params,
}: {
  params: Promise<{ workerId: string }>;
}) {
  const { workerId } = await params;

  return (
    <AppShell
      eyebrow="Security operations"
      title={`Worker ${workerId}`}
      description="Identity, signing-key, and recent security-event detail for one registered worker, with admin lifecycle actions."
    >
      <SecurityWorkerDetailConsole workerId={workerId} />
    </AppShell>
  );
}
