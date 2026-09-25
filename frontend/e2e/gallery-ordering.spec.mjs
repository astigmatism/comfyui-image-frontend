import { expect, test } from "@playwright/test";

test.use({ timezoneId: "America/Los_Angeles" });

const cards = (page) => page.locator('#gallery [data-gallery-card="generation"]');
const card = (page, id) => page.locator(`#gallery [data-gallery-card="generation"][data-generation-id="${id}"]`);
const cardIds = (page) => cards(page).evaluateAll((nodes) => nodes.map((node) => node.dataset.generationId));
const generation = (id, acceptedAt, overrides = {}) => ({
  id, accepted_at: acceptedAt, collection_id: null, prompt_fingerprint: "batch",
  status: "queued", workflow_display_name: "Fixture workflow", comfyui_instance_id: "primary",
  comfyui_instance_label: "Primary", image_count: 0, artifact_count: 0, final_artifact_count: 0,
  expected_width: 512, expected_height: 512, cancel_allowed: true, ...overrides,
});
const photo = (id, acceptedAt, overrides = {}) => generation(id, acceptedAt, {
  status: "succeeded", cancel_allowed: false, image_count: 1,
  display_artifact: { id, kind: "image", thumbnail_url: `/api/artifacts/${id}/thumbnail` },
  ...overrides,
});
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

async function fixture(page, initial) {
  const settings = { gallery_layout: "grouped", prompt_generation: { enabled: false, active_source: null, sources: {} }, active_source: null, sources: {}, model_selections: {}, quantity: 1, control_sections: {}, recent_resolutions: {}, creative_direction: "", assistant_mode: "refine", assistant_think: true, assistant_instructions: {}, use_creative_direction: false, max_generations: 200 };
  const data = {
    details: new Map(initial.map((item) => [item.id, item])), batch: [], groups: [initial.map((item) => item.id)],
    lookups: [], detailRequests: [], batchRequests: [], historyRequests: 0, memberRequests: 0,
    groupGate: null, detailGates: new Map(),
    preferences: { settings_initialized: true, settings, revision: 1, gallery_scale: 30, checkpoint_tiers: {} },
  };
  const source = {
    source_key: "fixture", display_name: "Fixture workflow", available: true,
    revision: { publication_id: "fixture" },
    interface: { inputs: [{ id: "prompt", label: "Prompt", type: "string", semantic_role: "positive_prompt", required: true }], presets: [] },
  };
  await page.addInitScript(() => {
    window.EventSource = class extends EventTarget {
      constructor() { super(); window.orderingEvents = this; }
      close() {}
    };
    // Full reconciliation scans the gallery's cards to build its key map.
    // This lets the test distinguish it from a single-card progress update.
    window.galleryScans = 0;
    const query = Element.prototype.querySelectorAll;
    Element.prototype.querySelectorAll = function (selector) {
      if (this.id === "gallery" && selector === "[data-gallery-card]") window.galleryScans++;
      return query.call(this, selector);
    };
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    let result = {};
    if (path.startsWith("/api/artifacts/")) return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" fill="#25384d"/></svg>' });
    if (path === "/api/auth/session") result = { authenticated: true, csrf_token: "fixture", user: { id: "ordering", username: "ordering", role: "user", must_change_password: false } };
    else if (path === "/api/preferences") {
      if (request.method() === "PUT") data.preferences = { ...data.preferences, ...request.postDataJSON(), revision: data.preferences.revision + 1 };
      result = data.preferences;
    } else if (path === "/api/workflows") result = url.searchParams.has("output_kind") ? [] : [source];
    else if (path === "/api/workflows/fixture") result = source;
    else if (path === "/api/comfyui-instances") result = { items: [{ id: "primary", label: "Primary", available: true }], default_instance_id: "primary" };
    else if (path === "/api/generations") { data.historyRequests++; result = { items: initial, next_cursor: null }; }
    else if (path === "/api/generations/validate") result = { resolved_seeds: {} };
    else if (path === "/api/generations/batch") {
      data.batchRequests.push(request.postDataJSON());
      return route.fulfill({ status: 201, json: { items: data.batch.map((item, index) => ({ index, generation: item, error: null })) } });
    } else if (/^\/api\/generations\/[^/]+$/.test(path)) {
      const id = path.split("/").at(-1);
      data.detailRequests.push(id);
      if (data.detailGates.has(id)) await data.detailGates.get(id).promise;
      result = data.details.get(id);
    } else if (path.endsWith("/lookup")) {
      const ids = request.postDataJSON().generation_ids;
      data.lookups.push(ids);
      if (data.groupGate) await data.groupGate.promise;
      result = ids.map((id) => {
        const members = data.groups.find((group) => group.includes(id));
        return { generation_id: id, group: { id: members.at(-1), generation_count: members.length, previous_generation_id: null } };
      });
    } else if (path.endsWith("/members")) { data.memberRequests++; result = { items: [], next_cursor: null }; }
    else if (path === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
    else if (path === "/api/generation-activity") result = { remaining_count: data.batch.length, collections: [], run: null };
    else if (["/api/collections", "/api/services", "/api/prompt-generations", "/api/generation-preparations"].includes(path)) result = [];
    return route.fulfill({ json: result });
  });
  await page.goto("/");
  await expect(cards(page)).toHaveCount(initial.length);
  await expect(page.locator(`[data-prompt-group="${initial.at(-1).id}"]`)).toHaveCount(1);
  data.emit = (id, type = "generation.stage", payload = {}) => page.evaluate(({ id, type, payload }) => {
    orderingEvents.dispatchEvent(new MessageEvent(type, { data: JSON.stringify({ generation_id: id, payload }) }));
  }, { id, type, payload });
  return data;
}

test("four accepted cards stay together with legacy timestamps and delayed detail and group responses", async ({ page }) => {
  const older = photo("older", "2026-09-25T07:00:00", { prompt_fingerprint: "older" });
  const data = await fixture(page, [older]);
  data.batch = [1, 2, 3, 4].map((index) => generation(`g${index}`, `2026-09-25T07:11:40.12345${index}${index === 4 ? "Z" : ""}`));
  for (const item of data.batch) data.details.set(item.id, { ...item, accepted_at: item.accepted_at.replace(/Z$/, ""), status: "running" });
  data.groups = [["g4", "g3", "g2", "g1"], ["older"]];
  const groupGate = data.groupGate = deferred();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("Four cards");
  await page.locator("#generation-quantity").fill("4");
  await page.locator("#generation-quantity").blur();
  await page.locator("#generate-button").click();
  await expect(page.locator("#generate-button")).toHaveText("Generate");
  expect(data.batchRequests).toHaveLength(1);
  expect(data.batchRequests[0].items).toHaveLength(4);
  await expect.poll(() => cardIds(page)).toEqual(["g4", "g3", "g2", "g1", "older"]);
  await expect(page.locator(".prompt-group").first().locator('[data-gallery-card="generation"]')).toHaveCount(4);
  await expect.poll(() => data.lookups.some((ids) => ids.includes("g4"))).toBe(true);
  groupGate.resolve(); data.groupGate = null;
  await expect(page.locator('[data-prompt-group="g1"]')).toHaveCount(1);
  await expect(page.locator('[data-prompt-group="g1"] [data-gallery-card="generation"]')).toHaveCount(4);
  await expect(page.locator("[data-group-more]")).toHaveCount(0);

  const lastDetail = deferred(); data.detailGates.set("g4", lastDetail);
  for (const id of ["g4", "g2", "g1", "g3"]) await data.emit(id);
  await expect(card(page, "g3")).toHaveClass(/status-running/);
  await expect(card(page, "g4")).toHaveClass(/status-queued/);
  lastDetail.resolve();
  await expect(card(page, "g4")).toHaveClass(/status-running/);
  expect(await cardIds(page)).toEqual(["g4", "g3", "g2", "g1", "older"]);
  await expect(page.locator('[data-prompt-group="g1"] [data-gallery-card="generation"]')).toHaveCount(4);
  expect(data.historyRequests).toBe(1);
  expect(data.memberRequests).toBe(0);
});

test("live acceptance corrections reorder existing cards while retaining images, selection and focus", async ({ page }) => {
  const data = await fixture(page, [photo("newer", "2026-09-25T07:00:02Z"), photo("older", "2026-09-25T07:00:01Z")]);
  await expect(card(page, "older").locator("img")).toHaveAttribute("data-thumbnail-state", "ready");
  const selection = card(page, "older").locator(".card-select-button");
  await selection.focus(); await selection.press("Space");
  await page.evaluate(() => {
    window.savedCard = document.querySelector('[data-gallery-card="generation"][data-generation-id="older"]');
    window.savedImage = savedCard.querySelector("img");
  });
  data.details.set("older", { ...data.details.get("older"), accepted_at: "2026-09-25T07:00:03Z" });
  data.groups = [["older", "newer"]];
  await data.emit("older");
  await expect.poll(() => cardIds(page)).toEqual(["older", "newer"]);
  await expect(page.locator('[data-prompt-group="newer"]')).toHaveCount(1);
  expect(await page.evaluate(() => savedCard.isConnected && savedImage === savedCard.querySelector("img"))).toBe(true);
  await expect(selection).toHaveAttribute("aria-checked", "true");
  await expect(selection).toBeFocused();
});

test("live prompt changes refresh group membership without losing collapsed groups", async ({ page }) => {
  const data = await fixture(page, [photo("newer", "2026-09-25T07:00:02Z"), photo("older", "2026-09-25T07:00:01Z")]);
  const toggle = page.locator('[data-group-toggle="older"]');
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  data.details.set("newer", { ...data.details.get("newer"), prompt_fingerprint: "changed" });
  data.groups = [["newer"], ["older"]];
  const lookups = data.lookups.length;
  await data.emit("newer");
  await expect(page.locator(".prompt-group")).toHaveCount(2);
  expect(data.lookups.length).toBeGreaterThan(lookups);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(cards(page)).toHaveCount(2);
});

test("live favorites and collection moves reconcile filtered membership", async ({ page }) => {
  const data = await fixture(page, [photo("newer", "2026-09-25T07:00:02Z", { is_favorite: true }), photo("older", "2026-09-25T07:00:01Z")]);
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  await expect.poll(() => cardIds(page)).toEqual(["newer"]);
  data.details.set("newer", { ...data.details.get("newer"), is_favorite: false });
  await data.emit("newer");
  await expect(cards(page)).toHaveCount(0);
  data.details.set("older", { ...data.details.get("older"), is_favorite: true });
  await data.emit("older");
  await expect.poll(() => cardIds(page)).toEqual(["older"]);
  data.details.set("older", { ...data.details.get("older"), collection_id: "elsewhere" });
  await data.emit("older");
  await expect(cards(page)).toHaveCount(0);
});

test("ordinary stage and progress events keep the single-card path and ETA deadline", async ({ page }) => {
  const now = Date.now();
  const progress = { kind: "node", label: "Sampling", value: 2, maximum: 10, fraction: .2,
    updated_at: new Date(now).toISOString(), eta: { remaining_seconds: 30, completion_at: new Date(now + 30_000).toISOString(), updated_at: new Date(now).toISOString(), basis: "history" } };
  const data = await fixture(page, [generation("running", "2026-09-25T07:00:02Z", { status: "running", progress }), generation("waiting", "2026-09-25T07:00:01Z")]);
  await page.evaluate(() => { window.scansBefore = galleryScans; });
  const lookupCount = data.lookups.length;
  const eta = card(page, "running").locator("[data-generation-eta]");
  await expect(eta).toBeVisible();
  const deadline = await eta.getAttribute("data-generation-eta-completion");
  expect(Number(deadline)).toBeGreaterThan(now);
  data.details.set("running", { ...data.details.get("running"), current_stage_label: "Sampling again" });
  await data.emit("running");
  await expect(card(page, "running")).toContainText("Sampling again");
  await data.emit("running", "generation.progress", { progress: { ...progress, value: 3, fraction: .3 } });
  await expect(card(page, "running").getByRole("progressbar")).toHaveAttribute("aria-valuenow", "3");
  expect(await page.evaluate(() => galleryScans - scansBefore)).toBe(0);
  expect(data.lookups.length).toBe(lookupCount);
  expect(await eta.getAttribute("data-generation-eta-completion")).toBe(deadline);
});
