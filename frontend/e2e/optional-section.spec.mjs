import { expect, test } from "@playwright/test";

// The Optional section holds the extras a workflow declares on top of its core
// controls. Their vertical rhythm is the shared one Prompt Generation already
// uses, so this spec declares the same pair the production bundle publishes —
// `enable_upscale` and `upscale_denoise` — because the published e2e fixtures
// declare a single extra, whose section has no interior divider to remove.
const REVISION = {
  publication_id: "publication-1",
  workflow_sha256: "w".repeat(64),
  api_sha256: "a".repeat(64),
  manifest_sha256: "m".repeat(64),
};

const SOURCE = {
  source_key: "optional-source",
  display_name: "Upscale workflow",
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
        advanced: false,
        default: "",
        group: "Basic",
        order: 10,
      },
      {
        id: "enable_upscale",
        type: "boolean",
        label: "Enable Upscale",
        semantic_role: "feature_toggle",
        required: false,
        advanced: false,
        default: false,
        group: "Basic",
        order: 50,
      },
      {
        id: "upscale_denoise",
        type: "number",
        label: "Upscale denoise",
        description: "Denoise strength used by the upscale stage.",
        semantic_role: "denoise",
        required: false,
        advanced: false,
        default: 0.18,
        minimum: 0,
        maximum: 0.5,
        step: 0.01,
        group: "Advanced",
        order: 55,
      },
    ],
    outputs: [],
  },
};

async function fixture(page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let result = {};
    if (path === "/api/auth/session") {
      result = {
        authenticated: true,
        app_title: "ImageGen",
        csrf_token: "fixture",
        user: { id: "optional", username: "optional", role: "user", must_change_password: false },
      };
    } else if (path === "/api/preferences") {
      result = {
        settings_initialized: true,
        settings: {
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
        },
        revision: 1,
        gallery_scale: 30,
        checkpoint_tiers: {},
      };
    } else if (path === "/api/generations") result = { items: [], next_cursor: null };
    else if (path === "/api/gallery/items") result = { generations: [], collection_ids: [] };
    else if (path === "/api/generation-activity") result = { remaining_count: 0, run: null, worker_pool: null };
    else if (path === "/api/comfyui-instances") {
      result = {
        default_instance_id: "primary",
        text_instance_id: null,
        configuration_mode: "explicit",
        image_pool: { worker_count: 1, available_count: 1, idle_count: 1, busy_count: 0, free_slot_count: 1, unassigned_queued_count: 0 },
        image_pool_instance_ids: ["primary"],
        items: [
          {
            id: "primary",
            label: "Primary",
            description: null,
            is_default: true,
            available: true,
            message: null,
            checked_at: new Date().toISOString(),
            role: "image",
            in_image_pool: true,
            busy: false,
          },
        ],
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
}

test("Optional stacks its controls without a divider, on the rhythm Prompt Generation uses", async ({ page }) => {
  await fixture(page);
  const optional = page.locator('[data-control-section="optional"]');
  // The section opens collapsed, like every other extras section.
  await expect(optional.locator(".control-section-trigger")).toHaveAttribute("aria-expanded", "false");
  await optional.locator(".control-section-trigger").click();

  const blocks = optional.locator(".control-block");
  await expect(blocks).toHaveCount(2);
  await expect(blocks.first()).toHaveAttribute("data-control-block", "enable_upscale");
  await expect(blocks.last()).toHaveAttribute("data-control-block", "upscale_denoise");

  // A control row carries a bottom hairline by default; inside Optional none of
  // them keeps it, so the switch and the refinement under it read as one list.
  for (const block of [blocks.first(), blocks.last()]) {
    await expect(block).toHaveCSS("border-bottom-width", "0px");
    await expect(block).toHaveCSS("padding-bottom", "0px");
  }
  await expect(blocks.first()).toHaveCSS("margin-bottom", "12px");
  // The list ends at the section's own padding instead of an extra gap.
  await expect(blocks.last()).toHaveCSS("margin-bottom", "0px");

  // 12px is the gap Prompt Generation separates its own stacked controls with.
  await expect(page.locator(".prompt-generation-body")).toHaveCSS("row-gap", "12px");
});
