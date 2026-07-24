import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import { OverviewPage } from "@/features/overview/overview-page";
import { getOverviewData } from "@/lib/api";

// This page fetches live backend state on every request; it must not be
// statically prerendered at build time (there is no live backend during
// `next build`/`docker build`, and the data would be stale even if there
// were). See docs/known-limitations.md for the Docker build investigation
// this fixes.
export const dynamic = "force-dynamic";

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
