import assert from "node:assert/strict";
import test from "node:test";
import { webcrypto } from "node:crypto";
globalThis.crypto ||= webcrypto;
import { submitGeneration, recoverSubmission, pendingSubmission, pendingPromptJobs, setSubmissionOwner } from "../src/generation-submissions.mjs";

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

for (const path of ["/api/prompt-generations", "/api/generation-preparations"]) test(`${path}: lost reply recovers the original job and context without resubmitting`, async (context) => {
  const original = { fetch: globalThis.fetch, localStorage: globalThis.localStorage, sessionStorage: globalThis.sessionStorage };
  context.after(() => { Object.assign(globalThis, original); setSubmissionOwner(null); });
  globalThis.localStorage = storage();
  globalThis.sessionStorage = storage();
  setSubmissionOwner("owner");
  let key;
  let posts = 0;
  globalThis.fetch = async (_url, options) => {
    posts += 1;
    key ??= options.headers.get("Idempotency-Key");
    assert.equal(options.headers.get("Idempotency-Key"), key);
    assert.equal(pendingSubmission().key, key);
    throw new TypeError("Lost reply after acceptance");
  };
  await assert.rejects(submitGeneration(path, { parameters: {} }, { source: "image", generator: "text" }), { code: "submission_status_unknown" });
  const requests = posts;
  const result = path.endsWith("preparations") ? { id: "group", items: [{ id: "item", status: "preparing" }] } : { id: "text-run", status: "running" };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `/api/generation-submissions/${key}`);
    assert.equal(options.method, "GET");
    return new Response(JSON.stringify({ result }), { headers: { "content-type": "application/json" } });
  };
  const recovered = await recoverSubmission();
  assert.deepEqual(recovered.result, result);
  assert.equal(posts, requests);
  assert.equal(pendingSubmission(), null);
  assert.equal(pendingPromptJobs()[0].source, "image");
  assert.equal(pendingPromptJobs()[0].generator, "text");
  setSubmissionOwner("other");
  assert.deepEqual(pendingPromptJobs(), []);
});
