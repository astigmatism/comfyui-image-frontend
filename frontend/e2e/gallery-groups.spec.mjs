import { expect, test } from "@playwright/test";

async function mountGroups(page) {
  let sawNew = false;
  await page.route("**/app.mjs", (route) => route.fulfill({ contentType: "text/javascript", body: "export {};" }));
  await page.route("**/api/gallery/prompt-groups/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const members = Array.from({ length: 4 }, (_, i) => ({ id: `g${i}`, prompt_fingerprint: "A", status: "succeeded", collection_id: null, image_count: 1, accepted_at: `2026-09-15T00:00:0${i}Z` }));
    let body;
    if (url.pathname.endsWith("/lookup")) {
      if (request.postDataJSON().generation_ids.includes("new")) sawNew = true;
      body = request.postDataJSON().generation_ids.map((id) => ({ generation_id: id, group: { id: id === "older" ? "older" : "g0", generation_count: id === "older" ? 1 : members.length + (sawNew ? 1 : 0), previous_generation_id: id === "older" ? null : "older", after_cursor: "after-group" } }));
    } else if (url.pathname.endsWith("/members")) {
      body = { items: url.pathname.includes("/older/") ? [{ ...members[0], id: "older", prompt_fingerprint: "B" }] : members, next_cursor: null };
    } else body = { first_prompt: false, edit_count: 1, snippets: [[{ kind: "context", text: "a lake at " }, { kind: "removed", text: "sunrise" }, { kind: "added", text: "sunset" }]], omitted_edits: 0 };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.evaluate(async () => {
    const base = document.querySelector('script[type="module"]').src;
    const { galleryMarkup, shellMarkup } = await import(new URL("./render.mjs", base));
    const { bindGalleryGroups } = await import(new URL("./gallery-groups.mjs", base));
    const { bindGallerySelection } = await import(new URL("./gallery-selection.mjs", base));
    const { scaleToLayout } = await import(new URL("./lib.mjs", base));
    const state = {
      session: { authenticated: true, user: { username: "groups", role: "user" }, app_title: "ImageGen V2" },
      galleryScale: 20, currentCollectionId: null, favoritesFilter: false,
      collections: [{ id: "archive", name: "Archive", generation_count: 0, parent_id: null }],
      generations: [3, 2].map((i) => ({ id: `g${i}`, prompt_fingerprint: "A", status: "succeeded", collection_id: null, image_count: 1, accepted_at: `2026-09-15T00:00:0${i}Z`, expected_width: 512, expected_height: 512 })),
    };
    state.generations.push({ ...state.generations[0], id: "older", prompt_fingerprint: "B", accepted_at: "2026-09-14T00:00:00Z" });
    const root = document.querySelector("#app");
    root.innerHTML = shellMarkup(state);
    const gallery = root.querySelector("#gallery");
    gallery.classList.add("has-prompt-groups");
    const groups = bindGalleryGroups(root, {
      getState: () => state, render,
      notify: (message) => { window.groupNotice = message; },
      appendMembers: (items) => { const known = new Set(state.generations.map((item) => item.id)); state.generations.push(...items.filter((item) => !known.has(item.id))); render(); },
    });
    function render() { gallery.innerHTML = galleryMarkup(state.generations, { promptGroups: groups.options() }); groups.afterRender(); }
    bindGallerySelection(root, { getState: () => state, refresh: async () => {}, notify: (message) => { window.groupNotice = message; } });
    root.querySelector("#gallery-scale").addEventListener("input", (event) => {
      const layout = scaleToLayout(event.target.value);
      gallery.style.setProperty("--gallery-card-min", `${layout.cardWidth}px`);
      gallery.classList.toggle("gallery-full", layout.full);
    });
    window.addGroupCard = () => { state.generations.unshift({ ...state.generations[0], id: "new", accepted_at: "2026-09-15T00:00:09Z" }); render(); };
    window.redrawGroups = render;
    window.groupCursor = () => groups.paginationCursor("middle-of-group");
    render();
  });
  await expect(page.locator('[data-prompt-group-select="g0"]')).toBeEnabled();
}

test("selects unloaded members, preserves collapsed selections, and reuses bulk dialogs", async ({ page }) => {
  await mountGroups(page);
  const group = page.locator('[data-prompt-group="g0"]');
  const select = group.getByRole("checkbox", { name: "Select group of 4 generations" });
  await select.click();
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("4 selected");
  await expect(select).toHaveAttribute("aria-checked", "true");
  await expect(select).toBeFocused();
  await group.getByRole("button", { name: "Collapse group" }).click();
  await expect(group.locator(".prompt-group-grid")).toBeHidden();
  expect(await page.evaluate(() => window.groupCursor())).toBe("middle-of-group");
  await page.getByRole("button", { name: "Move / Copy…" }).click();
  await expect(page.locator("#gallery-transfer-dialog")).toContainText("4 image cards");
  await page.locator("#gallery-transfer-dialog").getByText("Archive", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Move here", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Delete…", exact: true }).click();
  await expect(page.locator("#gallery-delete-dialog")).toContainText("4 generations");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await group.getByRole("button", { name: "Expand group" }).click();
  await group.getByRole("button", { name: "Load more in group" }).click();
  await expect(group.locator('[data-gallery-card="generation"]')).toHaveCount(4);
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("4 selected");
  await group.locator('[data-generation-id="g3"] .card-select-button').click();
  await expect(select).toHaveAttribute("aria-checked", "mixed");
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("3 selected");
  await page.locator('[data-prompt-group-select="older"]').click();
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("4 selected");
  await page.evaluate(() => window.redrawGroups());
  await expect(select).toHaveAttribute("aria-checked", "mixed");
  await page.getByRole("button", { name: "Clear selection", exact: true }).click();
  await expect(select).toHaveAttribute("aria-checked", "false");
});

test("new arrivals remain unselected and collapsed final groups supply a skip cursor", async ({ page }) => {
  await mountGroups(page);
  await page.locator('[data-prompt-group-select="g0"]').click();
  await page.evaluate(() => window.addGroupCard());
  await expect(page.locator('[data-prompt-group="g0"]')).toHaveAttribute("data-group-count", "5");
  await expect(page.locator('[data-prompt-group-select="g0"]')).toHaveAttribute("aria-checked", "mixed");
  await expect(page.locator('[data-generation-id="new"] .card-select-button')).toHaveAttribute("aria-checked", "false");
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("4 selected");
  await page.locator('[data-group-toggle="older"]').click();
  await expect(page.getByRole("button", { name: "Select loaded (4)", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => window.groupCursor())).toBe("after-group");
});

test("context preview supports keyboard, pinning, Escape and close without clearing selection", async ({ page }) => {
  await mountGroups(page);
  await page.locator('[data-prompt-group-select="g0"]').click();
  const changes = page.locator('[data-group-changes="g0"]');
  await changes.focus();
  await expect(page.locator("#prompt-group-preview del").first()).toHaveText("sunrise");
  await changes.press("Space");
  await page.getByRole("button", { name: "Close prompt changes" }).click();
  await expect(page.locator("#prompt-group-preview")).toBeHidden();
  await expect(changes).toBeFocused();
  await changes.click();
  await expect(page.locator("#prompt-group-preview")).toBeVisible();
  await changes.press("Escape");
  await expect(page.locator("#prompt-group-preview")).toBeHidden();
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("4 selected");
});

test("group grids follow gallery scale and narrow viewports without horizontal overflow", async ({ page }) => {
  await mountGroups(page);
  const card = page.locator('[data-gallery-card="generation"]').first();
  const small = await card.boundingBox();
  await page.getByRole("slider", { name: "Gallery scale" }).fill("100");
  const large = await card.boundingBox();
  expect(large.width).toBeGreaterThan(small.width);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator('[data-group-changes="g0"]').click();
  const popup = await page.locator("#prompt-group-preview").boundingBox();
  expect(popup.x).toBeGreaterThanOrEqual(0);
  expect(popup.x + popup.width).toBeLessThanOrEqual(390);
});
