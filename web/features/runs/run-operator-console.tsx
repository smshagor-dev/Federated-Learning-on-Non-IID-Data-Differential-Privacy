"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { getLiveRunData, mutateRunLifecycle } from "@/lib/api";
import { CoordinatorStatusPanel } from "@/features/runs/coordinator-status-panel";
import { RunDashboard } from "@/features/runs/run-dashboard";
import type { AuthSession, RunAction, RunDashboardData, RunStatus } from "@/types/api";

const statusByAction: Record<RunAction, RunStatus> = {
  start: "QUEUED",
  resume: "QUEUED",
  pause: "PAUSED",
  cancel: "CANCELED",
};

export function RunOperatorConsole({
  initialData,
  runId,
}: {
  initialData: RunDashboardData | undefined;
  runId: string;
}) {
  const router = useRouter();
  const [data, setData] = useState(initialData);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    const cached = window.localStorage.getItem("fl-platform-session");
    if (!cached) {
      return;
    }
    try {
      setSession(JSON.parse(cached) as AuthSession);
    } catch {
      window.localStorage.removeItem("fl-platform-session");
    }
  }, []);

  async function refreshFromLiveAPI() {
    const next = await getLiveRunData(runId);
    if (next) {
      setData(next);
    }
    router.refresh();
  }

  function handleRefresh() {
    setError(null);
    setNotice("Refreshing live signals...");
    startTransition(async () => {
      try {
        await refreshFromLiveAPI();
        setNotice("Live signals refreshed.");
      } catch (refreshError) {
        const message = refreshError instanceof Error ? refreshError.message : "Unable to refresh signals";
        setError(message);
        setNotice(null);
      }
    });
  }

  function handleAction(action: RunAction) {
    if (!session?.token) {
      setError("Sign in from the auth page first to operate this run.");
      return;
    }
    if (!data) {
      setError("Run data is not available yet.");
      return;
    }
    setError(null);
    setNotice(`Applying ${action}...`);
    const optimisticStatus = statusByAction[action];
    setData({
      ...data,
      run: {
        ...data.run,
        status: optimisticStatus,
      },
      signals: [`Operator requested ${action}. Pending control-plane confirmation.`, ...data.signals],
    });
    startTransition(async () => {
      try {
        await mutateRunLifecycle(runId, action, session.token);
        await refreshFromLiveAPI();
        setNotice(`Run ${action} completed successfully.`);
      } catch (mutationError) {
        const message = mutationError instanceof Error ? mutationError.message : `Unable to ${action} run`;
        setError(message);
        setNotice(null);
        setData(initialData);
      }
    });
  }

  const actions = getAllowedActions(data?.run.status);

  return (
    <div className="content-stack">
      <article className="card operator-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Operator console</div>
            <h2 className="card-title">Lifecycle controls</h2>
            <p className="card-copy">
              Drive the run directly from the dashboard with optimistic updates and a post-action sync from the Go
              control plane.
            </p>
          </div>
          <div className="operator-actions">
            {actions.map((action) => (
              <button
                className={action === "cancel" ? "button-secondary danger-button" : "button-primary"}
                disabled={isPending}
                key={action}
                onClick={() => handleAction(action)}
                type="button"
              >
                {labelForAction(action)}
              </button>
            ))}
            <button className="button-secondary" disabled={isPending} onClick={handleRefresh} type="button">
              Refresh live signals
            </button>
          </div>
        </div>
        <div className="pill-row">
          <span className="pill">Operator: {session?.user.display_name ?? "not signed in"}</span>
          <span className="pill">Role: {session?.user.role ?? "guest"}</span>
          <span className="pill">Run: {runId}</span>
        </div>
        {notice ? <div className="success-banner">{notice}</div> : null}
        {error ? <div className="notice">{error}</div> : null}
      </article>

      <RunDashboard data={data} />
      <CoordinatorStatusPanel runId={runId} token={session?.token} />
    </div>
  );
}

function getAllowedActions(status: RunStatus | undefined): RunAction[] {
  switch (status) {
    case "CREATED":
      return ["start", "cancel"];
    case "QUEUED":
      return ["cancel"];
    case "RUNNING":
      return ["pause", "cancel"];
    case "PAUSED":
      return ["resume", "cancel"];
    default:
      return [];
  }
}

function labelForAction(action: RunAction): string {
  switch (action) {
    case "start":
      return "Start run";
    case "resume":
      return "Resume run";
    case "pause":
      return "Pause run";
    case "cancel":
      return "Cancel run";
  }
}
