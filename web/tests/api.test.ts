import { afterEach, describe, expect, it, vi } from "vitest";

import { getLiveRunData, loginWithPassword } from "@/lib/api";

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
});
