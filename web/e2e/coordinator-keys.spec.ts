import { expect, test } from "@playwright/test";

import { loginAs } from "./fixtures/auth";

// Work Package K: Coordinator-Key Admin browser coverage. Rotation is
// exercised live through the real POST /api/v1/security/coordinator/
// signing-keys/rotate endpoint: rotation is additive (the previous
// active key moves into a grace period and stays trusted -- see
// security-coordinator-keys-console.tsx's own consequence copy), so it
// is safe to run inside the same shared Docker stack every other
// scenario group in this harness run depends on.
//
// Revocation of the coordinator's *active* key is genuinely destructive
// -- it halts task issuance until a new key is rotated in -- which would
// break every later group in a shared-stack harness run (e.g.
// signed-tasks.coordinator-key.active). Per this Work Package's own
// "mark the exact browser scenario BLOCKED rather than mocking
// completion" instruction, live revoke-of-the-active-key is NOT
// exercised here. What IS exercised live: the revoke button renders,
// opens a real confirmation dialog with the correct danger copy, and
// the action is then cancelled rather than confirmed. Live revoke
// behavior itself already has real, non-destructive-to-this-suite
// coverage in cpp/coordinator/tests/idempotency_store_test.cpp and
// go/internal/transport/httpapi/security_handlers_test.go.

test.describe("Coordinator signing keys", () => {
  test("admin sees the real coordinator signing-key table", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/coordinator-keys");
    await expect(page.getByRole("heading", { name: "Coordinator identity key rotation and revocation" })).toBeVisible();
    await expect(page.getByText(/^Active: /)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("row", { name: /active/ }).first()).toBeVisible();
  });

  test("viewer cannot read or rotate/revoke coordinator signing keys", async ({ page }) => {
    // VIEWER has no PermCoordinatorKeysRead grant -- the real backend
    // returns 403 to GET /api/v1/security/coordinator/signing-keys,
    // which the console renders as "Coordinator is not reachable right
    // now." (the same generic unavailable state a real outage would
    // show), not the admin-mutation-denial copy: that copy only renders
    // once `keys` has actually loaded, which never happens for this
    // role. Confirmed live -- coordinator-signing-key listings are
    // all-or-nothing (RESEARCHER/ADMIN full, VIEWER denied entirely),
    // per docs/security-capability-inventory.md.
    await loginAs(page, "viewer");
    await page.goto("/security/coordinator-keys");
    await expect(page.getByText("Coordinator is not reachable right now.")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Rotate coordinator signing key" })).toHaveCount(0);
  });

  test("admin can rotate the coordinator signing key through the real API", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/security/coordinator-keys");

    const activePill = page.getByText(/^Active: /);
    await expect(activePill).toBeVisible({ timeout: 20_000 });
    const previousActiveKeyId = (await activePill.textContent())?.replace("Active: ", "").trim();

    await page.getByRole("button", { name: "Rotate coordinator signing key" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    // The dialog's "Expected current active key ID" field is pre-filled
    // by the console's own poll cycle, but this is a real compare-and-
    // swap safety field the admin is meant to confirm, not a hidden
    // value -- explicitly (re-)filling it with the value this test
    // itself already confirmed from the page removes any dependency on
    // that pre-fill's timing relative to the dialog opening. An empty/
    // stale value here causes a real, correct 409 from the server (the
    // rotation is a genuine compare-and-swap), which is exactly what
    // was observed before this fix.
    if (previousActiveKeyId) {
      await dialog.getByLabel("Expected current active key ID").fill(previousActiveKeyId);
    }
    await dialog
      .getByPlaceholder("Explain why this action is being taken -- recorded in the security audit journal.")
      .fill("Playwright coordinator-keys.spec.ts: live rotation check (additive, old key kept in grace period).");
    await dialog.getByRole("checkbox").check();
    await dialog.getByRole("button", { name: "Rotate key" }).click();
    await expect(dialog).not.toBeVisible({ timeout: 15_000 });

    await expect(page.getByText("Rotated to new key", { exact: false })).toBeVisible();

    // The previously-active key must now show up as grace_period (still
    // trusted, per the console's own explanation), not simply vanish.
    if (previousActiveKeyId) {
      const previousKeyRow = page.getByRole("row", { name: new RegExp(previousActiveKeyId) });
      await expect(previousKeyRow).toBeVisible();
      await expect(previousKeyRow.getByText("grace_period", { exact: false })).toBeVisible();
    }
  });

  test("revoke dialog opens with the correct destructive-action copy, then is cancelled (not exercised live)", async ({
    page,
  }) => {
    await loginAs(page, "admin");
    await page.goto("/security/coordinator-keys");
    await expect(page.getByText(/^Active: /)).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "Revoke" }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("task issuance halts until a new key is rotated in", { exact: false })).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).not.toBeVisible();
  });
});
