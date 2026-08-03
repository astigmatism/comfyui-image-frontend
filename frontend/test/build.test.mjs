import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  access,
  appendFile,
  cp,
  mkdir,
  mkdtemp,
  readFile,
  rm,
} from "node:fs/promises";
import test from "node:test";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");

function buildFrontend() {
  execFileSync(process.execPath, ["scripts/build.mjs"], {
    cwd: root,
    stdio: "pipe",
  });
}

async function buildManifest() {
  return JSON.parse(await readFile(join(dist, "build.json"), "utf8"));
}

test("production build emits one content-addressed frontend module graph", async () => {
  buildFrontend();
  const first = await buildManifest();
  assert.match(first.asset_version, /^[0-9a-f]{64}$/u);
  const prefix = `/assets/${first.asset_version}/`;
  assert.ok(Object.values(first.assets).every((path) => path.startsWith(prefix)));

  const emittedPaths = new Set(Object.values(first.assets));
  for (const path of emittedPaths) {
    await access(join(dist, path.replace(/^\//u, "")));
  }
  const index = await readFile(join(dist, "index.html"), "utf8");
  assert.match(index, new RegExp(`href="${first.assets.styles}"`, "u"));
  assert.match(index, new RegExp(`src="${first.assets.app}"`, "u"));
  assert.doesNotMatch(index, /(?:href|src)="\/assets\/(?:styles\.css|app\.mjs)"/u);

  const expectedImports = {
    app: new Set([first.assets.api, first.assets.lib, first.assets.render]),
    api: new Set(),
    lib: new Set(),
    render: new Set([first.assets.lib]),
  };
  for (const name of ["app", "api", "lib", "render"]) {
    const source = await readFile(
      join(dist, first.assets[name].replace(/^\//u, "")),
      "utf8",
    );
    const resolvedImports = new Set(
      [...source.matchAll(/from\s+"(\.\/[^"?]+\.mjs)"/gu)].map(
        ([, specifier]) =>
          new URL(specifier, `https://frontend.invalid${first.assets[name]}`).pathname,
      ),
    );
    assert.deepEqual(resolvedImports, expectedImports[name]);
  }

  const styles = await readFile(
    join(dist, first.assets.styles.replace(/^\//u, "")),
    "utf8",
  );
  assert.match(styles, new RegExp(first.assets.font, "u"));
  assert.doesNotMatch(styles, /\/assets\/syncopate-latin\.woff2/u);
  await assert.rejects(access(join(dist, "assets", "app.mjs")), { code: "ENOENT" });

  buildFrontend();
  const second = await buildManifest();
  assert.equal(second.asset_version, first.asset_version);
  assert.deepEqual(second.assets, first.assets);
});

test("production asset version changes with JavaScript or stylesheet content", async () => {
  const temporaryRoot = await mkdtemp(join(tmpdir(), "cif-frontend-build-"));
  try {
    await mkdir(join(temporaryRoot, "scripts"));
    await cp(join(root, "scripts", "build.mjs"), join(temporaryRoot, "scripts", "build.mjs"));
    await cp(join(root, "index.html"), join(temporaryRoot, "index.html"));
    await cp(join(root, "src"), join(temporaryRoot, "src"), { recursive: true });
    const build = async () => {
      execFileSync(process.execPath, ["scripts/build.mjs"], {
        cwd: temporaryRoot,
        stdio: "pipe",
      });
      return JSON.parse(
        await readFile(join(temporaryRoot, "dist", "build.json"), "utf8"),
      );
    };

    const original = await build();
    await appendFile(join(temporaryRoot, "src", "render.mjs"), "\n// changed release\n");
    const javascriptChanged = await build();
    assert.notEqual(javascriptChanged.asset_version, original.asset_version);
    assert.notEqual(javascriptChanged.assets.app, original.assets.app);

    await appendFile(join(temporaryRoot, "src", "styles.css"), "\n/* changed release */\n");
    const stylesheetChanged = await build();
    assert.notEqual(stylesheetChanged.asset_version, javascriptChanged.asset_version);
    assert.notEqual(stylesheetChanged.assets.styles, javascriptChanged.assets.styles);
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
});
