import { getDemoOverview, getDemoRunDashboard } from "@/lib/demo-data";
import type {
  AlgorithmDescriptor,
  AlgorithmSummary,
  AuditEvent,
  AuthSession,
  CoordinatorHealth,
  CoordinatorRunSnapshot,
  DatasetEntry,
  Experiment,
  ModelEntry,
  OverviewData,
  PartitionEntry,
  PersonalizationMetrics,
  PersonalizationRecord,
  PrivacyLedger,
  PrivacyMetricsSnapshot,
  PrivacyProjection,
  Project,
  Run,
  RunAction,
  RunDashboardData,
} from "@/types/api";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_FL_API_BASE_URL ?? process.env.FL_API_BASE_URL ?? "http://127.0.0.1:8080";

// Requests to the Go API must never hang indefinitely: callers of
// getOverviewData()/getRunData() catch failures and fall back to demo
// data, but that fallback only helps if a dead/unreachable backend fails
// *fast*. Without an explicit timeout, an environment where the backend
// connection stalls rather than rejects immediately (observed inside a
// Docker build with no `api` container running) hangs the entire request
// — and during `next build`'s static generation, that hangs the build.
const REQUEST_TIMEOUT_MS = 3_000;

async function requestJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    next: { revalidate: 5 },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

async function requestMutableJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function getOverviewData(): Promise<OverviewData> {
  try {
    return await requestJSON<OverviewData>("/api/v1/dashboard/overview");
  } catch {
    return getDemoOverview();
  }
}

export async function getRunData(runId: string): Promise<RunDashboardData | undefined> {
  try {
    return await getLiveRunData(runId);
  } catch {
    return getDemoRunDashboard(runId);
  }
}

export async function getLiveRunData(runId: string): Promise<RunDashboardData | undefined> {
  return await requestJSON<RunDashboardData>(`/api/v1/dashboard/runs/${runId}`);
}

export async function loginWithPassword(email: string, password: string): Promise<AuthSession> {
  const response = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error(response.status === 401 ? "Invalid credentials" : `Login failed: ${response.status}`);
  }
  return (await response.json()) as AuthSession;
}

export async function mutateRunLifecycle(runId: string, action: RunAction, token: string): Promise<void> {
  await requestMutableJSON(`/api/v1/runs/${runId}/${action}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
}

export async function listProjectsWithToken(token: string): Promise<Project[]> {
  return await requestMutableJSON<Project[]>("/api/v1/projects", {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
}

export async function createProjectWithToken(
  token: string,
  payload: { name: string; description: string },
): Promise<Project> {
  return await requestMutableJSON<Project>("/api/v1/projects", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
}

export async function createExperimentWithToken(
  token: string,
  payload: { project_id: string; name: string; description: string; config: Record<string, unknown> },
): Promise<Experiment> {
  return await requestMutableJSON<Experiment>("/api/v1/experiments", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
}

export async function createRunWithToken(
  token: string,
  payload: { experiment_id: string; config: Record<string, unknown> },
): Promise<Run> {
  return await requestMutableJSON<Run>("/api/v1/runs", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
}

// The Go API collapses "no coordinator client configured" and
// "coordinator process unreachable" into the same 503 (see
// writeCoordinatorError in go/internal/transport/httpapi/coordinator_handlers.go)
// since an operator can't act differently on either — both mean "no live
// coordinator to talk to right now."
export type CoordinatorAvailability = "connected" | "unavailable" | "unauthorized" | "unknown";

// Distinguishes "coordinator client configured but the coordinator
// process is unreachable" (503 from writeCoordinatorError in
// go/internal/transport/httpapi/coordinator_handlers.go) from "auth
// failed" and from a genuinely unexpected error, so the operator console
// can show an accurate status instead of a generic failure banner.
export async function getCoordinatorHealth(
  token: string,
): Promise<{ availability: CoordinatorAvailability; health?: CoordinatorHealth }> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/system/coordinator-health`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (response.status === 401 || response.status === 403) {
      return { availability: "unauthorized" };
    }
    if (response.status === 503) {
      return { availability: "unavailable" };
    }
    if (!response.ok) {
      return { availability: "unknown" };
    }
    const health = (await response.json()) as CoordinatorHealth;
    return { availability: "connected", health };
  } catch {
    return { availability: "unknown" };
  }
}

export async function getCoordinatorRun(runId: string, token: string): Promise<CoordinatorRunSnapshot | undefined> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/coordinator/runs/${runId}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as CoordinatorRunSnapshot;
  } catch {
    return undefined;
  }
}

export async function listAuditEventsWithToken(token: string, limit = 100): Promise<AuditEvent[]> {
  return await requestMutableJSON<AuditEvent[]>(`/api/v1/audit/events?limit=${limit}`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
}

// --- the Algorithm Expansion phase: algorithm metadata --------------------------------------

export async function listAlgorithmsWithToken(token: string): Promise<AlgorithmDescriptor[]> {
  return await requestMutableJSON<AlgorithmDescriptor[]>("/api/v1/algorithms", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- the Algorithm Expansion phase: model registry ------------------------------------------

export async function listModelsWithToken(token: string): Promise<ModelEntry[]> {
  return await requestMutableJSON<ModelEntry[]>("/api/v1/models", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function registerModelWithToken(token: string, model: Partial<ModelEntry>): Promise<ModelEntry> {
  return await requestMutableJSON<ModelEntry>("/api/v1/models", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(model),
  });
}

export async function transitionModelWithToken(
  token: string,
  name: string,
  version: string,
  action: "validate" | "activate" | "deprecate" | "archive",
  body?: Record<string, unknown>,
): Promise<ModelEntry> {
  return await requestMutableJSON<ModelEntry>(`/api/v1/models/${name}/${version}/${action}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(body ?? {}),
  });
}

// --- the Algorithm Expansion phase: dataset registry -----------------------------------------

export async function listDatasetsWithToken(token: string): Promise<DatasetEntry[]> {
  return await requestMutableJSON<DatasetEntry[]>("/api/v1/datasets", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function registerDatasetWithToken(token: string, dataset: Partial<DatasetEntry>): Promise<DatasetEntry> {
  return await requestMutableJSON<DatasetEntry>("/api/v1/datasets", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(dataset),
  });
}

export async function transitionDatasetWithToken(
  token: string,
  datasetId: string,
  action: "validate" | "activate" | "deprecate",
): Promise<DatasetEntry> {
  return await requestMutableJSON<DatasetEntry>(`/api/v1/datasets/${datasetId}/${action}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function listPartitionsWithToken(token: string, datasetId: string): Promise<PartitionEntry[]> {
  return await requestMutableJSON<PartitionEntry[]>(`/api/v1/datasets/${datasetId}/partitions`, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function createPartitionWithToken(
  token: string,
  datasetId: string,
  partition: Partial<PartitionEntry>,
): Promise<PartitionEntry> {
  return await requestMutableJSON<PartitionEntry>(`/api/v1/datasets/${datasetId}/partitions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(partition),
  });
}

// --- the Algorithm Expansion phase: personalization / fairness -------------------------------

// These three calls return undefined (rather than throwing) for the
// common "coordinator not configured" / "coordinator unreachable" (503)
// case, so callers can render an explicit empty/unavailable state instead
// of a generic error banner — mirroring getCoordinatorRun's contract.
export async function getPersonalizationRecordsWithToken(
  token: string,
  runId: string,
): Promise<PersonalizationRecord[] | undefined> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/coordinator/runs/${runId}/personalization`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) {
      return undefined;
    }
    const payload = (await response.json()) as { records: PersonalizationRecord[] };
    return payload.records;
  } catch {
    return undefined;
  }
}

export async function getFairnessWithToken(token: string, runId: string): Promise<PersonalizationMetrics | undefined> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/coordinator/runs/${runId}/fairness`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as PersonalizationMetrics;
  } catch {
    return undefined;
  }
}

// Privacy Engineering phase: the three read-only privacy endpoints
// (docs/privacy-ledger.md). Same undefined-on-503/network-failure
// contract as getFairnessWithToken above — the Privacy Center panel
// renders an explicit "coordinator unreachable" state, not a generic
// error banner, when these return undefined.
export async function getPrivacyMetricsWithToken(
  token: string,
  runId: string,
): Promise<PrivacyMetricsSnapshot | undefined> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/coordinator/runs/${runId}/privacy/metrics`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as PrivacyMetricsSnapshot;
  } catch {
    return undefined;
  }
}

export async function getPrivacyLedgerWithToken(token: string, runId: string): Promise<PrivacyLedger | undefined> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/coordinator/runs/${runId}/privacy/ledger`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as PrivacyLedger;
  } catch {
    return undefined;
  }
}

export async function getPrivacyProjectionWithToken(
  token: string,
  runId: string,
): Promise<PrivacyProjection | undefined> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/coordinator/runs/${runId}/privacy/projection`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as PrivacyProjection;
  } catch {
    return undefined;
  }
}

export async function getAlgorithmSummaryWithToken(token: string, runId: string): Promise<AlgorithmSummary | undefined> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/coordinator/runs/${runId}/algorithm-summary`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) {
      return undefined;
    }
    return (await response.json()) as AlgorithmSummary;
  } catch {
    return undefined;
  }
}
