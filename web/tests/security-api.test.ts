import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getSecurityOverviewWithToken,
  listSecurityWorkersWithToken,
  mutateWorkerLifecycleWithToken,
  rotateCoordinatorSigningKeyWithToken,
} from "@/lib/security-api";

// Web Security Center, Event Centralization, and Security CI slice:
// coverage for lib/security-api.ts, matching tests/privacy-api.test.ts's
// mocked-fetch convention. Two behaviors specific to this module (not
// present in lib/api.ts) get dedicated coverage: the undefined-vs-empty-
// array distinction for list reads, and the Idempotency-Key header/
// throw-on-failure contract for mutations.
describe("Security Center API helpers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads the security overview", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          transport: { transport_mode: "mtls_required", mutual_tls_enforced: true, coordinator_available: true },
          worker_identities: { active: 2, suspended: 0, revoked: 0, expired: 0, certificate_expiry_warnings: 0 },
          feature_availability: {
            secure_aggregation_available: false,
            worker_attestation_available: false,
            verifiable_client_clipping_available: false,
            byzantine_robustness_available: false,
            central_coordinator_observes_updates: true,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const overview = await getSecurityOverviewWithToken("token-1");
    expect(overview?.transport.transport_mode).toBe("mtls_required");
    expect(overview?.worker_identities.active).toBe(2);
    expect(overview?.feature_availability.secure_aggregation_available).toBe(false);
  });

  it("returns undefined for the overview when the coordinator is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 503 }));
    const overview = await getSecurityOverviewWithToken("token-1");
    expect(overview).toBeUndefined();
  });

  it("distinguishes an unreachable worker list (undefined) from a genuinely empty one ([])", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("", { status: 503 }));
    const unreachable = await listSecurityWorkersWithToken("token-1");
    expect(unreachable).toBeUndefined();

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ workers: [] }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const empty = await listSecurityWorkersWithToken("token-1");
    expect(empty).toEqual([]);
  });

  it("sends the Idempotency-Key header on a worker lifecycle mutation", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ identity: { worker_id: "worker-1", registration_status: "suspended" }, changed: true, leases_canceled: 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const result = await mutateWorkerLifecycleWithToken("token-1", "worker-1", "suspend", {
      reason: "investigation",
      idempotencyKey: "idem-1",
    });
    expect(result.changed).toBe(true);
    expect(result.identity.registration_status).toBe("suspended");

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("idem-1");
    expect(JSON.parse(init.body as string)).toMatchObject({ reason: "investigation" });
  });

  it("throws with the server-provided error message on a failed mutation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "forbidden: missing permission security.workers.suspend" }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(
      mutateWorkerLifecycleWithToken("token-1", "worker-1", "suspend", {
        reason: "x",
        idempotencyKey: "idem-2",
      }),
    ).rejects.toThrow("forbidden: missing permission security.workers.suspend");
  });

  it("aborts the request when the caller's AbortSignal fires", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      const signal = (init as RequestInit | undefined)?.signal;
      return new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      });
    });
    const controller = new AbortController();
    const promise = getSecurityOverviewWithToken("token-1", controller.signal);
    controller.abort();
    // securityRead swallows all failures (including AbortError) and
    // resolves to undefined, matching the undefined-on-failure contract
    // every other read function in this module follows.
    await expect(promise).resolves.toBeUndefined();
  });

  it("sends rotation-specific fields for coordinator signing-key rotation", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          accepted: true,
          new_key: { signing_key_id: "key-2", status: "active" },
          previous_key: { signing_key_id: "key-1", status: "grace_period" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const result = await rotateCoordinatorSigningKeyWithToken("token-1", {
      reason: "scheduled rotation",
      idempotencyKey: "idem-3",
      expectedCurrentSigningKeyId: "key-1",
      newKeyExpiresAtUnixS: 1_800_000_000,
      requestedGracePeriodSeconds: 3600,
    });
    expect(result.accepted).toBe(true);
    expect(result.new_key.signing_key_id).toBe("key-2");

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toMatchObject({
      expected_current_signing_key_id: "key-1",
      new_key_expires_at_unix_s: 1_800_000_000,
      requested_grace_period_seconds: 3600,
    });
  });
});
