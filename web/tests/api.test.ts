import { afterEach, describe, expect, it, vi } from "vitest";

import { getCoordinatorHealth, getCoordinatorRun, getLiveRunData, loginWithPassword } from "@/lib/api";

describe("API helpers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts credentials to the login endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          token: "token-1",
          user: { id: "u1", email: "researcher@example.com", role: "RESEARCHER" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const session = await loginWithPassword("researcher@example.com", "secret");

    expect(session.token).toBe("token-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8080/api/v1/auth/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          email: "researcher@example.com",
          password: "secret",
        }),
      }),
    );
  });

  it("reads live run dashboard data", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          run: { id: "run-1" },
          metrics: { current_round: 2 },
          audit_events: [],
          signals: [],
          source: "live",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const data = await getLiveRunData("run-1");

    expect(data?.source).toBe("live");
    expect(data?.metrics.current_round).toBe(2);
  });

  it("reports coordinator health as connected on 200", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await getCoordinatorHealth("token-1");

    expect(result.availability).toBe("connected");
    expect(result.health?.status).toBe("ok");
  });

  it("reports coordinator health as unavailable on 503", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 503 }));

    const result = await getCoordinatorHealth("token-1");

    expect(result.availability).toBe("unavailable");
  });

  it("reports coordinator health as unauthorized on 401", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 401 }));

    const result = await getCoordinatorHealth("token-1");

    expect(result.availability).toBe("unauthorized");
  });

  it("returns undefined for a coordinator run that does not exist", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 404 }));

    const result = await getCoordinatorRun("missing-run", "token-1");

    expect(result).toBeUndefined();
  });

  it("reads a coordinator run snapshot", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          run_id: "run-1",
          state: "RUNNING",
          current_round: 2,
          max_rounds: 5,
          model_version: "v2",
          algorithm: "fedavg",
          registered_workers: 3,
          healthy_workers: 3,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const snapshot = await getCoordinatorRun("run-1", "token-1");

    expect(snapshot?.state).toBe("RUNNING");
    expect(snapshot?.current_round).toBe(2);
  });
});
