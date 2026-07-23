import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import { OverviewPage } from "@/features/overview/overview-page";
import { getOverviewData } from "@/lib/api";

export default async function HomePage() {
  const data = await getOverviewData();

  return (
    <AppShell
      eyebrow="Platform overview"
      title="Federated learning control room"
      description="Overview of projects, experiments, and runs with a design language that is ready for future auth, live events, and privacy telemetry."
      actions={
        <>
          <Link className="button-primary" href="/experiments/new">
            New experiment
          </Link>
          <Link className="button-secondary" href="/runs/run-demo-1">
            Open live run view
          </Link>
        </>
      }
    >
      <OverviewPage data={data} />
    </AppShell>
  );
}
