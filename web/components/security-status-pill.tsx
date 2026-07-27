// Web Security Center slice: a generalized status pill for the security
// domain's own vocabulary (worker/key lifecycle states, event
// severities, message outcomes), kept separate from the existing
// StatusPill (components/status-pill.tsx), which is typed specifically
// to RunStatus and whose variant classes ("failed", "completed", ...)
// would otherwise collide with same-named-but-differently-colored
// security states.
const SECURITY_STATUS_VARIANTS: Record<string, "sec-good" | "sec-warn" | "sec-bad" | "sec-neutral"> = {
  active: "sec-good",
  accepted: "sec-good",
  completed: "sec-good",
  connected: "sec-good",

  grace_period: "sec-warn",
  suspended: "sec-warn",
  warning: "sec-warn",
  high: "sec-warn",
  pending: "sec-warn",
  stale: "sec-warn",

  expired: "sec-bad",
  revoked: "sec-bad",
  rejected: "sec-bad",
  blocked: "sec-bad",
  failed: "sec-bad",
  critical: "sec-bad",
  unavailable: "sec-bad",
  corrupted: "sec-bad",

  canceled: "sec-neutral",
  info: "sec-neutral",
  unknown: "sec-neutral",
};

export function SecurityStatusPill({ status }: { status: string }) {
  const key = status.toLowerCase();
  const variant = SECURITY_STATUS_VARIANTS[key] ?? "sec-neutral";
  return <span className={`status-pill ${variant}`}>{status}</span>;
}
