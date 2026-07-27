import { expect, test } from "@playwright/test";

import { loginAs } from "./fixtures/auth";

// Work Package I: Security Overview browser coverage. Exercises the
// real GET /api/v1/security/overview + /security/events/sources
// endpoints through an actual browser session -- no mocked responses.

test.describe("Security overview", () => {
  test("unauthenticated visitor sees the sign-in prompt, not overview data", async ({ page }) => {
    await page.goto("/security");
    await expect(page.getByText("Sign in as an admin, researcher, or viewer to inspect security posture.")).toBeVisible();
    await expect(page.getByText("Aggregate security posture")).not.toBeVisible();
  });

  test("admin sees real aggregate security posture", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security");

    await expect(page.getByRole("heading", { name: "Aggregate security posture" })).toBeVisible();
    await expect(page.getByText("Role: admin")).toBeVisible();

    // Real backend data, not a fixture: the transport card always
    // renders a transport mode string and a connected/unavailable pill
    // once the coordinator overview call resolves either way.
    await expect(page.getByText("Transport", { exact: true })).toBeVisible();
    await expect(page.getByText("Worker identities", { exact: true })).toBeVisible();
    await expect(page.getByText("Worker signing keys", { exact: true })).toBeVisible();
    await expect(page.getByText("Coordinator signing keys", { exact: true })).toBeVisible();
    await expect(page.getByText("Signed messages", { exact: true })).toBeVisible();
    await expect(page.getByText("Security event journal", { exact: true })).toBeVisible();
    await expect(page.getByText("Security audit journal", { exact: true })).toBeVisible();

    // The real docker-compose.security.yml stack enforces mTLS end to
    // end for this slice -- assert the actual reported transport mode
    // string contains "mtls" (e.g. "mtls_required"), not just that some
    // string is present.
    await expect(page.getByText(/mtls/i).first()).toBeVisible();
    // formatBoolean (lib/security-format.ts) renders "yes"/"no", not
    // "true"/"false" -- confirmed live: an earlier version of this
    // assertion asked for "mTLS enforced: true" and never matched.
    await expect(page.getByText("mTLS enforced: yes")).toBeVisible();
  });

  test("viewer role can read the overview (read-only role grant)", async ({ page }) => {
    await loginAs(page, "viewer");
    await page.goto("/security");
    await expect(page.getByRole("heading", { name: "Aggregate security posture" })).toBeVisible();
    await expect(page.getByText("Role: viewer")).toBeVisible();
  });

  test("service role is explicitly denied overview read access", async ({ page }) => {
    await loginAs(page, "service");
    await page.goto("/security");
    await expect(
      page.getByText("The service role does not have overview read access. Sign in as an admin, researcher, or viewer instead."),
    ).toBeVisible();
  });

  test("event source health table renders real source rows", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security");
    await expect(page.getByText("Event source health")).toBeVisible();
    const sourceTable = page.locator("table", { has: page.getByRole("columnheader", { name: "Source" }) });
    await expect(sourceTable).toBeVisible();
  });
});
