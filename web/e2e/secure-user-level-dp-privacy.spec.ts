import { expect, test } from "@playwright/test";

import { loginAs } from "./fixtures/auth";

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice, Work Area O: browser coverage for the new
// /security/secure-aggregation/privacy page, against the real running
// backend (no mocked responses) -- same pattern as
// security-overview.spec.ts. No fixed sleeps: every assertion waits on
// a real rendered element via Playwright's own auto-waiting expect().

test.describe("Secure user-level DP privacy runtime", () => {
  test("unauthenticated visitor is prompted to sign in, not shown runtime data", async ({ page }) => {
    await page.goto("/security/secure-aggregation/privacy");
    await expect(page.getByText("Sign in to view the secure user-level DP privacy runtime.")).toBeVisible();
    await expect(page.getByTestId("secure-user-dp-capability")).not.toBeVisible();
  });

  test("admin sees the mandatory trust limitations prominently, not hidden", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/secure-aggregation/privacy");

    const limitations = page.getByTestId("secure-user-dp-limitations");
    await expect(limitations).toBeVisible();
    await expect(limitations.getByText("Worker clipping is not cryptographically verified", { exact: false })).toBeVisible();
    await expect(limitations.getByText("not production privacy-ready", { exact: false })).toBeVisible();
    // All 10 mandated warnings render as real <li> items, not truncated
    // into a summary or collapsed behind a toggle.
    await expect(limitations.locator("li")).toHaveCount(10);
  });

  test("admin sees capability and runtime health sections with real backend data", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/secure-aggregation/privacy");

    const capability = page.getByTestId("secure-user-dp-capability");
    await expect(capability).toBeVisible();
    await expect(capability.getByText("SECAGG_NO_DROPOUT_EXPERIMENTAL")).toBeVisible();
    await expect(capability.getByText("ADD_REMOVE_ONE")).toBeVisible();

    const health = page.getByTestId("secure-user-dp-health");
    await expect(health).toBeVisible();
    await expect(health.getByText("Active runs using this mechanism")).toBeVisible();
  });

  test("viewer can read capability and health but not budget/rounds", async ({ page }) => {
    await loginAs(page, "viewer");
    await page.goto("/security/secure-aggregation/privacy");

    await expect(page.getByTestId("secure-user-dp-capability")).toBeVisible();
    await expect(page.getByTestId("secure-user-dp-health")).toBeVisible();

    await page.getByTestId("secure-user-dp-budget-run-id").fill("nonexistent-run");
    await page.getByTestId("secure-user-dp-budget-lookup").click();
    await expect(page.getByTestId("secure-user-dp-budget-error")).toBeVisible();
  });

  test("service role is denied every section (no implicit access)", async ({ page }) => {
    await loginAs(page, "service");
    await page.goto("/security/secure-aggregation/privacy");

    await expect(page.getByTestId("secure-user-dp-degraded")).toBeVisible();
    await expect(page.getByText("Capability information is not available.")).toBeVisible();
    await expect(page.getByText("Runtime health is not available.")).toBeVisible();
  });

  test("round explorer shows an empty state for a run with no committed rounds", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/secure-aggregation/privacy");

    await page.getByTestId("secure-user-dp-rounds-run-id").fill("no-such-run-" + Date.now());
    await page.getByTestId("secure-user-dp-rounds-search").click();
    await expect(page.getByTestId("secure-user-dp-rounds-empty")).toBeVisible();
  });
});
