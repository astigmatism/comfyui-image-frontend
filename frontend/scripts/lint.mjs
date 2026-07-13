import { readFile, readdir } from "node:fs/promises";
import { join, resolve } from "node:path";

const root = resolve("src");
const files = (await readdir(root)).filter((name) => name.endsWith(".mjs"));
let failed = false;
for (const name of files) {
  const text = await readFile(join(root, name), "utf8");
  const banned = ["eval(", "new Function(", "document.write("];
  for (const pattern of banned) {
    if (text.includes(pattern)) {
      console.error(`${name}: banned construct ${pattern}`);
      failed = true;
    }
  }
  if (/https?:\/\/(?:localhost|127\.0\.0\.1|[^"']*comfy)/i.test(text)) {
    console.error(`${name}: frontend must not contain external ComfyUI/Ollama URLs`);
    failed = true;
  }
}
if (failed) process.exit(1);
console.log(`Linted ${files.length} frontend modules.`);
