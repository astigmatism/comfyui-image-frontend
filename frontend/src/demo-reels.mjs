import { icon } from "./icons.mjs";
import { isAdvancedInput, normalizeThumbnailScale, sortInterfaceInputs } from "./lib.mjs";
import { controlMarkup, demoReelGalleryCardMarkup, demoReelPlaceholderCardMarkup } from "./render.mjs";
import { createThumbnailScheduler, installThumbnails } from "./thumbnails.mjs";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const encoded = encodeURIComponent;

export function demoReelPath(libraryKey, identity) {
  return `/api/demo-reels/${encoded(libraryKey)}/${encoded(identity)}`;
}

export function demoReelRerunApi(api) {
  return (path, options) => {
    if (path === "/api/gallery/prompt-rerun/preview" && options?.body) {
      const body = JSON.parse(options.body);
      const scope = body.demo_reel;
      if (scope) {
        delete body.demo_reel;
        return api(`${demoReelPath(scope.library_key, scope.lora_identity)}/prompt-rerun/preview`, { ...options, body: JSON.stringify(body) });
      }
    }
    return api(path, options);
  };
}

export function expandDemoReelPrompt(prompt, subject) {
  return String(prompt || "").replaceAll("{subject}", () => subject || "");
}

export function moveDemoReelSlot(slots, id, destination) {
  const index = slots.findIndex((slot) => slot.id === id);
  if (index < 0 || destination < 0 || destination >= slots.length) return slots;
  const result = [...slots];
  result.splice(destination, 0, result.splice(index, 1)[0]);
  return result;
}

// The Demo Reel is the main viewport's grouped gallery wearing a dialog: one
// collapsible group per prompt slot, real image cards in the same responsive
// grid, and every action revealed on the card instead of written underneath it.
const slotLabel = (slot, index) => String(slot.title || "").trim() || `Prompt ${index + 1}`;
const slotEntries = (view, slot) => (view.entries || []).filter((entry) => entry.slot_id === slot.id);

function reelStatusText(view) {
  return [
    `${view.completed || 0} of ${view.expected || 0} images`,
    view.status,
    view.rating_username ? `A/B grades from ${view.rating_username}` : "",
  ].filter(Boolean).join(" · ");
}

// The slider is the LoRA library's, wearing this dialog: 50% to 150% of this browser's Gallery
// scale, so 100% is exactly the width the main viewport shows and the reel stays the same grid.
// The chosen size belongs to the browser that chose it, like the gallery scale and the library's.
export function demoReelHeaderMarkup(view, thumbnailScale = 100) {
  const manage = view.can_manage
    ? `<button type="button" class="icon-button lm-header-button" data-demo-action="settings" aria-label="Edit Demo Reel" title="Edit Demo Reel">${icon("reel")}</button>`
    : "";
  return `<header class="dialog-header lm-dialog-header demo-reel-header"><div class="demo-reel-header-copy"><h2 id="demo-reel-title">${escape(view.label)} · Demo Reel</h2><p role="status" data-demo-reel-status>${escape(reelStatusText(view))}</p></div><label class="lm-scale-control"><span>Thumbnail size</span><input type="range" min="50" max="150" step="5" value="${thumbnailScale}" data-demo-reel-scale aria-label="Demo Reel thumbnail size" aria-valuetext="${thumbnailScale}%" /><output data-demo-reel-scale-value>${thumbnailScale}%</output></label><div class="lm-header-actions">${manage}<button type="button" class="icon-button lm-close-button" data-demo-action="close" aria-label="Close Demo Reel" title="Close Demo Reel">${icon("close")}</button></div></header>`;
}

function slotGroupMarkup(view, slot, index, collapsed) {
  const entries = slotEntries(view, slot);
  if (!entries.length) return "";
  const id = escape(slot.id);
  const closed = collapsed.has(slot.id);
  const label = slotLabel(slot, index);
  const prompt = expandDemoReelPrompt(slot.prompt, view.subject);
  const cards = entries.map((entry) => (entry.generation
    ? demoReelGalleryCardMarkup(entry.generation, { checkpointTiers: view.checkpointTiers })
    : demoReelPlaceholderCardMarkup(entry))).join("");
  return `<section class="prompt-group demo-reel-slot-group" data-demo-slot-group="${id}" aria-label="${escape(`${label}, ${entries.length} Demo Reel images`)}"><header class="prompt-group-header"><button type="button" class="prompt-group-collapse" data-demo-action="toggle-slot" data-demo-id="${slot.id}" aria-expanded="${String(!closed)}" aria-controls="demo-reel-slot-${id}" aria-label="${closed ? "Expand" : "Collapse"} ${escape(label)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg></button><span class="demo-reel-slot-copy" title="${escape(prompt)}">${escape(label)}</span><span class="prompt-group-rule" aria-hidden="true"></span><span class="prompt-group-count">${entries.length} ${entries.length === 1 ? "image" : "images"}</span></header><div class="prompt-group-grid" id="demo-reel-slot-${id}" ${closed ? "hidden" : ""}>${cards}</div></section>`;
}

export function demoReelGroupsMarkup(view) {
  const collapsed = view.collapsed || new Set();
  const groups = (view.slots || []).map((slot, index) => slotGroupMarkup(view, slot, index, collapsed)).filter(Boolean).join("");
  // A LoRA that has been taken out of the Demo Reel is not waiting for anything, so it must not
  // be told images are still five minutes away.
  const empty = groups ? "" : view.status === "off"
    ? '<section class="empty-gallery"><h2>Demo Reel is off for this LoRA.</h2><p>Its images were removed. An administrator can turn the Demo Reel back on from this LoRA in the LoRA library.</p></section>'
    : '<section class="empty-gallery"><h2>No Demo Reel images yet.</h2><p>Images appear after prompts, a generation recipe, and A/B checkpoints are configured, then all users have been idle for five minutes.</p></section>';
  const hasMore = view.next_offset !== null && view.next_offset !== undefined;
  return `${groups}${empty}<div class="gallery-sentinel demo-reel-sentinel" data-demo-reel-sentinel${hasMore ? "" : " hidden"}>${hasMore ? '<button type="button" class="button secondary" data-demo-action="more">Load more</button>' : ""}</div>`;
}

export function demoReelShellMarkup(view, thumbnailScale = 100) {
  return `<div class="dialog-frame demo-reel-frame">${demoReelHeaderMarkup(view, thumbnailScale)}<p class="demo-reel-notice form-error" role="alert" data-demo-reel-notice${view.message ? "" : " hidden"}>${escape(view.message || "")}</p><div class="demo-reel-content" data-demo-reel-content>${demoReelGroupsMarkup(view)}</div></div>`;
}

export function demoReelGalleryMarkup(view, thumbnailScale = 100) {
  return demoReelShellMarkup(view, thumbnailScale);
}


// Controls the Demo Reel never asks the recipe for. The first three are rewritten
// for every image by the server (slot prompt, fresh seed, the target LoRA) and the
// checkpoint is chosen by A/B grades, so offering them as editable settings would
// only produce values that are silently thrown away.
const reelOwnedTypes = new Set(["image", "image_upload", "mask_upload", "lora_stack", "seed"]);
const checkpointRoles = new Set(["model", "checkpoint"]);

const isCheckpointControl = (control) => checkpointRoles.has(control.semantic_role) || control.id === "checkpoint";
const recipeInputs = (detail) => (detail?.interface?.inputs || []).filter((control) => !reelOwnedTypes.has(control.type) && control.semantic_role !== "positive_prompt");
const recipeControls = (detail) => sortInterfaceInputs(recipeInputs(detail).filter((control) => !isCheckpointControl(control)));
const dimensionControls = (detail) => recipeInputs(detail).filter((control) => ["width", "height"].includes(control.semantic_role));
const recipeValues = (controls, recipe) => Object.fromEntries(controls.map((control) => [control.id, recipe?.parameters?.[control.id] ?? control.default]));
const ownedFacts = (config) => [
  ["Prompt", "each prompt slot, with {subject} replaced by the LoRA trigger phrase"],
  ["Seed", "a new random seed for every image"],
  ["LoRAs", "only this image's LoRA, at the strength set above"],
  // With nothing graded A or B there is no list to point at, and a fact that points at a card
  // that was never drawn explains nothing.
  ["Checkpoint", `${checkpointRows(config).length ? "one image for each checkpoint ticked below" : "nothing until a checkpoint is graded A or B"} · LoRA pictures use ${thumbnailCheckpointFact(config)}`],
];
// The graded checkpoints a Demo Reel may draw from, in the order the server says to list them: the
// rating account's A then B. Being listed is not being developed — that is
// `qualifying_checkpoint_ids`, which is what the list below lets someone choose.
const checkpointRows = (config) => config.thumbnail_checkpoint_choices || [];
const developedIds = (config) => new Set(config.qualifying_checkpoint_ids ?? checkpointRows(config).map((choice) => choice.id));
// Only a developed checkpoint has an image to be a picture, so a checkpoint left out of the list
// is not on offer here either.
const thumbnailChoices = (config) => checkpointRows(config).filter((choice) => developedIds(config).has(choice.id));
const pictureCheckpoint = (config) => checkpointRows(config).find((choice) => choice.id === config.thumbnail_checkpoint_id);
// A saved picture choice can stop being usable in two different ways, and the settings say which:
// a checkpoint left out of the developed list is one tick away from working again, while one the
// rating account no longer ranks A or B needs a different account or a different checkpoint. A
// choice that is merely waiting for its first image keeps its label and no complaint.
const thumbnailCheckpointFact = (config) => {
  if (!config.thumbnail_checkpoint_id) return "the highest-ranked checkpoint, unless a first-slot image is favorited";
  const choice = pictureCheckpoint(config);
  if (!choice) return `${config.thumbnail_checkpoint_id}, which is no longer available`;
  return developedIds(config).has(choice.id) ? choice.label : `${choice.label}, which is not being developed`;
};

function statusMarkup(config) {
  const states = config.progress?.states || {};
  const paused = config.saved_paused ?? config.paused;
  const [tone, word] = paused
    ? ["is-drifted", "paused"]
    : states.running
      ? ["is-busy", "generating"]
      : states.blocked
        ? ["is-alert", "blocked"]
        : states.failed
          ? ["is-alert", "failed"]
          : config.progress?.expected && config.progress.completed === config.progress.expected
            ? ["is-synced", "complete"]
            : ["", "waiting"];
  const progress = config.progress ? `${config.progress.completed} of ${config.progress.expected} images complete · ${word}` : word;
  return `<span class="lm-status-chip ${tone}" role="status" data-demo-settings-status>${escape(progress)}</span>`;
}

// One labelled row per model family that publishes width and height, so a slot can
// keep the family size or override it, with the inherited value in plain sight.
function resolutionRowMarkup(slot, config, details) {
  const families = (config.libraries || [])
    .map((library) => ({ library, recipe: config.recipes[library.key], dimensions: dimensionControls(details[config.recipes[library.key]?.source_key]) }))
    .filter((entry) => entry.recipe && entry.dimensions.length);
  if (!families.length) return "";
  const rows = families.map(({ library, recipe, dimensions }) => {
    const value = slot.resolutions?.[library.key] || {};
    const custom = dimensions.some((control) => value[control.id] !== undefined);
    const defaults = dimensions.map((control) => recipe.parameters?.[control.id] ?? control.default).join(" × ");
    const dimension = (control, index) => `${index ? '<span class="demo-reel-size-times" aria-hidden="true">×</span>' : ""}<input class="demo-reel-dim" type="number" id="demo-reel-resolution-${escape(library.key)}-${escape(slot.id)}-${escape(control.id)}" data-demo-resolution="${escape(control.id)}" data-demo-family="${escape(library.key)}" data-demo-id="${escape(slot.id)}" value="${escape(value[control.id] ?? "")}" min="${escape(control.minimum)}" max="${escape(control.maximum)}" step="${escape(control.step || 1)}" placeholder="${escape(recipe.parameters?.[control.id] ?? control.default)}" aria-label="${escape(`${library.label} ${control.label}`)}" />`;
    const state = custom
      ? `<span class="demo-reel-size-state is-custom">custom · family default ${escape(defaults)}</span>`
      : '<span class="demo-reel-size-state">inherited from the family</span>';
    return `<div class="demo-reel-size-family"><span class="demo-reel-size-name">${escape(library.label)}</span>${dimensions.map(dimension).join("")}${state}<button type="button" class="button low demo-reel-size-reset" data-demo-action="size-reset" data-demo-id="${escape(slot.id)}" data-demo-family="${escape(library.key)}" aria-label="Use the family size for ${escape(library.label)}"${custom ? "" : " hidden"}>Reset</button></div>`;
  });
  return `<div class="demo-reel-size-row"><span class="demo-reel-size-label">Image size</span>${rows.join("")}</div>`;
}

// The slot header reuses the LoRA dialog's group header classes, so its title
// and icon buttons stay identical to it by construction. Its hairline is left
// out: inside a card the tinted band already says where the header ends.
function slotMarkup(slot, index, config, details) {
  const total = config.slots.length;
  // The kind arrives without its prefix and the attribute adds it here: one prefix site is
  // what keeps a header button from being drawn with a name the dispatcher never sees.
  const move = (kind, label, disabled, extraClass = "") => `<button type="button" class="icon-button${extraClass}" data-demo-action="slot-${kind}" data-demo-id="${escape(slot.id)}" aria-label="${escape(label)}" title="${escape(label)}"${disabled ? " disabled" : ""}>${icon(kind === "up" ? "up" : kind === "down" ? "down" : "delete")}</button>`;
  return `<li class="demo-reel-slot-editor" data-demo-slot="${escape(slot.id)}">
    <header class="lm-group-heading demo-reel-slot-heading">
      <button type="button" class="icon-button demo-reel-slot-handle" draggable="true" tabindex="0" data-demo-slot-handle="${escape(slot.id)}" aria-label="Reorder slot ${index + 1}" title="Drag, or press arrow keys, to reorder">${icon("grip")}</button>
      <input class="demo-reel-slot-title" id="demo-reel-slot-title-${escape(slot.id)}" data-demo-slot-field="title" data-demo-id="${escape(slot.id)}" maxlength="120" value="${escape(slot.title)}" placeholder="Untitled slot" aria-label="Slot ${index + 1} title (optional)" />
      <div class="lm-group-actions demo-reel-slot-actions">${move("up", `Move slot ${index + 1} up`, index === 0)}${move("down", `Move slot ${index + 1} down`, index === total - 1)}${move("delete", `Delete slot ${index + 1}`, false, " is-destructive")}</div>
    </header>
    <div class="demo-reel-slot-body">
      <label class="field demo-reel-prompt-field"><span>Prompt</span><textarea rows="3" maxlength="100000" required id="demo-reel-slot-prompt-${escape(slot.id)}" data-demo-slot-field="prompt" data-demo-id="${escape(slot.id)}" placeholder="Describe the image this slot should produce">${escape(slot.prompt)}</textarea></label>
      <p class="demo-reel-slot-hint"><code>{subject}</code> becomes each LoRA's trigger phrase, so one slot serves every LoRA.</p>
      ${resolutionRowMarkup(slot, config, details)}
    </div>
  </li>`;
}

function controlsMarkup(controls, values, detail, { family = "", idPrefix = "", headings = true } = {}) {
  let group = null;
  let markup = "";
  for (const control of controls) {
    const current = String(control.group || "");
    if (current !== group) {
      if (group !== null) markup += "</div>";
      const heading = headings && current && current.trim().toLowerCase() !== "basic" ? `<h4 class="control-group-heading">${escape(current)}</h4>` : "";
      markup += `<div class="demo-reel-group" data-interface-group="${escape(current)}">${heading}`;
      group = current;
    }
    // controlMarkup already carries the label, constraints, and conditional
    // behaviour; the interface description it ignores is added below the field,
    // and a value that differs from the published default says so.
    const changed = values[control.id] !== control.default;
    markup += `<div class="demo-reel-control${changed ? " is-changed" : ""}">${controlMarkup(control, values, detail, {}, { idPrefix })}${control.description ? `<p class="demo-reel-help">${escape(control.description)}</p>` : ""}${changed ? `<button type="button" class="button low demo-reel-restore" data-demo-action="control-reset" data-demo-family="${escape(family)}" data-demo-control="${escape(control.id)}">Restore workflow default ${escape(control.default)}</button>` : ""}</div>`;
  }
  return group !== null ? `${markup}</div>` : markup;
}

function familyMarkup(library, config, details, context) {
  const recipe = config.recipes[library.key];
  const detail = details[recipe?.source_key];
  const controls = recipeControls(detail);
  const values = recipeValues(controls, recipe);
  const advanced = (control) => isAdvancedInput(control) || String(control.group || "").trim().toLowerCase() === "advanced";
  const basic = controls.filter((control) => !advanced(control));
  const extra = controls.filter(advanced);
  const changed = controls.filter((control) => values[control.id] !== control.default).length;
  const summary = recipe
    ? [
        escape(library.sources.find((source) => source.source_key === recipe.source_key)?.label || "Workflow"),
        escape(dimensionControls(detail).map((control) => values[control.id]).join("×")),
        `LoRA strength ${escape(recipe.strength)}`,
      ].filter(Boolean).join(" · ")
    : "";
  // Opened from a LoRA, this family is where its strength gets replaced, so the
  // override belongs here and is named for the LoRA it applies to.
  const saved = context.identity ? config.strengths?.[context.libraryKey]?.[context.identity] : undefined;
  const override = recipe && context.identity && library.key === context.libraryKey
    ? `<div class="demo-reel-override"><label class="field demo-reel-override-field"><span>${escape(context.label)} · strength</span><input type="number" min="0.000001" step="any" id="demo-reel-lora-strength" data-demo-lora-strength value="${escape(saved ?? "")}" placeholder="${escape(recipe.strength)}" /></label><span class="demo-reel-override-hint">Only ${escape(context.label)} uses a strength here; every other LoRA in ${escape(library.label)} uses ${escape(recipe.strength)}.</span><button type="button" class="button low demo-reel-override-reset" data-demo-action="lora-strength-reset"${saved === undefined ? " hidden" : ""}>Use the family strength</button></div>`
    : "";
  return `<section class="demo-reel-family" data-demo-recipe="${escape(library.key)}">
    <header class="lm-group-heading demo-reel-family-heading">
      <h3>${escape(library.label)}</h3>
      <span class="demo-reel-family-summary">${summary}</span>
      <div class="lm-group-actions"><label class="switch demo-reel-family-switch"><input type="checkbox" id="demo-reel-recipe-enabled-${escape(library.key)}" data-demo-recipe-enabled="${escape(library.key)}" ${recipe ? "checked" : ""} aria-label="Generate images for ${escape(library.label)}" /><span aria-hidden="true"></span><em>${recipe ? "On" : "Off"}</em></label></div>
    </header>
    ${recipe ? `<div class="demo-reel-family-body">
      <div class="demo-reel-family-grid">
        <label class="field"><span>Workflow</span><select id="demo-reel-source-${escape(library.key)}" data-demo-source="${escape(library.key)}">${library.sources.map((source) => `<option value="${escape(source.source_key)}" ${source.source_key === recipe.source_key ? "selected" : ""}>${escape(source.label)}</option>`).join("")}</select></label>
        <label class="field"><span>LoRA strength</span><input type="number" min="0.000001" step="any" required id="demo-reel-strength-${escape(library.key)}" data-demo-strength="${escape(library.key)}" value="${escape(recipe.strength)}" /></label>
      </div>
      ${override}
      <p class="demo-reel-help">Every Demo Reel image in this family is generated with this workflow. It has to publish a seed control and must not require an uploaded image.</p>
      <ul class="demo-reel-facts">${ownedFacts(config).map(([term, fact]) => `<li><strong>${escape(term)}</strong><span>${escape(fact)}</span></li>`).join("")}</ul>
      <div class="demo-reel-controls">${controlsMarkup(basic, values, detail, { family: library.key, idPrefix: `${library.key}-` })}</div>
      ${extra.length ? `<details class="demo-reel-advanced" data-demo-details="advanced-${escape(library.key)}"><summary><strong>Advanced</strong><span>${extra.length} settings · leave these alone unless a workflow author says otherwise</span></summary><div class="demo-reel-controls">${controlsMarkup(extra, values, detail, { family: library.key, idPrefix: `${library.key}-`, headings: false })}</div></details>` : ""}
      <p class="demo-reel-family-foot"><button type="button" class="button low" data-demo-action="recipe-reset" data-demo-family="${escape(library.key)}">Reset to workflow defaults</button><span class="muted">${changed ? `${changed} setting${changed === 1 ? "" : "s"} ${changed === 1 ? "differs" : "differ"} from the workflow defaults` : "Everything is at the workflow defaults"}</span></p>
    </div>` : '<p class="demo-reel-family-off">Off · no images are generated for this family, and its existing images are removed.</p>'}
  </section>`;
}

const familySummary = (config, details) => (config.libraries || [])
  .map((library) => {
    const recipe = config.recipes[library.key];
    if (!recipe) return `${library.label} off`;
    const size = dimensionControls(details[recipe.source_key]).map((control) => recipe.parameters?.[control.id] ?? control.default).join("×");
    return `${library.label} on (${size ? `${size} · ` : ""}strength ${recipe.strength})`;
  })
  .join(" · ");

// Half of every Demo Reel image is chosen here: the prompt slots say what an image is about, this
// says which checkpoints it is made with. A checkpoint left out stops being developed and its
// images go, which is the same rule an administrator already knows from switching a model family
// or a single LoRA off, so it is said on the card rather than discovered after a Save.
function checkpointRowMarkup(config, choice) {
  const developing = developedIds(config).has(choice.id);
  const note = choice.note
    ? `<span class="demo-reel-checkpoint-note is-problem">${escape(choice.note)}</span>`
    : developing
      ? `<span class="demo-reel-checkpoint-note">${choice.images ? `${choice.images} ${choice.images === 1 ? "image" : "images"}` : "nothing yet · next in the queue"}</span>`
      : "";
  const picture = choice.id === config.thumbnail_checkpoint_id;
  // One label wraps the row, so the whole line is the hit area and the checkbox is announced with
  // its checkpoint, its grade, and the family that publishes it rather than as "checkbox".
  const name = [choice.label, choice.tier && `tier ${choice.tier}`, choice.library_label].filter(Boolean).join(", ");
  return `<li class="demo-reel-checkpoint-row${developing ? "" : " is-off"}">
    <label class="demo-reel-checkpoint-pick"><input type="checkbox" data-demo-checkpoint="${escape(choice.id)}" aria-label="${escape(name)}"${developing ? " checked" : ""} /><span class="demo-reel-checkpoint-tier" data-tier="${escape(choice.tier || "")}">${escape(choice.tier || "?")}</span><span class="demo-reel-checkpoint-name">${escape(choice.label)}</span><span class="demo-reel-checkpoint-family">${escape(choice.library_label || "")}</span><span class="demo-reel-checkpoint-aside">${note}${picture ? `<span class="demo-reel-checkpoint-picture"${developing ? "" : " data-stale"}>${icon("reel")}<span>LoRA picture${developing ? "" : " · not being developed"}</span></span>` : ""}</span></label>
  </li>`;
}

function checkpointCardMarkup(config) {
  const rows = checkpointRows(config);
  // With nothing graded A or B there is no list to edit and nothing is developed, which the
  // grading choice below already explains; an empty card here would only repeat it.
  if (!rows.length) return "";
  const developing = developedIds(config);
  const automatic = config.qualifying_checkpoint_ids === null || config.qualifying_checkpoint_ids === undefined;
  const perLora = developing.size * config.slots.length;
  const state = !developing.size
    ? '<p class="demo-reel-checkpoints-state is-empty">No checkpoints selected · on Save, no Demo Reel images are generated and every existing Demo Reel image is removed for everyone.</p>'
    : automatic
      ? `<p class="demo-reel-checkpoints-state">Automatic · every checkpoint ${escape(config.rating_username || "the rating account")} ranks A or B, including any graded later. Change a tick to choose the list yourself.</p>`
      : `<p class="demo-reel-checkpoints-state">Your choice · only the checkpoints ticked below are developed; the others keep no Demo Reel images. ${escape(config.rating_username || "The rating account")} still decides the order they are made and shown in.</p>`;
  return `<section class="demo-reel-checkpoints" data-demo-checkpoints>
    <header class="lm-group-heading demo-reel-checkpoints-heading">
      <h3>Develop these checkpoints</h3>
      <span class="demo-reel-checkpoints-count">${escape(`${developing.size} of ${rows.length} selected · ${perLora} image${perLora === 1 ? "" : "s"} per LoRA`)}</span>
      <div class="lm-group-actions"><button type="button" class="button low demo-reel-checkpoint-tool" data-demo-action="checkpoint-all">Select all</button><button type="button" class="button low demo-reel-checkpoint-tool" data-demo-action="checkpoint-none">Clear</button></div>
    </header>
    ${state}
    <ul class="demo-reel-checkpoint-list">${rows.map((choice) => checkpointRowMarkup(config, choice)).join("")}</ul>
    <p class="demo-reel-checkpoints-foot"><button type="button" class="button low" data-demo-action="checkpoint-auto"${automatic ? " hidden" : ""}>Restore automatic · every A and B checkpoint</button><span class="demo-reel-help">Unchecking a checkpoint removes the Demo Reel images it made for every LoRA, the same as switching a model family off. Nothing else is regenerated or invalidated.</span></p>
  </section>`;
}

function generationMarkup(config, details, users, context) {
  const idle = Math.max(1, Math.round((config.idle_seconds || 300) / 60));
  // A LoRA switched off sits on the LoRA card, not in these settings, so the settings say that
  // far away LoRAs are excluded rather than leaving the count to be discovered image by image.
  const excluded = Number(config.disabled_reels) || 0;
  const excludedNote = excluded
    ? ` ${excluded} LoRA${excluded === 1 ? "" : "s"} ${excluded === 1 ? "has" : "have"} been taken out of the Demo Reel with Disable Demo Reel in a LoRA card menu.`
    : "";
  // One choice for the whole library, because a LoRA's picture is shared by every account:
  // favorites still win, and this decides the picture for every LoRA that has none.
  const saved = config.thumbnail_checkpoint_id || "";
  const choices = thumbnailChoices(config);
  const choosable = choices.some((choice) => choice.id === saved);
  // A saved picture checkpoint that is graded but left out of the developed list still has a
  // label, and the reason it is not on offer is one the card above can fix in one tick; only a
  // checkpoint outside the grades entirely is a name with no story to tell.
  const unavailable = saved && !choosable
    ? `<option value="${escape(saved)}" selected>${escape(pictureCheckpoint(config) ? `${pictureCheckpoint(config).label} · not being developed` : `${saved} · no longer available`)}</option>`
    : "";
  const checkpointOptions = `${unavailable || `<option value=""${saved ? "" : " selected"}>Automatic · highest-ranked checkpoint</option>`}${choices.map((choice) => `<option value="${escape(choice.id)}" ${choice.id === saved ? "selected" : ""}>${escape(choice.label)}</option>`).join("")}`;
  return `<details class="demo-reel-generation" data-demo-details="generation">
    <summary class="demo-reel-generation-summary"><span class="demo-reel-generation-title">Model families</span><span class="demo-reel-generation-state">${escape(familySummary(config, details))}</span></summary>
    <p class="demo-reel-how"><strong>How this works.</strong> Prompt slots decide what is drawn. A model family decides how — the workflow, its settings, and how strongly each LoRA is applied. Images appear after everyone has been idle for ${idle} minutes.</p>
    ${checkpointCardMarkup(config)}
    <div class="demo-reel-families">${(config.libraries || []).map((library) => familyMarkup(library, config, details, context)).join("") || '<p class="demo-reel-empty">No published LoRA libraries are available.</p>'}</div>
    <div class="demo-reel-grading">
      <label class="field demo-reel-grading-user"><span>A/B ratings from</span><select id="demo-reel-rating-user" data-demo-rating-user>${users.map((user) => `<option value="${escape(user.id)}" ${user.id === config.rating_user_id ? "selected" : ""}>${escape(user.username)}</option>`).join("")}</select></label>
      <label class="field demo-reel-grading-checkpoint"><span>LoRA pictures from</span><select id="demo-reel-thumbnail-checkpoint" data-demo-thumbnail-checkpoint>${checkpointOptions}</select></label>
      <label class="switch demo-reel-pause"><input type="checkbox" id="demo-reel-paused" data-demo-paused ${config.paused ? "checked" : ""} /><span aria-hidden="true"></span><em>${config.paused ? "Paused" : "Active"}</em></label>
      <p class="demo-reel-help">Only this account's A/B grades decide which checkpoints qualify, and they set the order of the list above and of this picture choice. A checkpoint ticked there gets one image per prompt slot for every LoRA; one left out gets none, and its images go away. Naming one here only chooses which of those images the LoRA gallery shows, and changes nothing about what is generated. A favorited first-slot image always supplies that LoRA's picture instead, and a checkpoint with no image in the first slot yet falls back to the highest-ranked one until it has. Pausing stops new images until someone resumes it.${excludedNote}</p>
    </div>
  </details>`;
}

export function demoReelSettingsMarkup(config, details = {}, context = {}, users = []) {
  const idle = Math.max(1, Math.round((config.idle_seconds || 300) / 60));
  const slots = config.slots.map((slot, index) => slotMarkup(slot, index, config, details)).join("");
  return `<form class="dialog-frame demo-reel-settings-frame" data-demo-settings-form>
    <header class="dialog-header demo-reel-settings-header">
      <div class="demo-reel-header-copy"><h2 id="demo-reel-settings-title">Demo Reel settings</h2><p>Shared prompts · every installed LoRA gets one image per slot</p></div>
      <div class="demo-reel-header-side">${statusMarkup(config)}<button type="button" class="icon-button lm-close-button" data-demo-action="cancel-settings" aria-label="Close Demo Reel settings" title="Close Demo Reel settings">${icon("close")}</button></div>
    </header>
    <p class="demo-reel-notice form-error" role="alert" data-demo-settings-error${config.error ? "" : " hidden"}>${escape(config.error || "")}</p>
    <div class="demo-reel-content">
      <section class="demo-reel-section">
        <header class="demo-reel-section-heading">
          <h3>Prompt slots</h3>
          <span class="demo-reel-section-count">${config.slots.length} slot${config.slots.length === 1 ? "" : "s"}</span>
          <div class="lm-group-actions"><button type="button" class="button secondary demo-reel-add" data-demo-action="slot-add">${icon("plus")}Add prompt slot</button></div>
        </header>
        <ol class="demo-reel-slots">${slots}</ol>
        ${slots ? "" : '<p class="demo-reel-empty">No prompt slots yet. Add one to start generating.</p>'}
      </section>
      ${generationMarkup(config, details, users, context)}
    </div>
    <footer class="dialog-actions"><span class="muted">Saves after ${idle} quiet minutes across all accounts. Prompt and size changes replace affected images.</span><button type="button" class="button secondary" data-demo-action="cancel-settings">Cancel</button><button type="submit" class="button primary">Save</button></footer>
  </form>`;
}

export function createDemoReels({ root, api, isAdmin, user, notify, onRerun = () => {}, onChanged = () => {}, onClose = () => {}, checkpointTiers = () => ({}), preserveHover = (update) => update() }) {
  const gallery = document.createElement("dialog");
  gallery.id = "demo-reel-dialog";
  gallery.className = "demo-reel-dialog";
  gallery.setAttribute("aria-labelledby", "demo-reel-title");
  const editor = document.createElement("dialog");
  editor.id = "demo-reel-settings-dialog";
  editor.className = "demo-reel-settings-dialog";
  editor.setAttribute("aria-labelledby", "demo-reel-settings-title");
  const transfer = document.createElement("dialog");
  transfer.className = "demo-reel-transfer-dialog";
  transfer.setAttribute("aria-label", "Choose destination collection");
  root.append(gallery, editor, transfer);
  let view = null, context = {}, settings = null, users = [], timer = null, loading = false, revision = 0, dragId = null, transferDraft = null;
  let thumbnails = null, paging = null, headerMarkup = "";
  // The card size this browser chose for this dialog, held as a multiplier of that browser's
  // Gallery scale. It is written to the dialog element itself, which outlives every render of the
  // grid inside it, so a refresh or a reopen cannot lose it.
  let thumbnailScale = 100;
  const scaleStorageKey = "cif.demo-reel-thumbnail-scale.v1";
  try { thumbnailScale = normalizeThumbnailScale(localStorage.getItem(scaleStorageKey)); } catch { /* The slider still works when browser storage is unavailable. */ }
  const applyThumbnailScale = () => gallery.style.setProperty("--demo-reel-card-scale", String(thumbnailScale / 100));
  applyThumbnailScale();
  const collapsed = new Set();
  const details = {};
  const items = () => (view?.entries || []).flatMap((entry) => entry.generation ? [entry.generation] : []);
  const find = (id) => items().find((item) => item.id === id);
  const path = () => demoReelPath(context.libraryKey, context.identity);
  const contentHost = () => gallery.querySelector("[data-demo-reel-content]");
  // Collapsed slots and the checkpoint ranks are the dialog's own context: the
  // markup stays a pure function of the view by carrying them along with it.
  const marked = () => ({ ...view, collapsed, checkpointTiers: checkpointTiers() });
  // A cheap fingerprint of everything the grid draws. The five-second poll is
  // otherwise a re-render every five seconds under a resting cursor.
  const signature = () => JSON.stringify([
    view?.completed, view?.expected, view?.status, view?.next_offset, view?.message, view?.can_manage, view?.rating_username,
    (view?.slots || []).map((slot) => [slot.id, slot.title, slot.prompt]),
    (view?.entries || []).map((entry) => [entry.id, entry.slot_id, entry.state, entry.error, entry.generation?.id, entry.generation?.status, entry.generation?.is_favorite]),
  ]);
  // The gallery's cards load thumbnails through the authenticated scheduler, whose
  // observers watch the dialog's own scrolling element rather than the page.
  function mountThumbnails() {
    thumbnails?.();
    thumbnails = installThumbnails(contentHost(), { scheduler: createThumbnailScheduler() });
  }
  // Paging follows the main viewport: a sentinel near the bottom of the scroll area
  // loads the next page, and the Load more button stays for a browser without
  // IntersectionObserver.
  function observePaging() {
    paging?.disconnect();
    paging = null;
    const sentinel = contentHost()?.querySelector("[data-demo-reel-sentinel]");
    if (!sentinel || sentinel.hidden || !("IntersectionObserver" in window)) return;
    paging = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void refresh(true);
    }, { root: contentHost(), rootMargin: "600px" });
    paging.observe(sentinel);
  }
  function syncHeader() {
    const notice = gallery.querySelector("[data-demo-reel-notice]");
    if (notice) { notice.hidden = !view.message; notice.textContent = view.message || ""; }
    const markup = demoReelHeaderMarkup(marked(), thumbnailScale);
    const header = gallery.querySelector(".demo-reel-header");
    if (!header || markup === headerMarkup) { headerMarkup = markup; return; }
    const focused = header.contains(document.activeElement) ? document.activeElement.dataset.demoAction : null;
    const sliding = header.contains(document.activeElement) && document.activeElement.matches("[data-demo-reel-scale]");
    headerMarkup = markup;
    header.outerHTML = markup;
    if (focused) gallery.querySelector(`.demo-reel-header [data-demo-action="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
    // A poll that lands between two keystrokes of someone setting the size must not hand the
    // slider away; the replaced input is the same size, so focus simply goes back to it.
    else if (sliding) gallery.querySelector(".demo-reel-header [data-demo-reel-scale]")?.focus({ preventScroll: true });
  }
  // The dialog keeps its shell, its scroller, and its thumbnail observers: a refresh
  // replaces the grid only when the fingerprint moved, and hands the hover state and
  // keyboard position to the gallery's own preservation.
  function render() {
    if (!view) return;
    const content = contentHost();
    if (!content) {
      headerMarkup = demoReelHeaderMarkup(marked(), thumbnailScale);
      gallery.innerHTML = demoReelShellMarkup(marked(), thumbnailScale);
    } else {
      const scroll = content.scrollTop;
      preserveHover(() => { content.innerHTML = demoReelGroupsMarkup(marked()); });
      content.scrollTop = scroll;
    }
    // Closing the dialog disposes the thumbnail install along with its observers,
    // while the shell and its scroller stay in the DOM: so the grid that the next
    // open draws is observed only if this open re-installs it.
    if (!thumbnails) mountThumbnails();
    syncHeader();
    observePaging();
  }
  const caret = (element) => {
    try {
      return [element.selectionStart, element.selectionEnd];
    } catch {
      return [null, null];
    }
  };
  const captureSettings = () => ({
    scroll: editor.querySelector(".demo-reel-content")?.scrollTop || 0,
    open: new Map([...editor.querySelectorAll("details[data-demo-details]")].map((node) => [node.dataset.demoDetails, node.open])),
    focus: editor.contains(document.activeElement) && document.activeElement.id ? document.activeElement.id : null,
    caret: editor.contains(document.activeElement) ? caret(document.activeElement) : [null, null],
  });
  const restoreSettings = (snapshot) => {
    const content = editor.querySelector(".demo-reel-content");
    if (content) content.scrollTop = snapshot.scroll;
    for (const [key, open] of snapshot.open) {
      const node = editor.querySelector(`details[data-demo-details="${CSS.escape(key)}"]`);
      if (node) node.open = open;
    }
    if (!snapshot.focus) return;
    const node = document.getElementById(snapshot.focus);
    if (!node || !editor.contains(node)) return;
    node.focus();
    if (node.setSelectionRange && snapshot.caret[0] !== null) node.setSelectionRange(snapshot.caret[0], snapshot.caret[1]);
  };
  // Re-rendering the whole editor is the simplest way to keep markup and state in
  // step, so everything a person was busy with - how far they scrolled, which
  // disclosures they opened, which field they were typing in - is put back after.
  const renderSettings = (options = {}) => {
    if (!settings) return;
    const snapshot = captureSettings();
    editor.innerHTML = demoReelSettingsMarkup(settings, details, context, users);
    restoreSettings(snapshot);
    if (options.focus) {
      const node = document.getElementById(options.focus);
      if (node) {
        node.focus();
        if (node.setSelectionRange) node.setSelectionRange(node.value.length, node.value.length);
      }
    }
  };
  const clearSettingsError = () => {
    if (!settings?.error) return;
    settings.error = "";
    const notice = editor.querySelector("[data-demo-settings-error]");
    if (notice) { notice.hidden = true; notice.textContent = ""; }
  };
  async function loadRecipe(sourceKey) {
    details[sourceKey] = await api(`/api/workflows/${encoded(sourceKey)}`);
  }
  async function refresh(append = false) {
    if (loading || !gallery.open) return;
    loading = true;
    const token = revision;
    const before = signature();
    const offset = append ? view?.next_offset : 0;
    if (append && offset == null) { loading = false; return; }
    try {
      const next = await api(`${path()}?offset=${offset || 0}&limit=60`);
      if (token !== revision || !gallery.open) return;
      if (append) {
        const seen = new Set();
        next.entries = [...view.entries, ...next.entries].filter((entry) => !seen.has(entry.id) && seen.add(entry.id));
      }
      else if (view?.entries.length > 60) {
        for (let start = 60; start < view.entries.length && start < next.expected; start += 60) {
          const page = await api(`${path()}?offset=${start}&limit=60`);
          if (token !== revision || !gallery.open) return;
          next.entries.push(...page.entries);
          next.next_offset = page.next_offset;
        }
      }
      view = next;
      // An unchanged page leaves the grid, the hover state, and the thumbnails
      // exactly where they are; only the header's status line moves.
      if (signature() === before) syncHeader();
      else render();
      onChanged();
      return true;
    } catch (error) {
      if (view) { view.message = error.message; render(); }
      else notify(error.message, "error");
      return false;
    } finally { loading = false; }
  }
  async function open(nextContext) {
    if (!gallery.isConnected) root.append(gallery, editor, transfer);
    context = nextContext;
    revision += 1;
    collapsed.clear();
    view = { ...nextContext, label: nextContext.label, subject: nextContext.subject, entries: [], slots: [], completed: 0, expected: 0, status: "loading", next_offset: null, can_manage: isAdmin() };
    render();
    if (!gallery.open) gallery.showModal();
    await refresh();
    clearInterval(timer);
    timer = setInterval(() => { if (!document.hidden && !editor.open && !transfer.open) void refresh(); }, 5000);
  }
  function close() { gallery.close(); }
  // Sizing the reel's cards is a grid change and nothing else. The cards keep their nodes, so
  // their thumbnail observers, hover state, scroll position, and paging sentinel all stay put;
  // only the dialog's own custom property and the control's read-out move. The header fingerprint
  // is rewritten to match, so the five-second poll finds the header it already drew.
  gallery.addEventListener("input", (event) => {
    if (!event.target.matches("[data-demo-reel-scale]")) return;
    thumbnailScale = normalizeThumbnailScale(event.target.value);
    event.target.setAttribute("aria-valuetext", `${thumbnailScale}%`);
    const readout = gallery.querySelector("[data-demo-reel-scale-value]");
    if (readout) readout.value = `${thumbnailScale}%`;
    applyThumbnailScale();
    headerMarkup = demoReelHeaderMarkup(marked(), thumbnailScale);
    try { localStorage.setItem(scaleStorageKey, String(thumbnailScale)); } catch { /* Keep the in-memory preference. */ }
  });
  gallery.addEventListener("close", () => {
    revision += 1;
    clearInterval(timer);
    timer = null;
    view = null;
    thumbnails?.();
    thumbnails = null;
    paging?.disconnect();
    paging = null;
    onClose();
  });
  async function openSettings(nextContext = context) {
    if (!isAdmin()) return;
    if (!editor.isConnected) root.append(gallery, editor, transfer);
    context = nextContext;
    const [saved, accounts] = await Promise.all([api("/api/admin/demo-reels"), api("/api/admin/users")]);
    settings = structuredClone(saved);
    settings.saved_paused = saved.paused;
    settings.rating_user_id ||= user()?.id;
    users = accounts;
    await Promise.all(Object.values(settings.recipes).map((recipe) => loadRecipe(recipe.source_key)));
    renderSettings();
    if (!editor.open) editor.showModal();
  }
  async function action(kind, ids) {
    if (kind === "favorite") kind = find(ids[0])?.is_favorite ? "unfavorite" : "favorite";
    await api(`${path()}/actions`, { method: "POST", body: JSON.stringify({ action: kind, generation_ids: ids, revision: view?.revision ?? null }) });
    await refresh();
    if (["delete", "move", "regenerate"].includes(kind)) notify("Removed from Demo Reel. A new random image will be generated when everyone is idle.", "success");
  }
  async function chooseTransfer(kind, id) {
    const collections = await api("/api/collections");
    transferDraft = { kind, id, key: crypto.randomUUID() };
    transfer.innerHTML = `<form class="dialog-frame" data-demo-transfer-form><h2>${kind === "move" ? "Move" : "Copy"} to collection</h2><label class="field"><span>Destination</span><select name="collection_id"><option value="">Gallery root</option>${collections.map((collection) => `<option value="${escape(collection.id)}">${escape(collection.name)}</option>`).join("")}</select></label><p data-demo-transfer-error class="form-error" role="alert"></p><div class="button-row"><button type="button" class="button secondary" data-demo-action="cancel-transfer">Cancel</button><button type="submit" class="button primary">${kind === "move" ? "Move" : "Copy"}</button></div></form>`;
    transfer.showModal();
  }
  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-demo-action]");
    if (!button || button.disabled) return;
    const kind = button.dataset.demoAction, id = button.dataset.demoId;
    try {
      if (kind === "close") close();
      else if (kind === "settings") await openSettings();
      else if (kind === "cancel-settings") editor.close();
      else if (kind === "cancel-transfer") transfer.close();
      else if (kind === "more") await refresh(true);
      // The image, details, favorite, recall, and delete controls are the gallery's
      // own (data-action); the reel only owns slot collapsing, its transfers, and the
      // generation-side actions the gallery has no equivalent for.
      else if (kind === "toggle-slot") {
        if (!gallery.open || !view) return;
        if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
        render();
        gallery.querySelector(`[data-demo-action="toggle-slot"][data-demo-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
      }
      else if (kind === "rerun") onRerun(id, { library_key: context.libraryKey, lora_identity: context.identity });
      else if (["copy", "move"].includes(kind)) await chooseTransfer(kind, id);
      else if (["delete", "regenerate"].includes(kind)) {
        if (window.confirm("Remove this Demo Reel image? A fresh random image will replace it during the next idle period. Personal copies are kept.")) await action(kind, [id]);
      } else if (kind === "slot-add") {
        const slot = { id: crypto.randomUUID(), title: "", prompt: "", resolutions: {} };
        settings.slots.push(slot);
        renderSettings({ focus: `demo-reel-slot-prompt-${slot.id}` });
      }
      else if (kind === "size-reset") {
        const slot = settings.slots.find((slot) => slot.id === id);
        delete slot.resolutions?.[button.dataset.demoFamily];
        renderSettings();
      }
      else if (kind === "control-reset") {
        const family = button.dataset.demoFamily;
        const control = recipeInputs(details[settings.recipes[family].source_key]).find((input) => input.id === button.dataset.demoControl);
        settings.recipes[family].parameters[control.id] = structuredClone(control.default);
        renderSettings();
      }
      else if (kind === "recipe-reset") {
        const family = button.dataset.demoFamily;
        const recipe = settings.recipes[family];
        recipe.parameters = Object.fromEntries(recipeInputs(details[recipe.source_key]).map((control) => [control.id, structuredClone(control.default)]));
        renderSettings();
      }
      else if (kind === "lora-strength-reset") {
        if (settings.strengths[context.libraryKey]) delete settings.strengths[context.libraryKey][context.identity];
        renderSettings();
      }
      else if (["checkpoint-all", "checkpoint-none", "checkpoint-auto"].includes(kind)) {
        // All and none are still a choice the administrator made; only automatic gives the list
        // back, so that a checkpoint graded next week joins without anyone remembering to widen it.
        settings.qualifying_checkpoint_ids = kind === "checkpoint-all"
          ? checkpointRows(settings).map((choice) => choice.id)
          : kind === "checkpoint-none" ? [] : null;
        renderSettings();
      }
      else if (kind === "slot-delete") {
        const index = settings.slots.findIndex((slot) => slot.id === id);
        if (index < 0) return;
        // A slot is shared, so saving its removal takes every Demo Reel image made for it out of
        // every account's gallery. That belongs on the click, not in a document nobody opens.
        if (!window.confirm(`Delete prompt slot “${slotLabel(settings.slots[index], index)}”? On Save, every Demo Reel image made for this slot is removed for everyone. Your personal copies are kept.`)) return;
        settings.slots = settings.slots.filter((slot) => slot.id !== id);
        renderSettings();
      }
      else if (["slot-up", "slot-down"].includes(kind)) {
        settings.slots = moveDemoReelSlot(settings.slots, id, settings.slots.findIndex((slot) => slot.id === id) + (kind === "slot-up" ? -1 : 1)); renderSettings();
      }
    } catch (error) { notify(error.message, "error"); }
  });
  editor.addEventListener("input", (event) => {
    const input = event.target, data = input.dataset;
    clearSettingsError();
    if (data.demoSlotField) {
      const slot = settings.slots.find((slot) => slot.id === data.demoId);
      slot[data.demoSlotField] = input.value;
    } else if (data.controlId) {
      const family = input.closest("[data-demo-recipe]")?.dataset.demoRecipe;
      settings.recipes[family].parameters[data.controlId] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
    } else if (data.demoStrength) settings.recipes[data.demoStrength].strength = Number(input.value);
    else if (input.matches("[data-demo-paused]")) settings.paused = input.checked;
    else if (input.matches("[data-demo-rating-user]")) settings.rating_user_id = input.value;
    else if (input.matches("[data-demo-thumbnail-checkpoint]")) settings.thumbnail_checkpoint_id = input.value || null;
    else if (input.matches("[data-demo-lora-strength]")) {
      settings.strengths[context.libraryKey] ||= {};
      if (input.value === "") delete settings.strengths[context.libraryKey][context.identity];
      else settings.strengths[context.libraryKey][context.identity] = Number(input.value);
    } else if (input.matches("[data-demo-checkpoint]")) {
      // One tick moves from the automatic state to a chosen list, and from a chosen list back to
      // nothing chosen; the order stored is always the grading account's, never the click order.
      const picked = developedIds(settings);
      const identity = input.dataset.demoCheckpoint;
      if (input.checked) picked.add(identity);
      else picked.delete(identity);
      settings.qualifying_checkpoint_ids = checkpointRows(settings).map((choice) => choice.id).filter((id) => picked.has(id));
      renderSettings();
      editor.querySelector(`[data-demo-checkpoint="${CSS.escape(identity)}"]`)?.focus();
    } else if (data.demoResolution) {
      const slot = settings.slots.find((slot) => slot.id === data.demoId);
      slot.resolutions ||= {}; slot.resolutions[data.demoFamily] ||= {};
      if (input.value === "") delete slot.resolutions[data.demoFamily][data.demoResolution];
      else slot.resolutions[data.demoFamily][data.demoResolution] = Number(input.value);
      if (!Object.keys(slot.resolutions[data.demoFamily]).length) delete slot.resolutions[data.demoFamily];
    }
  });
  editor.addEventListener("change", async (event) => {
    const input = event.target;
    const family = input.dataset.demoRecipeEnabled || input.dataset.demoSource;
    if (!family) return;
    clearSettingsError();
    try {
      if (input.matches("[data-demo-recipe-enabled]") && !input.checked) delete settings.recipes[family];
      else {
        const sourceKey = input.dataset.demoSource ? input.value : settings.libraries.find((entry) => entry.key === family).sources[0].source_key;
        await loadRecipe(sourceKey);
        settings.recipes[family] = { source_key: sourceKey, strength: settings.recipes[family]?.strength || 1, parameters: Object.fromEntries(recipeInputs(details[sourceKey]).map((control) => [control.id, structuredClone(control.default)])) };
      }
      renderSettings();
    } catch (error) { notify(error.message, "error"); }
  });
  editor.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = editor.querySelector('[type="submit"]');
    // The only settings change here that deletes something somebody else is looking at, and the
    // card has been saying so since the last tick; an empty list still gets one more question.
    if (!developedIds(settings).size && !window.confirm("No checkpoints are selected. Saving removes every Demo Reel image for every account and generates nothing new until a checkpoint is selected. Save anyway?")) return;
    button.disabled = true;
    try {
      const payload = Object.fromEntries(["revision", "paused", "rating_user_id", "thumbnail_checkpoint_id", "qualifying_checkpoint_ids", "slots", "recipes", "strengths"].map((key) => [key, settings[key]]));
      await api("/api/admin/demo-reels", { method: "PUT", body: JSON.stringify(payload) });
      editor.close();
      notify(`Demo Reel settings saved. Generation waits for ${Math.max(1, Math.round((settings.idle_seconds || 300) / 60))} quiet minutes.`, "success");
      await refresh();
    } catch (error) { settings.error = error.message; renderSettings(); }
    finally { button.disabled = false; }
  });
  editor.addEventListener("keydown", (event) => {
    const handle = event.target.closest("[data-demo-slot-handle]");
    if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const id = handle.dataset.demoSlotHandle;
    settings.slots = moveDemoReelSlot(settings.slots, id, settings.slots.findIndex((slot) => slot.id === id) + (event.key === "ArrowUp" ? -1 : 1));
    renderSettings(); editor.querySelector(`[data-demo-slot-handle="${CSS.escape(id)}"]`)?.focus();
  });
  editor.addEventListener("dragstart", (event) => {
    const handle = event.target.closest("[data-demo-slot-handle]");
    if (handle) { dragId = handle.dataset.demoSlotHandle; event.dataTransfer.setData("text/plain", dragId); }
  });
  editor.addEventListener("dragover", (event) => { if (dragId && event.target.closest("[data-demo-slot]")) event.preventDefault(); });
  editor.addEventListener("drop", (event) => {
    const row = event.target.closest("[data-demo-slot]");
    if (!dragId || !row) return;
    event.preventDefault(); settings.slots = moveDemoReelSlot(settings.slots, dragId, settings.slots.findIndex((slot) => slot.id === row.dataset.demoSlot)); dragId = null; renderSettings();
  });
  editor.addEventListener("dragend", () => { dragId = null; });
  transfer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = transfer.querySelector('[type="submit"]'); button.disabled = true;
    try {
      await api(`${path()}/actions`, { method: "POST", body: JSON.stringify({ action: transferDraft.kind, generation_ids: [transferDraft.id], collection_id: new FormData(event.target).get("collection_id") || null, key: transferDraft.key }) });
      transfer.close(); await refresh(); notify("Image saved to your collection.", "success");
    } catch (error) { transfer.querySelector("[data-demo-transfer-error]").textContent = error.message; }
    finally { button.disabled = false; }
  });
  return { open, close, openSettings, action, find, items, path, refresh, loadMore: () => refresh(true), hasMore: () => view?.next_offset != null, isOpen: () => gallery.open };
}
