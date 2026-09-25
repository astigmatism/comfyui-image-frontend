// Local-only headed Chrome probe. No appliance, accounts, or generation services.
// Usage: CIF_PERF_PYTHON=/path/to/python node scripts/profile-photo-viewer.mjs [repo-root ...]
// Compare an archived baseline and the working tree in one run with identical images.
import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const output = process.env.CIF_PERF_OUTPUT || "/tmp/cif-viewer-profile";
const cycles = Number(process.env.CIF_PERF_CYCLES || 50);
await mkdir(output, { recursive: true });
const encoded = JSON.parse(execFileSync(process.env.CIF_PERF_PYTHON || "python3", ["-c", `
import base64, io, json
from PIL import Image
image = Image.effect_noise((2048, 1536), 60).convert('RGB')
result = {}
for name, fmt, size in [('png', 'PNG', None), ('jpeg', 'JPEG', None), ('thumb', 'WEBP', (640, 640))]:
    value = image.copy()
    if size: value.thumbnail(size)
    buffer = io.BytesIO(); value.save(buffer, format=fmt)
    result[name] = base64.b64encode(buffer.getvalue()).decode()
print(json.dumps(result))
`], { maxBuffer: 32 * 1024 * 1024 }));
const media = Object.fromEntries(Object.entries(encoded).map(([key, value]) => [key, Buffer.from(value, "base64")]));

async function measure(sourceRoot, run) {
  const requests = { originals: 0, thumbnails: 0 };
  const generation = (i) => ({ id: `photo-${i}`, collection_id: null, status: i ? "succeeded" : "running", prompt_fingerprint: "profile",
    accepted_at: new Date(Date.UTC(2026, 8, 24, 0, 0, 600 - i)).toISOString(), image_count: 1, expected_width: 2048, expected_height: 1536,
    display_artifact: { id: `image-${i}`, kind: "image", width: 2048, height: 1536,
      content_url: `/api/artifacts/image-${i}/content`, thumbnail_url: `/api/artifacts/image-${i}/thumbnail` } });
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, "http://fixture"), pathname = url.pathname;
      if (pathname.startsWith("/api/")) {
        if (/\/(content|thumbnail)$/.test(pathname)) {
          const thumbnail = pathname.endsWith("/thumbnail");
          requests[thumbnail ? "thumbnails" : "originals"]++;
          const kind = thumbnail ? "thumb" : Number(pathname.match(/image-(\d+)/)[1]) % 2 ? "jpeg" : "png";
          // A fixed delay makes the cache/navigation comparison reproducible.
          if (!thumbnail) await new Promise((resolve) => setTimeout(resolve, 100));
          res.writeHead(200, { "Content-Type": `image/${kind === "thumb" ? "webp" : kind}`, "Cache-Control": "private, max-age=86400, immutable" });
          res.end(media[kind]); return;
        }
        let result = {};
        if (pathname === "/api/auth/session") result = { authenticated: true, csrf_token: "fixture", user: { id: "profile", username: "profile", role: "user", must_change_password: false } };
        else if (pathname === "/api/generations") result = { items: Array.from({ length: 500 }, (_, i) => generation(i)), next_cursor: null };
        else if (pathname === "/api/preferences") result = { settings_initialized: true, settings: {}, revision: 1, gallery_scale: 40, checkpoint_tiers: {} };
        else if (pathname === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
        else if (pathname === "/api/generation-activity") result = { remaining_count: 0, collections: [], run: null };
        else if (pathname === "/api/comfyui-instances") result = { instances: [], default_instance_id: null };
        else if (["/api/collections", "/api/services", "/api/workflows", "/api/prompt-generations", "/api/generation-preparations"].includes(pathname) || pathname.endsWith("/lookup")) result = [];
        res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(result)); return;
      }
      const relative = pathname.startsWith("/assets/") ? pathname.slice(8) : null;
      let file = relative ? path.join(sourceRoot, "frontend/src", relative) : path.join(sourceRoot, "frontend/index.html");
      if (relative?.endsWith(".woff2")) file = path.join(sourceRoot, "frontend/src/assets", relative);
      const data = await readFile(file);
      res.writeHead(200, { "Content-Type": file.endsWith(".mjs") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".woff2") ? "font/woff2" : "text/html" }); res.end(data);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({ channel: "chrome", headless: false });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const cdp = await page.context().newCDPSession(page);
    const browserCdp = await browser.newBrowserCDPSession();
    await cdp.send("Performance.enable");
    await page.addInitScript(() => {
      const NativeImage = window.Image;
      window.profileImages = [];
      window.Image = function (...args) { const image = new NativeImage(...args); profileImages.push(new WeakRef(image)); return image; };
      window.EventSource = class extends EventTarget { constructor() { super(); window.profileEvents = this; } close() {} };
      window.profileScans = { thumbnails: 0, selection: 0 };
      const query = Element.prototype.querySelectorAll;
      Element.prototype.querySelectorAll = function (selector) {
        if (this.id === "gallery-viewport" && selector === "img[data-thumbnail-src]") profileScans.thumbnails++;
        if (this.id === "app" && selector === "#gallery [data-gallery-card]") profileScans.selection++;
        return query.call(this, selector);
      };
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForSelector('[data-action="open-photo"]');
    const ready = async (id) => {
      await page.waitForFunction((id) => {
        const image = document.querySelector(".photo-viewer-media img");
        return document.querySelector(".photo-viewer-frame")?.dataset.photoGenerationId === `photo-${id}` && image?.complete && image.naturalWidth === 2048 && Boolean(image.dataset.photoZoom);
      }, id);
      // The baseline commits before decoding. Compare decoded, painted images,
      // not just a changed src/naturalWidth, which can precede the visible photo.
      await page.evaluate(async () => {
        await document.querySelector(".photo-viewer-media img").decode();
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
    };
    const open = async (id) => { await page.locator(`[data-action="open-photo"][data-generation-id="photo-${id}"]`).evaluate((button) => button.click()); await ready(id); };
    const close = () => page.locator('[data-action="close-photo"]').evaluate((button) => button.click());
    const start = performance.now(); await open(0); const coldOpenMs = performance.now() - start;
    await page.mouse.move(80, 80); await page.mouse.move(81, 80);
    await page.locator('[data-action="toggle-photo-fullscreen"]').click();
    await page.waitForFunction(() => Boolean(document.fullscreenElement));
    const taskTime = async () => (await cdp.send("Performance.getMetrics")).metrics.find((m) => m.name === "TaskDuration").value;
    const idleStart = await taskTime();
    await page.waitForTimeout(2000);
    const idleTaskMs = ((await taskTime()) - idleStart) * 1000;
    await page.evaluate(() => {
      window.profileReplacements = 0;
      const observer = new MutationObserver((records) => {
        for (const record of records) for (const node of record.removedNodes) {
          if (node.nodeType === 1 && (node.matches(".photo-viewer-media img") || node.querySelector(".photo-viewer-media img"))) profileReplacements++;
        }
      });
      observer.observe(document.querySelector(".photo-viewer-host"), { childList: true, subtree: true });
      window.stopProfileObserver = () => observer.disconnect();
    });
    const scansBefore = await page.evaluate(() => ({ ...profileScans }));
    const progressStart = await taskTime();
    await page.evaluate(async () => {
      for (let i = 0; i < 100; i++) {
        profileEvents.dispatchEvent(new MessageEvent("generation.progress", { data: JSON.stringify({ generation_id: "photo-0", payload: { progress: { kind: "node", label: "Sampling", value: i, maximum: 100, fraction: i / 100 } } }) }));
        await new Promise(requestAnimationFrame);
      }
      stopProfileObserver(); delete window.stopProfileObserver;
    });
    const progressTaskMs = ((await taskTime()) - progressStart) * 1000;
    const progress = await page.evaluate(() => ({ ...profileScans, replacements: profileReplacements }));
    const navigation = [];
    for (let i = 1; i <= 12; i++) {
      await page.waitForTimeout(250); // User dwell, allowing one speculative neighbor.
      const start = performance.now(); await page.keyboard.press("ArrowRight"); await ready(i);
      navigation.push(performance.now() - start);
    }
    const warmNavigation = [];
    for (let i = 11; i >= 0; i--) {
      const start = performance.now(); await page.keyboard.press("ArrowLeft"); await ready(i);
      warmNavigation.push(performance.now() - start);
    }
    await page.screenshot({ path: path.join(output, `${run}-fullscreen.png`) });
    await page.keyboard.press("Escape"); await close();
    const memory = async () => {
      await cdp.send("HeapProfiler.collectGarbage");
      const heap = await cdp.send("Runtime.getHeapUsage"), dom = await cdp.send("Memory.getDOMCounters");
      const processes = (await browserCdp.send("SystemInfo.getProcessInfo")).processInfo;
      const rss = execFileSync("ps", ["-o", "rss=", "-p", processes.map((p) => p.id).join(",")], { encoding: "utf8" }).trim().split(/\s+/).reduce((sum, value) => sum + Number(value), 0);
      const loaderImages = await page.evaluate(() => {
        const images = profileImages.map((ref) => ref.deref()).filter(Boolean);
        return { live: images.length, withSource: images.filter((image) => image.hasAttribute("src")).length };
      });
      return { heapMiB: heap.usedSize / 1024 ** 2, browserRssMiB: rss / 1024, domNodes: dom.nodes, documents: dom.documents, loaderImages };
    };
    const memorySamples = [await memory()];
    for (let i = 0; i < cycles; i++) { await open(i % 12); await close(); if ((i + 1) % 10 === 0) memorySamples.push(await memory()); }
    const settledMemorySamples = [];
    for (let i = 0; i < 2; i++) {
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(2000); // Let browser-owned cancellation and image-cache work settle.
      settledMemorySamples.push(await memory());
    }
    if (process.env.CIF_PERF_HEAP_SNAPSHOT) {
      const chunks = [];
      cdp.on("HeapProfiler.addHeapSnapshotChunk", ({ chunk }) => chunks.push(chunk));
      await cdp.send("HeapProfiler.takeHeapSnapshot");
      await writeFile(path.join(output, `${run}.heapsnapshot`), chunks.join(""));
    }
    const retainedImages = await page.locator("#photo-viewer img[src]").count();
    const sorted = [...navigation].sort((a, b) => a - b);
    const warm = [...warmNavigation].sort((a, b) => a - b);
    return { sourceRoot, chrome: browser.version(), fixtureBytes: { png: media.png.length, jpeg: media.jpeg.length }, coldOpenMs, idleTaskMs,
      progressTaskMs, progress: { replacements: progress.replacements, thumbnailScans: progress.thumbnails - scansBefore.thumbnails, selectionScans: progress.selection - scansBefore.selection },
      navigationMs: { median: sorted[Math.floor(sorted.length / 2)], p95: sorted[Math.floor(sorted.length * .95)] },
      warmNavigationMs: { median: warm[Math.floor(warm.length / 2)], p95: warm[Math.floor(warm.length * .95)] },
      requests, retainedImages, cycles, memorySamples, settledMemorySamples };
  } finally { await browser.close(); server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); }
}

const sources = process.argv.slice(2);
if (!sources.length) sources.push(project);
for (const [index, source] of sources.entries()) {
  const result = await measure(path.resolve(source), index);
  await writeFile(path.join(output, `${index}-results.json`), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
}
