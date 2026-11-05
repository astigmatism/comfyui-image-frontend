import assert from "node:assert/strict";
import test from "node:test";

import { setCsrfToken } from "../src/api.mjs";
import { installComfyUIPush, pushBatchNotice, pushNotice } from "../src/comfyui-push.mjs";
import { icons } from "../src/icons.mjs";
import { galleryCardMarkup, photoViewerMarkup, pushToComfyUIControlMarkup } from "../src/render.mjs";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function savedResult(overrides = {}) {
  return {
    artifact_id: "a1",
    instance_id: "primary",
    instance_label: "GPU Image Generator",
    name: "moonrise_00002.png",
    subfolder: "",
    mime_type: "image/png",
    byte_size: 4096,
    readable_as_input: true,
    warning: null,
    ...overrides,
  };
}

function batchResult(overrides = {}) {
  return {
    requested: 3,
    sent: 3,
    instance_labels: ["GPU Image Generator"],
    warning: null,
    failures: [],
    ...overrides,
  };
}

function fakeButton(artifactId) {
  return {
    dataset: { artifactId },
    disabled: false,
    attributes: new Map(),
    setAttribute(name, value) { this.attributes.set(name, value); },
    removeAttribute(name) { this.attributes.delete(name); },
  };
}

// The smallest root the module can talk to: one click listener, one control to find.
function fakeRoot(button) {
  const handlers = new Map();
  return {
    addEventListener(type, handler) { handlers.set(type, handler); },
    removeEventListener(type) { handlers.delete(type); },
    querySelector: () => button,
    click: () => handlers.get("click")({ preventDefault() {}, target: { closest: () => button } }),
  };
}

test("one click copies one image and names the service that took it", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; setCsrfToken(null); });
  setCsrfToken("push-token");
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, method: options.method, body: options.body, csrf: options.headers.get("X-CSRF-Token") });
    return jsonResponse(savedResult());
  };
  const notices = [];
  const button = fakeButton("a1");
  const root = fakeRoot(button);
  installComfyUIPush(root, { notify: (message, kind) => notices.push({ message, kind }) });

  await root.click();

  assert.deepEqual(calls[0], {
    path: "/api/artifacts/a1/push-to-comfyui",
    method: "POST",
    body: "{}",
    csrf: "push-token",
  });
  assert.deepEqual(notices, [
    { message: "Sent moonrise_00002.png to GPU Image Generator → input", kind: "success" },
  ]);
  assert.equal(button.disabled, false);
  assert.equal(button.attributes.has("aria-busy"), false);
});

test("an in-flight copy holds the control until the answer arrives", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; setCsrfToken(null); });
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let attempts = 0;
  globalThis.fetch = async () => {
    attempts += 1;
    await gate;
    return jsonResponse(savedResult());
  };
  const notices = [];
  const button = fakeButton("a1");
  const root = fakeRoot(button);
  installComfyUIPush(root, { notify: (message, kind) => notices.push({ message, kind }) });

  const first = root.click();
  await Promise.resolve();
  const second = root.click();
  assert.equal(button.disabled, true);
  assert.equal(button.attributes.get("aria-busy"), "true");
  release();
  await Promise.all([first, second]);
  assert.equal(attempts, 1);
  assert.equal(notices.length, 1);
  assert.equal(button.disabled, false);
});

test("a copy ComfyUI cannot serve back is a warning and a refusal is an error", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; setCsrfToken(null); });
  const notices = [];
  const button = fakeButton("a1");
  const root = fakeRoot(button);
  installComfyUIPush(root, { notify: (message, kind) => notices.push({ message, kind }) });

  globalThis.fetch = async () => jsonResponse(savedResult({
    subfolder: "from-frontend",
    readable_as_input: false,
    warning: "ComfyUI took the file into its input folder but did not give it back.",
  }));
  await root.click();
  globalThis.fetch = async () => jsonResponse({ error: { code: "comfyui_unavailable", message: "The ComfyUI image service could not be reached." } }, 503);
  await root.click();

  assert.deepEqual(notices.map((notice) => notice.kind), ["warning", "error"]);
  assert.equal(
    notices[0].message,
    "Sent moonrise_00002.png to GPU Image Generator → from-frontend/input. ComfyUI took the file into its input folder but did not give it back.",
  );
  assert.equal(notices[1].message, "The ComfyUI image service could not be reached.");
  assert.equal(button.disabled, false);
});

test("the notice names the service because two of them are reachable", () => {
  assert.equal(
    pushNotice(savedResult({ name: "cat.png", instance_label: "Second GPU" })),
    "Sent cat.png to Second GPU → input",
  );
});

test("a selection is counted, placed, and only doubted when a copy did not land", () => {
  assert.deepEqual(
    pushBatchNotice(batchResult()),
    { message: "Sent 3 images to GPU Image Generator → input", kind: "success" },
  );
  assert.equal(
    pushBatchNotice(batchResult({ requested: 1, sent: 1 })).message,
    "Sent 1 image to GPU Image Generator → input",
  );
  // Two image services means the files went to two different input folders.
  assert.equal(
    pushBatchNotice(batchResult({ instance_labels: ["Primary", "Second GPU"] })).message,
    "Sent 3 images to 2 ComfyUI services → input",
  );
  assert.deepEqual(
    pushBatchNotice(batchResult({ requested: 4, sent: 3, warning: "The service was offline." })),
    {
      message: "Sent 3 images of 4 images to GPU Image Generator → input. The service was offline.",
      kind: "warning",
    },
  );
  // Nothing landed, so this is a failure and not a quiet success.
  assert.deepEqual(
    pushBatchNotice(batchResult({ sent: 0, warning: "The service was offline.", failures: [{ artifact_id: "a1", code: "comfyui_unavailable", message: "The service was offline." }] })),
    { message: "The service was offline.", kind: "error" },
  );
  assert.deepEqual(
    pushBatchNotice(batchResult({ sent: 0, warning: null })),
    { message: "The images could not be sent to ComfyUI.", kind: "error" },
  );
});

test("the card carries no ComfyUI control, the viewer and the helper do", () => {
  const markup = pushToComfyUIControlMarkup({ id: "a1", kind: "image" });
  assert.match(markup, /data-action="push-comfyui"/);
  assert.match(markup, /data-artifact-id="a1"/);
  // One glyph for the action wherever it appears, including the selection toolbar.
  assert.ok(markup.includes(icons.push));
  assert.match(pushToComfyUIControlMarkup(null), /disabled/);
  assert.doesNotMatch(pushToComfyUIControlMarkup(null), /data-action/);
  const card = galleryCardMarkup({
    id: "generation-1",
    status: "succeeded",
    display_artifact: {
      id: "image-1",
      kind: "image",
      content_url: "/api/artifacts/image-1/content",
    },
  });
  assert.doesNotMatch(card, /push-comfyui/);
  const viewer = photoViewerMarkup({
    id: "generation-1",
    workflow_display_name: "Published source",
    status: "succeeded",
    display_artifact: {
      id: "image-1",
      kind: "image",
      content_url: "/api/artifacts/image-1/content",
    },
  });
  assert.match(viewer, /data-action="push-comfyui" data-artifact-id="image-1"/);
});
