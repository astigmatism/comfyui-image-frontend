import assert from "node:assert/strict";
import test from "node:test";

import { setCsrfToken } from "../src/api.mjs";
import { installComfyUIPush, pushNotice } from "../src/comfyui-push.mjs";
import { galleryCardMarkup, pushToComfyUIControlMarkup } from "../src/render.mjs";

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
    listed_in_input_node: true,
    warning: null,
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

test("a warning travels as a warning and a refusal as an error", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; setCsrfToken(null); });
  const notices = [];
  const button = fakeButton("a1");
  const root = fakeRoot(button);
  installComfyUIPush(root, { notify: (message, kind) => notices.push({ message, kind }) });

  globalThis.fetch = async () => jsonResponse(savedResult({
    subfolder: "from-frontend",
    listed_in_input_node: false,
    warning: "ComfyUI saved the file, but its asset library has not listed it yet.",
  }));
  await root.click();
  globalThis.fetch = async () => jsonResponse({ error: { code: "comfyui_unavailable", message: "The ComfyUI image service could not be reached." } }, 503);
  await root.click();

  assert.deepEqual(notices.map((notice) => notice.kind), ["warning", "error"]);
  assert.equal(
    notices[0].message,
    "Sent moonrise_00002.png to GPU Image Generator → from-frontend/input. ComfyUI saved the file, but its asset library has not listed it yet.",
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

test("only an image can be sent, and the control carries the image it means", () => {
  const markup = pushToComfyUIControlMarkup({ id: "a1", kind: "image" });
  assert.match(markup, /data-action="push-comfyui"/);
  assert.match(markup, /data-artifact-id="a1"/);
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
  assert.match(card, /data-action="push-comfyui" data-artifact-id="image-1"/);
});
