import { API_BASE_URL } from "@/lib/api";
import type {
  CoordinatorSigningKey,
  RevokeCoordinatorSigningKeyResult,
  RotateCoordinatorSigningKeyResult,
  SecureUserDPBudget,
  SecureUserDPCapability,
  SecureUserDPHealth,
  SecureUserDPRound,
  SecureUserDPRoundsPage,
  SecurityAuditResponse,
  SecurityEventRecord,
  SecurityEventSourcesResponse,
  SecurityOverview,
  SecurityWorker,
  WorkerLifecycleResult,
  WorkerSigningKey,
  WorkerSigningKeyRevocationResult,
} from "@/types/api";

// Web Security Center, Event Centralization, and Security CI slice
// (Work Package I): typed client functions for the /api/v1/security/*
// surface (go/internal/transport/httpapi/security_handlers.go,
// security_overview.go). Deliberately a separate module from lib/api.ts
// rather than appended to it -- every function here accepts a caller-
// supplied AbortSignal (so a page navigating away or a poll tick being
// superseded can cancel an in-flight request) and mutation functions
// require an idempotency key (mirroring the Go side's requirement that
// coordinator-key rotation, especially, is not safely retryable without
// one). Neither capability exists on lib/api.ts's older functions today;
// adding both there instead of here would have changed those functions'
// existing contract for callers that don't need either.
//
// Two response shapes, matching lib/api.ts's existing split:
// - read functions return `undefined` on a non-OK response or network
//   failure (matching getCoordinatorRun/getPersonalizationRecordsWithToken),
//   so a page can render an explicit "unavailable" state instead of a
//   thrown error breaking a poll loop.
// - mutation functions throw (matching createProjectWithToken), since a
//   failed admin action must surface as an explicit error the caller
//   handles, never silently swallowed.

const REQUEST_TIMEOUT_MS = 8_000;

function combinedSignal(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

async function securityRead<T>(path: string, token: string, signal?: AbortSignal): Promise<T | undefined> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: combinedSignal(signal),
    });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as T;
  } catch {
    return undefined;
  }
}

export type SecurityMutationInput = {
  reason?: string;
  requestId?: string;
  traceId?: string;
  idempotencyKey: string;
  signal?: AbortSignal;
};

async function securityMutation<T>(
  path: string,
  token: string,
  input: SecurityMutationInput,
  extraBody?: Record<string, unknown>,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      "Idempotency-Key": input.idempotencyKey,
    },
    body: JSON.stringify({
      reason: input.reason ?? "",
      request_id: input.requestId ?? "",
      trace_id: input.traceId ?? "",
      ...extraBody,
    }),
    signal: combinedSignal(input.signal),
  });
  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body.error) {
        message = body.error;
      }
    } catch {
      // response body wasn't JSON -- fall back to the status-only message
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

// --- overview, event-source health ------------------------------------------------------------

export async function getSecurityOverviewWithToken(token: string, signal?: AbortSignal): Promise<SecurityOverview | undefined> {
  return securityRead<SecurityOverview>("/api/v1/security/overview", token, signal);
}

export async function getSecurityEventSourcesWithToken(
  token: string,
  signal?: AbortSignal,
): Promise<SecurityEventSourcesResponse | undefined> {
  return securityRead<SecurityEventSourcesResponse>("/api/v1/security/events/sources", token, signal);
}

// --- Secure User-Level DP Operations, Observability, and Release
// Evidence slice (docs/secure-user-level-operations-audit.md) ------------

export async function getSecureUserDPStatusWithToken(
  token: string,
  signal?: AbortSignal,
): Promise<SecureUserDPCapability | undefined> {
  return securityRead<SecureUserDPCapability>("/api/v1/secure-aggregation/privacy/status", token, signal);
}

export async function getSecureUserDPHealthWithToken(
  token: string,
  signal?: AbortSignal,
): Promise<SecureUserDPHealth | undefined> {
  return securityRead<SecureUserDPHealth>("/api/v1/secure-aggregation/privacy/health", token, signal);
}

export async function getSecureUserDPBudgetWithToken(
  token: string,
  runId: string,
  signal?: AbortSignal,
): Promise<SecureUserDPBudget | undefined> {
  return securityRead<SecureUserDPBudget>(
    `/api/v1/secure-aggregation/privacy/budget?run_id=${encodeURIComponent(runId)}`,
    token,
    signal,
  );
}

export async function listSecureUserDPRoundsWithToken(
  token: string,
  options: { runId: string; afterCursor?: string; limit?: number },
  signal?: AbortSignal,
): Promise<SecureUserDPRoundsPage | undefined> {
  const params = new URLSearchParams({ run_id: options.runId });
  if (options.afterCursor) params.set("after_cursor", options.afterCursor);
  if (options.limit) params.set("limit", String(options.limit));
  return securityRead<SecureUserDPRoundsPage>(
    `/api/v1/secure-aggregation/privacy/rounds?${params.toString()}`,
    token,
    signal,
  );
}

export async function getSecureUserDPRoundWithToken(
  token: string,
  runId: string,
  roundId: number,
  signal?: AbortSignal,
): Promise<SecureUserDPRound | undefined> {
  return securityRead<SecureUserDPRound>(
    `/api/v1/secure-aggregation/privacy/rounds/${roundId}?run_id=${encodeURIComponent(runId)}`,
    token,
    signal,
  );
}

// --- worker identities and signing keys ---------------------------------------------------------

// Returns undefined (not []) when the coordinator/journal is
// unreachable, distinct from a genuinely empty registry -- callers that
// need to distinguish "nothing to show" from "couldn't load" (e.g. to
// render an unavailable banner instead of an empty table) rely on that
// distinction; callers that don't care can default with `?? []`.
export async function listSecurityWorkersWithToken(
  token: string,
  signal?: AbortSignal,
): Promise<SecurityWorker[] | undefined> {
  const payload = await securityRead<{ workers: SecurityWorker[] }>("/api/v1/security/workers", token, signal);
  return payload?.workers;
}

export async function getSecurityWorkerWithToken(
  token: string,
  workerId: string,
  signal?: AbortSignal,
): Promise<SecurityWorker | undefined> {
  return securityRead<SecurityWorker>(`/api/v1/security/workers/${workerId}`, token, signal);
}

export async function listWorkerSigningKeysWithToken(
  token: string,
  workerId: string,
  signal?: AbortSignal,
): Promise<WorkerSigningKey[] | undefined> {
  const payload = await securityRead<{ signing_keys: WorkerSigningKey[] }>(
    `/api/v1/security/workers/${workerId}/signing-keys`,
    token,
    signal,
  );
  return payload?.signing_keys;
}

export type WorkerLifecycleAction = "suspend" | "activate" | "revoke";

export async function mutateWorkerLifecycleWithToken(
  token: string,
  workerId: string,
  action: WorkerLifecycleAction,
  input: SecurityMutationInput,
): Promise<WorkerLifecycleResult> {
  return securityMutation<WorkerLifecycleResult>(`/api/v1/security/workers/${workerId}/${action}`, token, input);
}

export async function revokeWorkerSigningKeyWithToken(
  token: string,
  workerId: string,
  signingKeyId: string,
  input: SecurityMutationInput,
): Promise<WorkerSigningKeyRevocationResult> {
  return securityMutation<WorkerSigningKeyRevocationResult>(
    `/api/v1/security/workers/${workerId}/signing-keys/${signingKeyId}/revoke`,
    token,
    input,
  );
}

// --- coordinator signing keys --------------------------------------------------------------------

export async function listCoordinatorSigningKeysWithToken(
  token: string,
  signal?: AbortSignal,
): Promise<CoordinatorSigningKey[] | undefined> {
  const payload = await securityRead<{ signing_keys: CoordinatorSigningKey[] }>(
    "/api/v1/security/coordinator/signing-keys",
    token,
    signal,
  );
  return payload?.signing_keys;
}

export type RotateCoordinatorSigningKeyInput = SecurityMutationInput & {
  expectedCurrentSigningKeyId?: string;
  newKeyExpiresAtUnixS?: number;
  requestedGracePeriodSeconds?: number;
};

export async function rotateCoordinatorSigningKeyWithToken(
  token: string,
  input: RotateCoordinatorSigningKeyInput,
): Promise<RotateCoordinatorSigningKeyResult> {
  return securityMutation<RotateCoordinatorSigningKeyResult>(
    "/api/v1/security/coordinator/signing-keys/rotate",
    token,
    input,
    {
      expected_current_signing_key_id: input.expectedCurrentSigningKeyId ?? "",
      new_key_expires_at_unix_s: input.newKeyExpiresAtUnixS ?? 0,
      requested_grace_period_seconds: input.requestedGracePeriodSeconds ?? 0,
    },
  );
}

export type RevokeCoordinatorSigningKeyInput = SecurityMutationInput & {
  expectedStatus?: string;
};

export async function revokeCoordinatorSigningKeyWithToken(
  token: string,
  signingKeyId: string,
  input: RevokeCoordinatorSigningKeyInput,
): Promise<RevokeCoordinatorSigningKeyResult> {
  return securityMutation<RevokeCoordinatorSigningKeyResult>(
    `/api/v1/security/coordinator/signing-keys/${signingKeyId}/revoke`,
    token,
    input,
    { expected_status: input.expectedStatus ?? "" },
  );
}

// --- security events and audit -------------------------------------------------------------------

export type SecurityEventsQuery = {
  afterEventId?: string;
  limit?: number;
  minSeverity?: string;
  subjectType?: string;
  eventType?: string;
};

export async function listSecurityEventsWithToken(
  token: string,
  query: SecurityEventsQuery = {},
  signal?: AbortSignal,
): Promise<SecurityEventRecord[] | undefined> {
  const params = new URLSearchParams();
  if (query.afterEventId) params.set("after_event_id", query.afterEventId);
  if (query.limit) params.set("limit", String(query.limit));
  if (query.minSeverity) params.set("min_severity", query.minSeverity);
  if (query.subjectType) params.set("subject_type", query.subjectType);
  if (query.eventType) params.set("event_type", query.eventType);
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  const payload = await securityRead<{ events: SecurityEventRecord[] }>(`/api/v1/security/events${suffix}`, token, signal);
  return payload?.events;
}

export type SecurityAuditQuery = {
  cursor?: string;
  limit?: number;
  actor?: string;
  action?: string;
  resourceType?: string;
  outcome?: string;
  sinceUnixS?: number;
  untilUnixS?: number;
};

export async function listSecurityAuditWithToken(
  token: string,
  query: SecurityAuditQuery = {},
  signal?: AbortSignal,
): Promise<SecurityAuditResponse | undefined> {
  const params = new URLSearchParams();
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.limit) params.set("limit", String(query.limit));
  if (query.actor) params.set("actor", query.actor);
  if (query.action) params.set("action", query.action);
  if (query.resourceType) params.set("resource_type", query.resourceType);
  if (query.outcome) params.set("outcome", query.outcome);
  if (query.sinceUnixS) params.set("since", String(query.sinceUnixS));
  if (query.untilUnixS) params.set("until", String(query.untilUnixS));
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  return securityRead<SecurityAuditResponse>(`/api/v1/security/audit${suffix}`, token, signal);
}
