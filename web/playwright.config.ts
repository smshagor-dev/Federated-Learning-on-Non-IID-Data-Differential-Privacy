import { defineConfig, devices } from "@playwright/test";

// Web Security Center browser test suite -- Security Runtime Completion
// and Release Evidence slice, Work Package G. Deliberately does NOT
// define `webServer`: the required web+API (+coordinator+python-worker)
// services are brought up by the security-validation harness
// (scripts/security-validation/groups/security_ui.py, which shells out
// to `npx playwright test` against an already-running Docker Compose
// stack -- see docs/security-browser-testing.md), not by Playwright
// itself. Running `npx playwright test` directly against a manually
// started `npm run dev` + a manually started backend also works, since
// PLAYWRIGHT_BASE_URL defaults to the same http://localhost:3000 the
// Compose `web` service publishes.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"], ["json", { outputFile: "e2e-results/results.json" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    // Screenshots only on failure -- never on every step -- and traces
    // only kept for a failing test's first retry, matching Work Package
    // G's "capture screenshots only on failure" / "sanitize traces and
    // artifacts" requirements. Neither a screenshot nor a trace of this
    // application's Security Center pages can contain a private key or
    // raw signed payload (the UI never renders one -- see
    // web/lib/security-api.ts's contract), so no additional redaction
    // step is needed before these artifacts leave the harness.
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  outputDir: "e2e-results/artifacts",
});
