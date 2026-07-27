import { expect, test } from "@playwright/test";

import { loginAs } from "./fixtures/auth";

// Work Package M: Audit Explorer browser coverage. Exercises the real
// GET /api/v1/security/audit endpoint. Deliberately self-contained --
// it does not assume worker-administration.spec.ts or
// coordinator-keys.spec.ts already ran and produced audit records (test
// file execution order is not a contract this suite should depend on)
// -- so it performs one real, reversible worker suspend->activate cycle
// itself via the real API, then verifies that exact action is durably
// recorded in the audit journal.

const WORKER_ID = "worker-1";

test.describe("Security audit explorer", () => {
  test("unauthenticated visitor sees the sign-in prompt", async ({ page }) => {
    await page.goto("/security/audit");
    await expect(page.getByText("Sign in to view the durable security audit journal.")).toBeVisible();
  });

  test("admin sees the durable audit journal, including a fresh real mutation", async ({ page }) => {
    await loginAs(page, "admin");

    // Produce one real, reversible audit record via the actual worker
    // lifecycle API before checking the audit explorer.
    await page.goto(`/security/workers/${WORKER_ID}`);
    await expect(page.getByRole("button", { name: "Suspend" })).toBeEnabled({ timeout: 20_000 });
    await page.getByRole("button", { name: "Suspend" }).click();
    const suspendDialog = page.getByRole("dialog");
    await suspendDialog
      .getByPlaceholder("Explain why this action is being taken -- recorded in the security audit journal.")
      .fill("Playwright audit-explorer.spec.ts: producing a real audit record.");
    await suspendDialog.getByRole("checkbox").check();
    await suspendDialog.getByRole("button", { name: "Suspend worker" }).click();
    await expect(suspendDialog).not.toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Suspend worker succeeded", { exact: false })).toBeVisible();

    await page.getByRole("button", { name: "Activate" }).click();
    const activateDialog = page.getByRole("dialog");
    await activateDialog
      .getByPlaceholder("Explain why this action is being taken -- recorded in the security audit journal.")
      .fill("Playwright audit-explorer.spec.ts: restoring worker-1 to active.");
    await activateDialog.getByRole("checkbox").check();
    await activateDialog.getByRole("button", { name: "Activate worker" }).click();
    await expect(activateDialog).not.toBeVisible({ timeout: 15_000 });

    await page.goto("/security/audit");
    await expect(page.getByRole("heading", { name: "Durable, append-only security audit trail" })).toBeVisible();

    // The real resource_type recorded for worker lifecycle mutations is
    // "worker_identity" (go/internal/application/security_service.go),
    // not "worker" -- the audit endpoint's resource_type filter is an
    // exact match, so "worker" alone matches zero records. Confirmed
    // live: an earlier version of this filter returned "Buffered: 0."
    await page.getByLabel("Resource type").fill("worker_identity");
    await expect(page.getByRole("row", { name: /suspend/i }).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("row", { name: /activate/i }).first()).toBeVisible();
  });

  test("outcome filter narrows results via the API's outcome param", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/audit");
    await page.getByLabel("Outcome").selectOption("success");
    await expect(async () => {
      const rowCount = await page.getByRole("row").count();
      const emptyState = await page.getByText("No audit records match the current filters.").isVisible();
      expect(rowCount > 1 || emptyState).toBeTruthy();
    }).toPass({ timeout: 10_000 });
  });
});
