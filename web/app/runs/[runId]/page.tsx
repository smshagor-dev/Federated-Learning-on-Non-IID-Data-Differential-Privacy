import { AppShell } from "@/components/app-shell";
import { RunOperatorConsole } from "@/features/runs/run-operator-console";
import { getRunData } from "@/lib/api";

export default async function RunPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  const data = await getRunData(runId);

  return (
    <AppShell
      eyebrow="Live run dashboard"
      title={`Run ${runId}`}
      description="Structured run monitoring shell for status, privacy, client fleet, and security signals. Real-time streaming hooks can plug into this layout later."
    >
      <RunOperatorConsole initialData={data} runId={runId} />
    </AppShell>
  );
}
