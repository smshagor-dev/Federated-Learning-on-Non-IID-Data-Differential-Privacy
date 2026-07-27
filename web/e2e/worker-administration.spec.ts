import { expect, test, type Page } from "@playwright/test";

import { loginAs } from "./fixtures/auth";

// Work Package J: Worker Administration browser coverage. Exercises the
// real GET/POST /api/v1/security/workers* endpoints -- no mocked
// mutation responses. "worker-1" is the real, live python-worker
// registered by docker-compose.security.yml's mTLS override (see
// python/src/fl_platform/worker/configuration.py's WorkerConfig.worker_id
// default), so this suite requires that container to have completed at
// least one RegisterWorker call before it runs.

const WORKER_ID = "worker-1";

async function confirmPendingDialog(page: Page, confirmLabel: string, reason: string): Promise<void> {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByPlaceholder("Explain why this action is being taken -- recorded in the security audit journal.").fill(reason);
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: confirmLabel }).click();
  await expect(dialog).not.toBeVisible({ timeout: 15_000 });
}

test.describe("Worker administration", () => {
  test("admin sees the registered worker-1 in the worker list", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/workers");
    await expect(page.getByRole("heading", { name: "Registered worker certificates and signing keys" })).toBeVisible();
    await expect(page.getByRole("row", { name: new RegExp(WORKER_ID) })).toBeVisible({ timeout: 20_000 });
  });

  test("admin can open worker-1's detail page from the list", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/workers");
    await page.getByRole("row", { name: new RegExp(WORKER_ID) }).getByRole("link", { name: "View" }).click();
    await expect(page).toHaveURL(new RegExp(`/security/workers/${WORKER_ID}$`));
    // exact: true -- the AppShell also renders an h1 "Worker worker-1"
    // page title, which otherwise makes this a strict-mode-violating
    // ambiguous match against the console's own h2 "worker-1" heading.
    await expect(page.getByRole("heading", { name: WORKER_ID, exact: true })).toBeVisible();
  });

  test("viewer cannot see worker lifecycle mutation controls", async ({ page }) => {
    await loginAs(page, "viewer");
    await page.goto(`/security/workers/${WORKER_ID}`);
    // exact: true -- the AppShell also renders an h1 "Worker worker-1"
    // page title, which otherwise makes this a strict-mode-violating
    // ambiguous match against the console's own h2 "worker-1" heading.
    await expect(page.getByRole("heading", { name: WORKER_ID, exact: true })).toBeVisible();
    await expect(page.getByText("Sign in as an admin to suspend, activate, or revoke this worker.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Suspend" })).toHaveCount(0);
  });

  // Reversible by design (see the security-validation harness's
  // groups/worker_identity.py, same constraint): this shared Docker
  // stack is also used by every other scenario group in this harness
  // run, so a destructive/terminal action here (Revoke) is never
  // exercised live in this suite -- only real suspend->activate, which
  // nets worker-1 back to its starting "active" state and does not
  // affect any later scenario that depends on worker-1 being usable.
  test("admin can suspend then reactivate worker-1 through the real API", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto(`/security/workers/${WORKER_ID}`);
    // exact: true -- the AppShell also renders an h1 "Worker worker-1"
    // page title, which otherwise makes this a strict-mode-violating
    // ambiguous match against the console's own h2 "worker-1" heading.
    await expect(page.getByRole("heading", { name: WORKER_ID, exact: true })).toBeVisible();

    await expect(page.getByRole("button", { name: "Suspend" })).toBeEnabled({ timeout: 20_000 });
    await page.getByRole("button", { name: "Suspend" }).click();
    await confirmPendingDialog(page, "Suspend worker", "Playwright worker-administration.spec.ts: reversible suspend/activate check.");
    await expect(page.getByText("Suspend worker succeeded", { exact: false })).toBeVisible();
    await expect(page.getByText("suspended", { exact: false }).first()).toBeVisible();

    await page.getByRole("button", { name: "Activate" }).click();
    await confirmPendingDialog(page, "Activate worker", "Playwright worker-administration.spec.ts: restoring worker-1 to active.");
    await expect(page.getByText("Activate worker succeeded", { exact: false })).toBeVisible();

    // Net effect: worker-1 ends this test in the same "active" state it
    // started in, so later scenario groups in the same harness run are
    // unaffected.
    await expect(page.getByRole("button", { name: "Suspend" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Activate" })).toBeDisabled();
  });
});
