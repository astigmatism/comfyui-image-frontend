import { expect, test } from "@playwright/test";

const REVISION = {
  publication_id: "publication-1",
  workflow_sha256: "w".repeat(64),
  api_sha256: "a".repeat(64),
  manifest_sha256: "m".repeat(64),
};

const SOURCE = {
  source_key: "pool-source",
  display_name: "Portrait",
  instance_id: "primary",
  readiness: "ready",
  available: true,
  cached: false,
  message: null,
  warnings: [],
  revision: REVISION,
  output_kind: "image",
  replicas: [],
  interface: {
    inputs: [
      {
        id: "prompt",
        type: "multiline_string",
        label: "Prompt",
        semantic_role: "positive_prompt",
        required: true,
        default: "",
        group: "Basic",
        order: 10,
      },
    ],
    outputs: [],
  },
};

function worker(id, label, { available = true, busy = false } = {}) {
  return {
    id,
    label,
    description: null,
    is_default: id === "primary",
    available,
    message: available ? null : "ComfyUI is unreachable.",
    checked_at: new Date().toISOString(),
    role: "image",
    in_image_pool: true,
    busy,
  };
}

async function fixture(page, { pool, instances }) {
  const state = { pool, instances };
  const settings = {
    gallery_layout: "classic",
    prompt_generation: { enabled: false, active_source: null, sources: {} },
    active_source: SOURCE.source_key,
    sources: {},
    model_selections: {},
    quantity: 1,
    control_sections: {},
    recent_resolutions: {},
    creative_direction: "",
    assistant_mode: "refine",
    assistant_think: true,
    assistant_instructions: {},
    use_creative_direction: false,
    max_generations: 200,
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let result = {};
    if (path === "/api/auth/session") {
      result = {
        authenticated: true,
        app_title: "ImageGen",
        csrf_token: "fixture",
        user: { id: "pool", username: "pool", role: "user", must_change_password: false },
      };
    } else if (path === "/api/preferences") {
      result = { settings_initialized: true, settings, revision: 1, gallery_scale: 30, checkpoint_tiers: {} };
    } else if (path === "/api/generations") result = { items: [], next_cursor: null };
    else if (path === "/api/gallery/items") result = { generations: [], collection_ids: [] };
    else if (path === "/api/generation-activity") {
      result = { remaining_count: 0, run: null, worker_pool: state.pool };
    } else if (path === "/api/comfyui-instances") {
      result = {
        default_instance_id: "primary",
        text_instance_id: null,
        configuration_mode: "explicit",
        image_pool: state.pool,
        image_pool_instance_ids: state.instances.map((item) => item.id),
        items: state.instances,
      };
    } else if (path === "/api/workflows") result = [SOURCE];
    else if (path === `/api/workflows/${SOURCE.source_key}`) result = SOURCE;
    else if (path === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
    else if (path === "/api/services") {
      result = [
        { service: "comfyui", available: true, message: null, checked_at: null },
        { service: "ollama", available: false, message: null, checked_at: null },
      ];
    } else if (path === "/api/events") {
      return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
    } else if (["/api/collections", "/api/prompt-generations", "/api/generation-preparations"].includes(path)) {
      result = [];
    }
    return route.fulfill({ json: result });
  });
  await page.goto("/");
  return state;
}

test("idle image workers are reported beside Generate and update in place", async ({ page }) => {
  const state = await fixture(page, {
    pool: {
      worker_count: 3,
      available_count: 3,
      idle_count: 2,
      busy_count: 1,
      free_slot_count: 2,
      unassigned_queued_count: 0,
    },
    instances: [
      worker("primary", "Primary", { busy: true }),
      worker("w-192-168-1-21-8189", "ComfyUI 192.168.1.21:8189"),
      worker("w-192-168-1-22-8188", "ComfyUI 192.168.1.22:8188"),
    ],
  });
  const indicator = page.locator("#worker-pool-status");
  await expect(indicator).toHaveText("2 of 3 workers idle");
  await expect(indicator).toHaveAttribute("title", /Primary: busy/);
  await expect(indicator).toHaveAttribute("title", /ComfyUI 192.168.1.21:8189: idle/);
  // The indicator is the only pool surface in the panel; no runtime selector appears.
  await expect(page.locator("#comfyui-instance")).toHaveCount(0);

  state.pool = {
    worker_count: 3,
    available_count: 2,
    idle_count: 0,
    busy_count: 2,
    free_slot_count: 0,
    unassigned_queued_count: 5,
  };
  state.instances = [
    worker("primary", "Primary", { busy: true }),
    worker("w-192-168-1-21-8189", "ComfyUI 192.168.1.21:8189", { busy: true }),
    worker("w-192-168-1-22-8188", "ComfyUI 192.168.1.22:8188", { available: false }),
  ];
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect
    .poll(() => page.locator("#worker-pool-status").textContent(), { timeout: 15000 })
    .toBe("All 2 workers busy — new images queue · 1 offline");
  // A partially degraded pool keeps generating, so no banner appears.
  await expect(page.locator("#service-banner .service-banner")).toHaveCount(0);
});

test("a single-worker deployment shows no worker readout", async ({ page }) => {
  await fixture(page, {
    pool: {
      worker_count: 1,
      available_count: 1,
      idle_count: 1,
      busy_count: 0,
      free_slot_count: 1,
      unassigned_queued_count: 0,
    },
    instances: [worker("primary", "Primary")],
  });
  await expect(page.locator("#worker-pool-status")).toHaveText("");
  expect(await page.locator("#worker-pool-status").isVisible()).toBe(false);
});
