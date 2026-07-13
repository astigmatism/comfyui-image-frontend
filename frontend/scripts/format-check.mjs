import { readFile, readdir } from "node:fs/promises";
import { join, resolve } from "node:path";

const roots = [resolve("src"), resolve("test"), resolve("scripts")];
let failed = false;
for (const root of roots) {
  for (const name of await readdir(root)) {
    if (!/\.(mjs|css)$/.test(name)) continue;
    const text = await readFile(join(root, name), "utf8");
    if (text.includes("\r\n")) {
      console.error(`${name}: CRLF line endings are not allowed`);
      failed = true;
    }
    text.split("\n").forEach((line, index) => {
      if (/\s+$/.test(line)) {
        console.error(`${name}:${index + 1}: trailing whitespace`);
        failed = true;
      }
    });
  }
}
if (failed) process.exit(1);
console.log("Frontend formatting checks passed.");
