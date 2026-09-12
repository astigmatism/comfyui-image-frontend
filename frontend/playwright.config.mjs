import { defineConfig, devices } from "@playwright/test";

const baseURL = `http://127.0.0.1:${process.env.CIF_E2E_PORT || "8765"}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
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
