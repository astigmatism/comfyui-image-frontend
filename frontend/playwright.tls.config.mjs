// Playwright config for the TLS-edge regression project.
//
// Unlike playwright.config.mjs (which runs the loopback in-process suite), this
// config runs tls-edge.spec.mjs against the REAL TLS edge on a non-loopback-ish
// origin (https://<CIF_TLS_HOSTNAME>:<port>):
//
//   - docker mode: the committed compose stack (compose.example.yml +
//     frontend/e2e/tls-edge.overlay.yml) with the real, unprivileged
//     cif-tls-edge service and the committed Caddyfile in-container.
//   - local mode: the committed Caddyfile (cert paths re-pointed, loopback bind
//     inserted) via a local caddy binary, in front of the in-process app.
//
// The edge is started by e2e/tls-global-setup.mjs (which calls
// scripts/e2e-tls-stack.sh up) and stopped by e2e/tls-global-teardown.mjs.
//
// The hostname and port are chosen by the caller (see the e2e-tls Make target
// and scripts/e2e-tls-stack.sh) and must match what the stack binds.
import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const hostname = process.env.CIF_TLS_HOSTNAME || "image-studio.lan";
const port = process.env.CIF_E2E_TLS_HOST_PORT || "8443";

export default defineConfig({
  testDir: "./e2e",
  // Only the TLS-edge spec runs under this config; the large loopback suite is
  // untouched and stays under playwright.config.mjs.
  testMatch: "**/tls-edge.spec.mjs",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  globalSetup: path.join(__dirname, "e2e", "tls-global-setup.mjs"),
  globalTeardown: path.join(__dirname, "e2e", "tls-global-teardown.mjs"),
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `https://${hostname}:${port}`,
    // The leaf is signed by a locally issued root that is NOT in the OS trust
    // store. Playwright's bundled Chromium has no supported way to load a
    // custom root (contextOptions.ca is ignored in this version), so the real
    // chain is validated up front in the global setup (curl --cacert in
    // e2e-tls-stack.sh) and the browser is told to trust the locally issued
    // leaf here. The secure-context assertions in tls-edge.spec.mjs verify the
    // property actually under test (that the TLS edge is a secure context).
    contextOptions: { ignoreHTTPSErrors: true },
    // Resolve the (non-public) edge hostname to loopback so the browser reaches
    // the local edge without editing /etc/hosts.
    launchOptions: {
      args: [`--host-resolver-rules=MAP ${hostname} 127.0.0.1`],
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
