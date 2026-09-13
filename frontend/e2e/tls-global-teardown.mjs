import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..", "..");
const stackScript = path.join(root, "scripts", "e2e-tls-stack.sh");

// Tear the TLS edge down. Best effort: the run result must not be masked by a
// cleanup failure, so a teardown error is reported but not thrown.
export default function teardown() {
  try {
    execFileSync("bash", [stackScript, "down"], {
      cwd: root,
      stdio: "inherit",
      env: process.env,
    });
  } catch (error) {
    console.warn(`[e2e-tls] teardown failed (non-fatal): ${error.message}`);
  }
}
