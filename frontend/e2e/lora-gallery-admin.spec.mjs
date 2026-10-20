import { expect, test } from "@playwright/test";

async function mount(page) {
  await page.route("**/gallery-sample-a.webp", (route) => route.fulfill({ path: new URL("../mockups/assets/cartographer.webp", import.meta.url).pathname }));
  await page.route("**/gallery-sample-b.webp", (route) => route.fulfill({ path: new URL("../mockups/assets/glass-orchid.webp", import.meta.url).pathname }));
  await page.route("**/app.mjs", (route) => route.fulfill({ contentType: "text/javascript", body: "export {};" }));
  await page.goto("/");
  await page.evaluate(async () => {
    const base = document.querySelector('script[type="module"]').src;
    const { installLoraManager } = await import(new URL("./lora-manager.mjs", base));
    const { createAdminLoraController } = await import(new URL("./admin-loras.mjs", base));
    const identity = (letter) => `lr1_${letter.repeat(64)}`;
    window.catalog = [{ id: "a", label: "Alpha", lora_identity: identity("a") }, { id: "b", label: "Beta", lora_identity: identity("b") }];
    window.revision = { publication_id: "one" };
    window.grouping = { library_key: "krea2", revision: 0, groups: [{ id: "uncategorized", name: "Uncategorized", system: true }], assignments: {} };
    window.stack = [{ id: "a", strength: .65 }, { id: "b", strength: 0 }];
    window.memory = { a: .65, b: 1.25 };
    window.operations = {};
    window.images = {};
    window.requests = [];
    window.notices = [];
    const control = () => ({ id: "loras", type: "lora_stack", label: "LoRAs", items: structuredClone(window.catalog), minimum: 0, maximum: 2, step: .05, default: window.catalog.map(({ id }) => ({ id, strength: 0 })) });
    const library = () => ({ key: "krea2", label: "Krea 2", eligible: true, in_sync: true, items: window.catalog, members: [{ source_key: "source", display_name: "Workflow", in_sync: true }], expected_library: [{ source_key: "source", revision: window.revision }] });
    const api = async (path, options) => {
      window.requests.push([path, options?.method || "GET"]);
      const body = options?.body && typeof options.body === "string" ? JSON.parse(options.body) : {};
      if (path === "/api/admin/lora-library") return { libraries: [library(), { ...library(), key: "other", label: "Other", items: [], members: [] }] };
      if (path === "/api/workflows/source") return { interface: { inputs: [control()] }, revision: window.revision };
      if (path.includes("lora-images")) return { items: window.catalog.map((item) => ({ id: item.id, lora_identity: item.lora_identity, version: "a".repeat(64), image_url: item.id === "a" || item.id === "b" ? `/gallery-sample-${item.id}.webp` : null })) };
      if (path === "/api/admin/lora-operations") {
        const id = String(Object.keys(window.operations).length + 1);
        const operation = { id, status: body.kind === "install" ? "awaiting_upload" : "succeeded" };
        window.operations[id] = operation;
        if (body.kind === "edit") window.catalog = window.catalog.map((item) => item.id === body.lora_id ? { ...item, label: body.display_name, trigger_word: body.trigger_word } : item);
        if (body.kind === "remove") window.catalog = window.catalog.filter((item) => item.id !== body.lora_id);
        window.revision = { publication_id: id };
        return operation;
      }
      if (path.startsWith("/api/admin/lora-operations/")) return window.operations[path.split("/").pop()];
      if (options && (path.includes("/groups") || path.includes("group-memberships") || path.endsWith("/group-order"))) {
        if (body.revision !== window.grouping.revision) throw Object.assign(new Error("Groups changed elsewhere. Review the latest groups and try again."), { code: "lora_group_conflict" });
        const last = path.split("/").pop();
        if (last === "group-order") window.grouping.groups = body.group_ids.map((id) => window.grouping.groups.find((group) => group.id === id));
        else if (path.includes("group-memberships")) window.grouping.assignments[last] = body.group_id;
        else if (options.method === "POST") window.grouping.groups.unshift({ id: `group-${window.grouping.revision}`, name: body.name, system: false });
        else if (options.method === "PATCH") window.grouping.groups = window.grouping.groups.map((group) => group.id === last ? { ...group, name: body.name } : group);
        else {
          window.grouping.groups = window.grouping.groups.filter((group) => group.id !== last);
          for (const key of Object.keys(window.grouping.assignments)) if (window.grouping.assignments[key] === last) window.grouping.assignments[key] = "uncategorized";
        }
        window.grouping.revision += 1;
      }
      if (path.includes("groups") || path.includes("group-memberships") || path.endsWith("/group-order")) return structuredClone(window.grouping);
      throw new Error(`Unexpected request ${path}`);
    };
    const root = document.querySelector("#app");
    root.innerHTML = '<button type="button" data-lora-open data-lora-control-id="loras">Open selector</button><button type="button" id="library">Open library</button><dialog id="lora-manager-dialog" class="lm-dialog"></dialog>';
    let manager;
    const admin = createAdminLoraController({ api, getCsrfToken: () => "token", notify: (message) => window.notices.push(message), pause: async () => {}, confirm: () => true,
      uploadFile: async (path) => { const id = path.split("/").at(-2); window.catalog.push({ id: "new", label: "New LoRA", lora_identity: identity("c") }); window.operations[id].status = "succeeded"; },
      refreshSources: () => manager.refreshLibrarySources(async () => { if (window.failRefresh) throw new Error("Refresh failed"); const ids = new Set(window.catalog.map((item) => item.id)); window.stack = window.stack.filter((item) => ids.has(item.id)); for (const item of window.catalog) if (!window.stack.some((entry) => entry.id === item.id)) window.stack.push({ id: item.id, strength: 0 }); }),
    });
    manager = installLoraManager(root, { api, isAdmin: () => true, adminController: admin,
      context: () => ({ control: control(), sourceKey: "source", sourceName: "Workflow", publicationRevision: window.revision, values: window.stack, memory: window.memory, images: window.images }),
      apply: (_id, values, memory) => { window.stack = structuredClone(values); window.memory = structuredClone(memory); },
      onImages: (_key, _id, images) => { window.images = images; }, notify: (message) => window.notices.push(message),
    });
    document.querySelector("#library").onclick = (event) => manager.openLibrary(event.target);
  });
}
const manager = (page) => page.locator("#lora-manager-dialog");
async function actions(page, name) { await manager(page).getByRole("button", { name: `Actions for ${name}`, exact: true }).click(); }
async function newGroup(page, name) {
  await manager(page).getByRole("button", { name: "New group" }).click();
  await manager(page).getByRole("textbox", { name: "Group name" }).fill(name);
  await manager(page).getByRole("button", { name: "Save", exact: true }).click();
}

test("admin groups save immediately while selection stays a draft", async ({ page }, testInfo) => {
  await mount(page);
  await page.getByRole("button", { name: "Open selector" }).click();
  await manager(page).getByRole("button", { name: "Toggle Beta" }).click();
  await newGroup(page, "Portraits");
  await expect(manager(page).getByText("No LoRAs in this group.", { exact: true })).toBeVisible();
  await actions(page, "Beta");
  await manager(page).getByRole("button", { name: "Move to group…", exact: true }).click();
  await manager(page).getByRole("combobox", { name: "Group", exact: true }).selectOption({ label: "Portraits" });
  await manager(page).getByRole("button", { name: "Save", exact: true }).click();
  const group = manager(page).locator('[data-lora-group="group-0"]');
  await expect(group.getByRole("button", { name: "Toggle Beta" })).toHaveAttribute("aria-pressed", "true");
  await group.getByRole("button", { name: "Rename", exact: true }).click();
  await manager(page).getByRole("textbox", { name: "Group name" }).fill("Characters");
  await manager(page).getByRole("button", { name: "Save", exact: true }).click();
  await expect(group.getByRole("heading", { name: "Characters" })).toBeVisible();
  await manager(page).screenshot({ path: testInfo.outputPath("grouped-gallery-desktop.png"), animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await manager(page).evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(manager(page).getByRole("button", { name: "Apply", exact: true })).toBeInViewport();
  await manager(page).screenshot({ path: testInfo.outputPath("grouped-gallery-mobile.png"), animations: "disabled" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await manager(page).getByRole("button", { name: "Cancel", exact: true }).click();
  expect(await page.evaluate(() => window.stack[1].strength)).toBe(0);
  await page.getByRole("button", { name: "Open selector" }).click();
  await expect(group.getByRole("button", { name: "Toggle Beta" })).toHaveAttribute("aria-pressed", "false");
  page.once("dialog", (dialog) => dialog.accept());
  await group.getByRole("button", { name: "Delete group", exact: true }).click();
  await expect(group).toHaveCount(0);
  await expect(manager(page).locator('[data-lora-group="uncategorized"] .lm-card')).toHaveCount(2);
});

test("group conflicts reload metadata and preserve selection for explicit retry", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "Open selector" }).click();
  await manager(page).getByRole("button", { name: "Toggle Beta" }).click();
  await page.evaluate(() => { window.grouping.revision = 4; });
  await newGroup(page, "Styles");
  await expect(manager(page).getByRole("alert")).toContainText("changed elsewhere");
  await manager(page).getByRole("button", { name: "Save", exact: true }).click();
  await expect(manager(page).getByRole("heading", { name: "Styles", exact: true })).toBeVisible();
  await manager(page).getByRole("button", { name: "Apply", exact: true }).click();
  expect(await page.evaluate(() => window.stack[1].strength)).toBe(1.25);
});

test("library operations keep the selector open and reconcile its draft", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "Open selector" }).click();
  await manager(page).getByRole("button", { name: "Toggle Beta" }).click();
  await actions(page, "Alpha");
  await manager(page).getByRole("button", { name: "Edit LoRA details…", exact: true }).click();
  await manager(page).getByRole("textbox", { name: "Display title" }).fill("Renamed Alpha");
  await manager(page).getByRole("button", { name: "Save details", exact: true }).click();
  await expect(manager(page).getByRole("button", { name: "Toggle Renamed Alpha" })).toHaveAttribute("aria-pressed", "true");
  await expect(manager(page).getByRole("button", { name: "Toggle Beta" })).toHaveAttribute("aria-pressed", "true");
  await manager(page).getByRole("button", { name: "Add LoRA" }).click();
  await manager(page).getByLabel("Weight file").setInputFiles({ name: "new.safetensors", mimeType: "application/octet-stream", buffer: Buffer.alloc(32) });
  await manager(page).getByRole("textbox", { name: "Display title" }).fill("New LoRA");
  await manager(page).getByRole("textbox", { name: "Trigger word", exact: true }).fill("new subject");
  await manager(page).getByRole("button", { name: "Install LoRA", exact: true }).click();
  await expect(manager(page).getByRole("button", { name: "Toggle New LoRA" })).toHaveAttribute("aria-pressed", "false");
  await actions(page, "Renamed Alpha");
  await manager(page).getByRole("button", { name: "Delete LoRA…", exact: true }).click();
  await expect(manager(page).getByRole("button", { name: "Toggle Renamed Alpha" })).toHaveCount(0);
  await expect(manager(page).getByRole("button", { name: "Apply", exact: true })).toBeEnabled();
  await manager(page).getByRole("button", { name: "Apply", exact: true }).click();
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "b", strength: 1.25 }, { id: "new", strength: 0 }]);
});

test("management mode switches families without selection controls and blocks stale Apply after failed refresh", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "Open library" }).click();
  await expect(manager(page).getByRole("heading", { name: "LoRA library", exact: true })).toBeVisible();
  await expect(manager(page).getByRole("button", { name: "Apply", exact: true })).toHaveCount(0);
  await expect(manager(page).locator(".lm-order-bar, [data-lora-toggle]")).toHaveCount(0);
  await expect(manager(page).getByRole("slider", { name: "LoRA thumbnail size" })).toBeVisible();
  await manager(page).getByRole("combobox", { name: "Model family" }).selectOption("other");
  await expect(manager(page).locator(".lm-card")).toHaveCount(0);
  await manager(page).getByRole("button", { name: "Done", exact: true }).click();
  await page.getByRole("button", { name: "Open selector" }).click();
  await page.evaluate(() => { window.failRefresh = true; });
  await actions(page, "Alpha");
  await manager(page).getByRole("button", { name: "Edit LoRA details…", exact: true }).click();
  await manager(page).getByRole("textbox", { name: "Display title" }).fill("Changed");
  await manager(page).getByRole("button", { name: "Save details", exact: true }).click();
  await expect(manager(page).getByRole("button", { name: "Apply", exact: true })).toBeDisabled();
  await expect(manager(page)).toContainText("Refresh failed");
});

test("group move controls save shared order, support the fallback, and require explicit conflict retry", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "Open selector" }).click();
  await newGroup(page, "Portraits");
  await newGroup(page, "Styles");
  const names = () => manager(page).locator(".lm-group-heading h3").allTextContents();
  await expect.poll(names).toEqual(["Styles", "Portraits", "Uncategorized"]);
  await expect(manager(page).getByRole("button", { name: "Move Styles up", exact: true })).toBeDisabled();
  await manager(page).getByRole("button", { name: "Toggle Beta" }).click();
  await page.evaluate(() => { window.grouping.revision += 1; });
  await manager(page).getByRole("button", { name: "Move Styles down", exact: true }).click();
  await expect(manager(page).getByRole("alert")).toContainText("changed elsewhere");
  expect(await names()).toEqual(["Styles", "Portraits", "Uncategorized"]);
  await manager(page).getByRole("button", { name: "Move Styles down", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect.poll(names).toEqual(["Portraits", "Styles", "Uncategorized"]);
  await expect(manager(page).getByRole("button", { name: "Move Styles down", exact: true })).toBeFocused();
  await manager(page).getByRole("button", { name: "Move Uncategorized up", exact: true }).click();
  await expect.poll(names).toEqual(["Portraits", "Uncategorized", "Styles"]);
  await expect(manager(page).getByRole("button", { name: "Move Styles down", exact: true })).toBeDisabled();
  await expect(manager(page).getByRole("button", { name: "Toggle Beta" })).toHaveAttribute("aria-pressed", "true");
  await manager(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Open library" }).click();
  await expect.poll(names).toEqual(["Portraits", "Uncategorized", "Styles"]);
  expect(await page.evaluate(() => window.stack[1].strength)).toBe(0);
  const touch = await page.context().newCDPSession(page);
  await touch.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
  await page.setViewportSize({ width: 390, height: 844 });
  const moveUp = manager(page).getByRole("button", { name: "Move Styles up", exact: true });
  await moveUp.scrollIntoViewIfNeeded();
  const box = await moveUp.boundingBox();
  expect(box.width).toBeGreaterThanOrEqual(44);
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(await manager(page).evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
});
