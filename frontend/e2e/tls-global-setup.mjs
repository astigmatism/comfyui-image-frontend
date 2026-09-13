import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..", "..");
const stackScript = path.join(root, "scripts", "e2e-tls-stack.sh");

// Bring the TLS edge up (compose stack, or a local Caddy in front of the
// in-process app) and wait until it is healthy. The runner validates the
// locally issued certificate chain (curl --cacert) before reporting healthy,
// so a broken or wrong chain fails the whole run here.
export default function setup() {
  execFileSync("bash", [stackScript, "up"], {
    cwd: root,
    stdio: "inherit",
    env: process.env,
  });
}
