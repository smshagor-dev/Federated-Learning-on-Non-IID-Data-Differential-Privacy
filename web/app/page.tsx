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
  const latestRunId = data?.runs[0]?.id;

  return (
    <AppShell
      eyebrow="Research operations"
      title="Research Evaluation Dashboard"
      description="Monitor experiments, privacy posture, and reproducibility signals across the federated learning platform with live control-plane data and explicit degraded states when services are unavailable."
      actions={
        <>
          <Link className="button-primary" href="/experiments/new">
            New experiment
          </Link>
          {latestRunId ? (
            <Link className="button-secondary" href={`/runs/${latestRunId}`}>
              Open latest run
            </Link>
          ) : (
            <span className="button-secondary button-disabled">No live run loaded</span>
          )}
        </>
      }
    >
      <OverviewPage data={data} />
    </AppShell>
  );
}
