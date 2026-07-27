import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // Vitest's default include glob (**/*.{test,spec}.*) otherwise also
    // picks up web/e2e/*.spec.ts, which use Playwright's own test/describe
    // (not Vitest's) -- collecting them here fails with "Playwright Test
    // did not expect test.describe() to be called here." Excluded
    // alongside Vitest's own default excludes (node_modules etc, which
    // this array replaces and must therefore restate).
    exclude: ["**/node_modules/**", "**/dist/**", "**/.next/**", "e2e/**"],
  },
});
