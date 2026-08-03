import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { access, cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const buildScript = fileURLToPath(import.meta.url);
const here = dirname(buildScript);
const root = resolve(here, "..");
const dist = join(root, "dist");
const modules = ["api.mjs", "lib.mjs", "render.mjs", "app.mjs"];
const staticAssets = ["syncopate-latin.woff2", "LICENSE-syncopate.txt"];
const buildInputs = [
  ["scripts/build.mjs", buildScript],
  ["index.html", join(root, "index.html")],
  ["src/styles.css", join(root, "src", "styles.css")],
  ...modules.map((name) => [`src/${name}`, join(root, "src", name)]),
  ...staticAssets.map((name) => [`src/assets/${name}`, join(root, "src", "assets", name)]),
];

for (const name of modules) {
  execFileSync(process.execPath, ["--check", join(root, "src", name)], {
    stdio: "inherit",
  });
}

const inputContents = new Map();
const fingerprintHash = createHash("sha256");
for (const [name, path] of buildInputs) {
  const content = await readFile(path);
  inputContents.set(name, content);
  fingerprintHash.update(name);
  fingerprintHash.update("\0");
  fingerprintHash.update(content);
  fingerprintHash.update("\0");
}
const assetVersion = fingerprintHash.digest("hex");
const assetPrefix = `/assets/${assetVersion}`;
const assetDirectory = join(dist, "assets", assetVersion);

await rm(dist, { recursive: true, force: true });
await mkdir(assetDirectory, { recursive: true });
for (const name of modules) {
  await cp(join(root, "src", name), join(assetDirectory, name));
}
for (const name of staticAssets) {
  await cp(join(root, "src", "assets", name), join(assetDirectory, name));
}

const sourceFontUrl = "/assets/syncopate-latin.woff2";
const sourceStyles = inputContents.get("src/styles.css").toString("utf8");
if (!sourceStyles.includes(sourceFontUrl)) {
  throw new Error(`Production styles do not reference ${sourceFontUrl}.`);
}
await writeFile(
  join(assetDirectory, "styles.css"),
  sourceStyles.replaceAll(
    sourceFontUrl,
    `${assetPrefix}/syncopate-latin.woff2`,
  ),
);

const sourceHtml = inputContents.get("index.html").toString("utf8");
if (
  !sourceHtml.includes("/assets/app.mjs") ||
  !sourceHtml.includes("/assets/styles.css")
) {
  throw new Error("Production index does not reference the source frontend assets.");
}
const productionHtml = sourceHtml
  .replaceAll("/assets/app.mjs", `${assetPrefix}/app.mjs`)
  .replaceAll("/assets/styles.css", `${assetPrefix}/styles.css`);
await writeFile(join(dist, "index.html"), productionHtml);

const assets = {
  app: `${assetPrefix}/app.mjs`,
  api: `${assetPrefix}/api.mjs`,
  lib: `${assetPrefix}/lib.mjs`,
  render: `${assetPrefix}/render.mjs`,
  styles: `${assetPrefix}/styles.css`,
  font: `${assetPrefix}/syncopate-latin.woff2`,
  font_license: `${assetPrefix}/LICENSE-syncopate.txt`,
};
await writeFile(
  join(dist, "build.json"),
  JSON.stringify(
    {
      version: "0.1.0",
      asset_version: assetVersion,
      assets,
      built_at: new Date().toISOString(),
    },
    null,
    2,
  ),
);

for (const path of Object.values(assets)) {
  await access(join(dist, path.replace(/^\//u, "")));
}
if (
  productionHtml.includes('href="/assets/styles.css"') ||
  productionHtml.includes('src="/assets/app.mjs"')
) {
  throw new Error("Production index retains an unversioned frontend entrypoint.");
}
console.log(`Built dependency-free frontend ${assetVersion} into ${dist}`);
