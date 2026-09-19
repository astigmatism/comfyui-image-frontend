import { defineConfig, devices } from "@playwright/test";

const baseURL = `http://127.0.0.1:${process.env.CIF_E2E_PORT || "8765"}`;

export default defineConfig({
  testDir: "./e2e",
  // The TLS-edge regression spec is its own Playwright project
  // (playwright.tls.config.mjs) that runs against a real TLS origin; the loopback
  // suite must not also run it here against the plain-HTTP webServer.
  testIgnore: "**/tls-edge.spec.mjs",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    extraHTTPHeaders: { "X-CIF-Generation-Protocol": "2" },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: {
    command: "node scripts/build.mjs && PYTHONPATH=../backend python3 ../backend/tests/e2e_server.py",
    url: `${baseURL}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
