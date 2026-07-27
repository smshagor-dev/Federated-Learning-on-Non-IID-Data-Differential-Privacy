import { expect, test } from "@playwright/test";

import { loginAs } from "./fixtures/auth";

// Work Package L: Event Explorer browser coverage. Exercises the real
// GET /api/v1/security/events endpoint and its filter query params --
// no mocked event data.

test.describe("Security event explorer", () => {
  test("unauthenticated visitor sees the sign-in prompt", async ({ page }) => {
    await page.goto("/security/events");
    await expect(page.getByText("Sign in to view security events.")).toBeVisible();
  });

  test("admin sees a live-polled feed of real security events", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/events");
    await expect(page.getByRole("heading", { name: "Signed-message, privacy-record, and task security events" })).toBeVisible();

    // The WORKER_REGISTERED event this slice added to
    // GrpcCoordinatorClient.register_worker() fires every time
    // worker-1's container starts, so a real, non-empty event feed is
    // expected once the stack has been up for a few polling cycles.
    await expect(page.getByRole("row", { name: /WORKER_REGISTERED/ }).first()).toBeVisible({ timeout: 30_000 });
  });

  test("severity filter narrows the real result set via the API's min_severity param", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/events");
    await expect(page.getByRole("row", { name: /WORKER_REGISTERED/ }).first()).toBeVisible({ timeout: 30_000 });

    await page.getByLabel("Minimum severity").selectOption("CRITICAL");
    // A real, live re-poll happens on filter change (see the effect's
    // dependency array in security-events-console.tsx) -- assert the
    // table settles into either real CRITICAL-only rows or the real
    // empty-state message, never leftover lower-severity rows.
    //
    // The feed also keeps polling every 5s independently of this
    // filter change, so asserting row-by-row with a separate `await`
    // per row (as an earlier version of this test did) is racy: a poll
    // tick can land between iterations and change which row `nth(i)`
    // resolves to. Instead, wait for the table to settle, then read
    // every row's text in one synchronous snapshot (no `await` between
    // reading it and asserting on it) so the whole check is atomic
    // relative to the live polling.
    await expect(async () => {
      const rowCount = await page.getByRole("row").count();
      const emptyState = await page.getByText("No events match the current filters.").isVisible();
      expect(rowCount > 1 || emptyState).toBeTruthy();
    }).toPass({ timeout: 10_000 });
    const severityCells = await page.locator("tbody tr td:nth-child(2)").allTextContents();
    for (const text of severityCells) {
      expect(text).toMatch(/CRITICAL/);
    }
  });

  test("event type search input filters client-side against the buffered feed", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/events");
    await expect(page.getByRole("row", { name: /WORKER_REGISTERED/ }).first()).toBeVisible({ timeout: 30_000 });

    await page.getByLabel("Search").fill("WORKER_REGISTERED");
    await expect(page.getByRole("row", { name: /WORKER_REGISTERED/ }).first()).toBeVisible();
    // Same atomic-snapshot reasoning as the severity-filter test above:
    // read every row's text once, then assert synchronously, rather
    // than awaiting a fresh locator resolution per row while the feed
    // keeps polling in the background.
    const rowTexts = await page.locator("tbody tr").allTextContents();
    for (const text of rowTexts) {
      expect(text).toContain("WORKER_REGISTERED");
    }
  });
});
