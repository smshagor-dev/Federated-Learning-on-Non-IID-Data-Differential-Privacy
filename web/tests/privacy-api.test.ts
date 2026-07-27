import { afterEach, describe, expect, it, vi } from "vitest";

import { getPrivacyLedgerWithToken, getPrivacyMetricsWithToken, getPrivacyProjectionWithToken } from "@/lib/api";

describe("Privacy Engineering phase API helpers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads privacy metrics with separate per-mechanism epsilon/delta", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          run_id: "run-1",
          round_id: 3,
          has_sample_level: true,
          sample_epsilon: 1.5,
          sample_delta: 1e-6,
          has_user_level: true,
          user_epsilon: 4.2,
          user_delta: 1e-5,
          has_clipping: false,
          clipping_epsilon: 0,
          clipping_delta: 0,
          current_clip_value: 1.0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const metrics = await getPrivacyMetricsWithToken("token-1", "run-1");
    expect(metrics?.sample_epsilon).toBe(1.5);
    expect(metrics?.user_epsilon).toBe(4.2);
    // Critical Privacy Rule regression guard: these must be independent
    // fields, never combined into one.
    expect(metrics?.sample_epsilon).not.toBe(metrics?.user_epsilon);
  });

  it("returns undefined for privacy metrics when coordinator is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 503 }));
    const metrics = await getPrivacyMetricsWithToken("token-1", "run-1");
    expect(metrics).toBeUndefined();
  });

  it("reads the privacy ledger with all three mechanisms independently", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          sample_level_entries: [{ run_id: "run-1", round_id: 1, client_id: "client-a", epsilon: 1.1 }],
          user_level_entries: [{ run_id: "run-1", round_id: 1, epsilon: 2.2, num_clients: 2 }],
          clipping_entries: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const ledger = await getPrivacyLedgerWithToken("token-1", "run-1");
    expect(ledger?.sample_level_entries).toHaveLength(1);
    expect(ledger?.user_level_entries).toHaveLength(1);
    expect(ledger?.clipping_entries).toHaveLength(0);
  });

  it("reads a privacy projection with an unbounded (absent) budget", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          has_user_level: true,
          user_current_epsilon: 2.0,
          user_projected_next_epsilon: 2.5,
          // user_budget_remaining intentionally omitted: unset budget.
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const projection = await getPrivacyProjectionWithToken("token-1", "run-1");
    expect(projection?.user_projected_next_epsilon).toBe(2.5);
    expect(projection?.user_budget_remaining).toBeUndefined();
  });

  it("returns undefined for privacy projection when coordinator is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 503 }));
    const projection = await getPrivacyProjectionWithToken("token-1", "run-1");
    expect(projection).toBeUndefined();
  });
});
