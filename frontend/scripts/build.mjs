import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const dist = join(root, "dist");
await rm(dist, { recursive: true, force: true });
await mkdir(join(dist, "assets"), { recursive: true });
const modules = ["api.mjs", "lib.mjs", "render.mjs", "app.mjs"];
for (const name of modules) {
  const source = join(root, "src", name);
  execFileSync(process.execPath, ["--check", source], { stdio: "inherit" });
  await cp(source, join(dist, "assets", name));
}
const assets = ["syncopate-latin.woff2", "LICENSE-syncopate.txt"];
for (const name of assets) {
  await cp(join(root, "src", "assets", name), join(dist, "assets", name));
}
await cp(join(root, "src", "styles.css"), join(dist, "assets", "styles.css"));
await cp(join(root, "index.html"), join(dist, "index.html"));
await writeFile(
  join(dist, "build.json"),
  JSON.stringify({ version: "0.1.0", built_at: new Date().toISOString() }, null, 2),
);
const html = await readFile(join(dist, "index.html"), "utf8");
if (!html.includes("/assets/app.mjs") || !html.includes("/assets/styles.css")) {
  throw new Error("Production index does not reference built assets.");
}
console.log(`Built dependency-free frontend into ${dist}`);
