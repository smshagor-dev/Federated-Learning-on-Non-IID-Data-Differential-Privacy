import type { RunStatus } from "@/types/api";

export function StatusPill({ status }: { status: RunStatus }) {
  return <span className={`status-pill ${status.toLowerCase()}`}>{status}</span>;
}
