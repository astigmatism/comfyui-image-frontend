// Interactive design preview for the Demo Reel settings redesign.
//
// The proposed design is the production demoReelSettingsMarkup() and
// src/styles.css, so this preview cannot drift from the application.
// `layout=current` takes the same fixture and swaps in the retired slot list and
// recipe grid, kept below as a frozen snapshot for the comparison.

import { demoReelSettingsMarkup, expandDemoReelPrompt } from "../src/demo-reels.mjs";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const params = new URLSearchParams(location.search);

// ---------------------------------------------------------------- fixtures ---
// A published text-to-image workflow with the metadata shape the API actually
// sends: description, group, order, advanced, semantic_role, constraints.
const kreaWorkflow = {
  source_key: "moody-krea2-simple",
  display_name: "Moody Krea2 Simple v31",
  interface: {
    inputs: [
      { id: "prompt", type: "string", label: "Prompt", semantic_role: "positive_prompt", required: true, group: "Basic", order: 10 },
      { id: "loras", type: "lora_stack", label: "LoRAs", group: "Basic", order: 15, items: [] },
      { id: "seed", type: "seed", label: "Seed", group: "Basic", order: 20 },
      { id: "checkpoint", type: "choice", label: "Checkpoint", semantic_role: "model", group: "Basic", order: 25, default: "v4", choices: [{ value: "v4", label: "Moody Krea2 V4 INT8" }, { value: "v5", label: "Moody Krea2 V5 BF16" }] },
      { id: "width", type: "integer", label: "Width", semantic_role: "width", required: true, group: "Basic", order: 30, default: 768, minimum: 256, maximum: 2048, step: 8, description: "Shorter edge of the final image." },
      { id: "height", type: "integer", label: "Height", semantic_role: "height", required: true, group: "Basic", order: 31, default: 1024, minimum: 256, maximum: 2048, step: 8, description: "Taller edge of the final image." },
      { id: "steps", type: "integer", label: "Steps", group: "Basic", order: 40, default: 28, minimum: 4, maximum: 60, description: "Sampling steps. Above ~35 the image changes very little and each image takes longer." },
      { id: "guidance", type: "number", label: "Guidance", group: "Basic", order: 41, default: 4.5, minimum: 1, maximum: 12, step: 0.1, description: "How closely the image follows the prompt. Higher is stricter and less varied." },
      { id: "sampler", type: "choice", label: "Sampler", group: "Advanced", order: 50, default: "euler_a", choices: [{ value: "euler_a", label: "Euler a" }, { value: "dpmpp_2m", label: "DPM++ 2M" }] },
      { id: "scheduler", type: "choice", label: "Scheduler", group: "Advanced", order: 51, default: "normal", choices: [{ value: "normal", label: "Normal" }, { value: "karras", label: "Karras" }] },
      { id: "denoise", type: "number", label: "Denoise", group: "Advanced", order: 52, default: 0.72, minimum: 0, maximum: 1, step: 0.01 },
      { id: "clip_skip", type: "integer", label: "CLIP skip", advanced: true, group: "Advanced", order: 53, default: -2, minimum: -12, maximum: -1 },
      { id: "noise_bias", type: "number", label: "Composition bias", advanced: true, group: "Advanced", order: 54, default: 0, minimum: -0.4, maximum: 0.4, step: 0.01, description: "Shifts framing between subject-centred and wide. Leave at 0 unless a workflow author asks." },
      { id: "tiling", type: "boolean", label: "Tiling", advanced: true, group: "Advanced", order: 55, default: false, description: "Makes opposite edges match so the image repeats seamlessly." },
      { id: "output_format", type: "choice", label: "File format", advanced: true, group: "Advanced", order: 56, default: "png", choices: [{ value: "png", label: "PNG" }, { value: "webp", label: "WebP" }] },
    ],
  },
};

const fluxWorkflow = {
  source_key: "flux-dev-hires",
  display_name: "Flux Dev 1.1 Hires",
  interface: {
    inputs: [
      { id: "prompt", type: "string", label: "Prompt", semantic_role: "positive_prompt", required: true, group: "Basic", order: 10 },
      { id: "loras", type: "lora_stack", label: "LoRAs", group: "Basic", order: 15, items: [] },
      { id: "seed", type: "seed", label: "Seed", group: "Basic", order: 20 },
      { id: "width", type: "integer", label: "Width", semantic_role: "width", required: true, group: "Basic", order: 30, default: 1024, minimum: 512, maximum: 2048, step: 16 },
      { id: "height", type: "integer", label: "Height", semantic_role: "height", required: true, group: "Basic", order: 31, default: 1024, minimum: 512, maximum: 2048, step: 16 },
      { id: "steps", type: "integer", label: "Steps", group: "Basic", order: 40, default: 20, minimum: 4, maximum: 50 },
    ],
  },
};

const details = { [kreaWorkflow.source_key]: kreaWorkflow, [fluxWorkflow.source_key]: fluxWorkflow };

const configFixture = () => ({
  revision: 12,
  paused: false,
  idle_seconds: 300,
  rating_user_id: "u1",
  rating_username: "admin",
  progress: { completed: 6, expected: 9, states: { ready: 6, running: 1, blocked: 0, failed: 0, missing: 2 } },
  slots: [
    { id: "s1", title: "Portrait study", prompt: "a cinematic portrait of {subject}, 85mm, soft window light", resolutions: {} },
    { id: "s2", title: "", prompt: "{subject} beside a lake at golden hour, shallow depth of field", resolutions: { krea2: { width: 1024 } } },
    { id: "s3", title: "Studio product", prompt: "{subject} on a seamless backdrop, product photography, softbox reflections", resolutions: {} },
  ],
  recipes: {
    krea2: {
      source_key: kreaWorkflow.source_key,
      strength: 0.85,
      parameters: { width: 768, height: 1024, steps: 34, guidance: 4.5, sampler: "euler_a", scheduler: "normal", denoise: 0.72, clip_skip: -2, noise_bias: 0, tiling: false, output_format: "png" },
    },
    flux: {
      source_key: fluxWorkflow.source_key,
      strength: 0.9,
      parameters: { width: 1024, height: 1024, steps: 20 },
    },
  },
  strengths: { krea2: { "lr1_a": 1.15 } },
  // `picout=on` is the state where the checkpoint that supplies every LoRA
  // picture has been left out of the list: the picture falls back, and the
  // picker has to say why its own saved value is not on offer.
  thumbnail_checkpoint_id: params.get("picout") === "on" ? checkpointIdentity("krea2fast") : checkpointIdentity("krea2v5"),
  thumbnail_checkpoint_choices: checkpointUniverse(),
  qualifying_checkpoint_ids: selectionFor(params.get("cps") || "some"),
  libraries: [
    { key: "krea2", label: "Krea 2", sources: [{ source_key: kreaWorkflow.source_key, label: kreaWorkflow.display_name }, { source_key: "moody-krea2-fast", label: "Moody Krea2 Fast v4" }] },
    { key: "flux", label: "Flux Dev", sources: [{ source_key: fluxWorkflow.source_key, label: fluxWorkflow.display_name }] },
  ],
});

const context = { libraryKey: "krea2", identity: "lr1_a", label: "Heroline Ember", subject: "heroline ember" };
const users = [{ id: "u1", username: "admin" }, { id: "u2", username: "sam" }];

// ---------------------------------------- the checkpoints worth developing ---
// The reel develops the checkpoints on a list the administrator keeps, and every
// one of them is a checkpoint the grading account ranks A or B: the grades stay
// the gate, and the list only ever narrows what they let through. Left automatic
// it is still every A and B checkpoint, which on a real installation is a lot of
// images nobody chose one by one.
//
// This is the same list `LoRA pictures from` offers, so a preview has to be able
// to show what the two do to each other: `thumbnail_checkpoint_choices` carries
// the tier a row is graded at, the family that publishes it, how many images it
// already has, and why a row may be ticked and still produce nothing, and
// `qualifying_checkpoint_ids` is the stored selection — NULL is automatic, an
// empty list is a deliberate none. Both families are on here, because a family
// switched off is a reason a checkpoint produces nothing, and that belongs in the
// row that produces nothing.
const checkpointIdentity = (seed) => `lr1_${seed.padEnd(64, "0")}`;

const checkpointUniverse = () => [
  { id: checkpointIdentity("krea2v5"), label: "Moody Krea2 V5 BF16", tier: "A", library_label: "Krea 2", images: 6, note: null },
  { id: checkpointIdentity("krea2v4"), label: "Moody Krea2 V4 INT8", tier: "A", library_label: "Krea 2", images: 6, note: null },
  { id: checkpointIdentity("fluxhires"), label: "Flux Dev 1.1 Hires", tier: "B", library_label: "Flux Dev", images: 0, note: null },
  { id: checkpointIdentity("krea2fast"), label: "Moody Krea2 Fast v4", tier: "B", library_label: "Krea 2", images: 0, note: "not in the selected workflow" },
  { id: checkpointIdentity("fluxrealism"), label: "Flux Realism Engine", tier: "B", library_label: "Flux Dev", images: 0, note: null },
];

// `auto` is the stored NULL: everything the account ranks A or B, including a
// checkpoint graded later, which is exactly what an explicit list gives up.
const selectionFor = (mode) => {
  const all = checkpointUniverse().map((choice) => choice.id);
  if (mode === "none") return [];
  if (mode === "all") return all;
  if (mode === "some") return all.slice(0, 3);
  return null;
};

const developedIds = (config) => config.qualifying_checkpoint_ids ?? checkpointUniverse().map((choice) => choice.id);

// ------------------------------------------------- retired design snapshot ---

function parameterMarkup(control, value, attributes) {
  const label = escape(control.label || control.id);
  if (control.type === "boolean") return `<label class="demo-reel-boolean"><input type="checkbox" ${attributes} ${value ? "checked" : ""} /> ${label}</label>`;
  if (control.type === "choice") return `<label class="field"><span>${label}</span><select ${attributes}>${(control.choices || []).map((choice) => `<option value="${escape(choice.value)}" ${choice.value === value ? "selected" : ""}>${escape(choice.label)}</option>`).join("")}</select></label>`;
  if (["integer", "float", "decimal", "number"].includes(control.type)) return `<label class="field"><span>${label}</span><input type="number" ${attributes} value="${escape(value)}" ${control.minimum !== undefined ? `min="${escape(control.minimum)}"` : ""} ${control.maximum !== undefined ? `max="${escape(control.maximum)}"` : ""} step="${escape(control.step || "any")}" required /></label>`;
  return `<label class="field"><span>${label}</span><textarea rows="2" ${attributes}>${escape(value)}</textarea></label>`;
}

const recipeInputs = (detail) => (detail?.interface?.inputs || []).filter((control) => control.type !== "lora_stack" && control.type !== "seed" && !["image", "image_upload", "mask_upload"].includes(control.type) && control.semantic_role !== "positive_prompt");

function retiredSettingsMarkup(config, details = {}, context = {}, users = []) {
  const states = config.progress?.states || {};
  const status = (config.saved_paused ?? config.paused) ? "paused" : states.running ? "generating" : states.blocked ? "blocked" : states.failed ? "failed" : config.progress?.expected && config.progress.completed === config.progress.expected ? "complete" : "waiting";
  const progress = config.progress ? `${config.progress.completed} of ${config.progress.expected} images complete · ${status}` : status;
  const slots = config.slots.map((slot, index) => `<li class="demo-reel-slot-editor" data-demo-slot="${escape(slot.id)}"><div class="button-row"><button type="button" draggable="true" class="icon-button" data-demo-slot-handle="${escape(slot.id)}" aria-label="Reorder slot ${index + 1}" aria-description="Drag or use arrow keys">⠿</button><strong>Slot ${index + 1}</strong><button type="button" data-demo-action="slot-up" data-demo-id="${escape(slot.id)}" ${index === 0 ? "disabled" : ""}>↑</button><button type="button" data-demo-action="slot-down" data-demo-id="${escape(slot.id)}" ${index === config.slots.length - 1 ? "disabled" : ""}>↓</button><button type="button" class="destructive" data-demo-action="slot-delete" data-demo-id="${escape(slot.id)}">Remove slot</button></div><label class="field"><span>Title (optional)</span><input maxlength="120" data-demo-slot-field="title" data-demo-id="${escape(slot.id)}" value="${escape(slot.title)}" /></label><label class="field"><span>Prompt · use {subject} for the LoRA trigger phrase</span><textarea rows="3" maxlength="100000" required data-demo-slot-field="prompt" data-demo-id="${escape(slot.id)}">${escape(slot.prompt)}</textarea></label><p class="muted" data-demo-preview="${escape(slot.id)}">Preview: ${escape(expandDemoReelPrompt(slot.prompt, context.subject || "LoRA subject"))}</p><details><summary>Resolution overrides</summary>${(config.libraries || []).map((library) => {
    const recipe = config.recipes[library.key];
    const dimensions = recipeInputs(details[recipe?.source_key]).filter((input) => ["width", "height"].includes(input.semantic_role));
    return dimensions.length ? `<fieldset><legend>${escape(library.label)} · blank inherits recipe</legend><div class="demo-reel-fields">${dimensions.map((control) => `<label class="field"><span>${escape(control.label)}</span><input type="number" min="${escape(control.minimum)}" max="${escape(control.maximum)}" step="${escape(control.step || 1)}" data-demo-resolution="${escape(control.id)}" data-demo-family="${escape(library.key)}" data-demo-id="${escape(slot.id)}" value="${escape(slot.resolutions?.[library.key]?.[control.id] ?? "")}" placeholder="${escape(recipe.parameters?.[control.id] ?? control.default)}" /></label>`).join("")}</div></fieldset>` : "";
  }).join("")}</details></li>`).join("");
  const recipes = (config.libraries || []).map((library) => {
    const recipe = config.recipes[library.key];
    const detail = details[recipe?.source_key];
    return `<fieldset class="demo-reel-recipe"><legend>${escape(library.label)}</legend><label class="demo-reel-boolean"><input type="checkbox" data-demo-recipe-enabled="${escape(library.key)}" ${recipe ? "checked" : ""} /> Generate Demo Reel images for this model family</label>${recipe ? `<label class="field"><span>Workflow</span><select data-demo-source="${escape(library.key)}">${library.sources.map((source) => `<option value="${escape(source.source_key)}" ${source.source_key === recipe.source_key ? "selected" : ""}>${escape(source.label)}</option>`).join("")}</select></label><label class="field"><span>Default LoRA strength</span><input type="number" min="0.000001" step="any" required data-demo-strength="${escape(library.key)}" value="${escape(recipe.strength)}" /></label><details><summary>Generation settings</summary><p class="muted">Every image uses a new random seed and only its target selectable LoRA.</p><div class="demo-reel-fields">${recipeInputs(detail).map((control) => parameterMarkup(control, recipe.parameters[control.id] ?? control.default, `data-demo-parameter="${escape(control.id)}" data-demo-family="${escape(library.key)}" data-demo-type="${escape(control.type)}"`)).join("")}</div></details>` : ""}</fieldset>`;
  }).join("");
  const override = context.identity && config.recipes[context.libraryKey] ? `<label class="field"><span>${escape(context.label)} · LoRA strength override (optional)</span><input type="number" min="0.000001" step="any" data-demo-lora-strength value="${escape(config.strengths?.[context.libraryKey]?.[context.identity] ?? "")}" placeholder="${escape(config.recipes[context.libraryKey].strength)}" /></label>` : "";
  return `<form class="dialog-frame demo-reel-settings-frame" data-demo-settings-form><header class="dialog-header"><div><h2 id="demo-reel-settings-title">Demo Reel settings</h2><p>Shared prompts apply to every installed LoRA.</p><p role="status" data-demo-settings-status>${escape(progress)}</p></div><button type="button" class="icon-button" data-demo-action="cancel-settings" aria-label="Close Demo Reel settings">×</button></header><div class="demo-reel-content"><label class="demo-reel-boolean"><input type="checkbox" data-demo-paused ${config.paused ? "checked" : ""} /> Pause background generation</label><label class="field"><span>Use A/B checkpoint grades from</span><select data-demo-rating-user>${users.map((user) => `<option value="${escape(user.id)}" ${user.id === config.rating_user_id ? "selected" : ""}>${escape(user.username)}</option>`).join("")}</select></label><h3>Prompt slots</h3><ol class="demo-reel-slots">${slots}</ol><button type="button" class="button secondary" data-demo-action="slot-add">Add prompt slot</button><h3>Generation recipes</h3>${recipes || '<p class="muted">No published LoRA libraries are available.</p>'}${override}<p class="form-error" role="alert" data-demo-settings-error></p></div><footer class="dialog-actions"><span class="muted">Saving waits for five quiet minutes. Changes to prompts or generation settings replace affected images.</span><button type="button" class="button secondary" data-demo-action="reload-settings">Reload saved settings</button><button type="button" class="button secondary" data-demo-action="cancel-settings">Cancel</button><button type="submit" class="button primary">Save</button></footer></form>`;
}

// ------------------------------------------------------------------- view ----

const layout = params.get("layout") === "current" ? "current" : "proposed";
const state = {
  generationOpen: params.get("gen") !== "closed",
  advancedOpen: params.get("adv") === "open",
  error: params.get("error") === "on",
  open: params.get("open") !== "false",
};
let config = configFixture();

const dialog = document.querySelector("#demo-reel-settings-dialog");
const note = document.querySelector("#scene-note");
const recipeDefault = (family, control) => structuredClone(control.default);

// The application's own handlers live in createDemoReels(); this preview needs
// just enough of them to move through the states that matter for review.
function act(button) {
  const kind = button?.dataset.demoAction;
  const id = button?.dataset.demoId;
  if (!kind) return false;
  if (kind === "slot-add") config.slots.push({ id: crypto.randomUUID(), title: "", prompt: "", resolutions: {} });
  else if (kind === "slot-delete") config.slots = config.slots.filter((slot) => slot.id !== id);
  else if (kind === "slot-duplicate") {
    const index = config.slots.findIndex((slot) => slot.id === id);
    config.slots.splice(index + 1, 0, { ...structuredClone(config.slots[index]), id: crypto.randomUUID() });
  } else if (kind === "slot-insert") {
    const slot = config.slots.find((slot) => slot.id === id);
    slot.prompt = `${slot.prompt}${slot.prompt && !/\s$/u.test(slot.prompt) ? " " : ""}{subject}`;
  } else if (kind === "slot-up" || kind === "slot-down") {
    const from = config.slots.findIndex((slot) => slot.id === id);
    const to = from + (kind === "slot-up" ? -1 : 1);
    if (to < 0 || to >= config.slots.length) return true;
    const [slot] = config.slots.splice(from, 1);
    config.slots.splice(to, 0, slot);
  } else if (kind === "size-reset") {
    delete config.slots.find((slot) => slot.id === id).resolutions?.[button.dataset.demoFamily];
  } else if (kind === "control-reset" || kind === "recipe-reset") {
    const recipe = config.recipes[button.dataset.demoFamily];
    const inputs = (details[recipe.source_key]?.interface?.inputs || []).filter((control) => control.semantic_role !== "positive_prompt" && !["lora_stack", "seed", "image", "image_upload", "mask_upload"].includes(control.type));
    if (kind === "recipe-reset") recipe.parameters = Object.fromEntries(inputs.map((control) => [control.id, recipeDefault(recipe.source_key, control)]));
    else recipe.parameters[button.dataset.demoControl] = recipeDefault(recipe.source_key, inputs.find((control) => control.id === button.dataset.demoControl));
  } else if (kind === "lora-strength-reset") delete config.strengths[context.libraryKey][context.identity];
  else if (kind === "checkpoint-all") config.qualifying_checkpoint_ids = checkpointUniverse().map((row) => row.id);
  else if (kind === "checkpoint-none") config.qualifying_checkpoint_ids = [];
  else if (kind === "checkpoint-auto") config.qualifying_checkpoint_ids = null;
  else if (kind === "cancel-settings") state.open = false;
  else if (kind === "reload-settings") config = configFixture();
  else return false;
  return true;
}

function render() {
  const scroll = dialog.querySelector(".demo-reel-content")?.scrollTop || 0;
  const opened = new Map([...dialog.querySelectorAll("details[data-demo-details]")].map((node) => [node.dataset.demoDetails, node.open]));
  document.body.classList.toggle("drs-retired", layout === "current");
  config.error = state.error ? "Enter a prompt for every slot." : "";
  dialog.innerHTML = layout === "current"
    ? retiredSettingsMarkup({ ...config, saved_paused: config.paused }, details, context, users)
    : demoReelSettingsMarkup(config, details, context, users);
  if (layout === "current" && state.generationOpen) dialog.querySelectorAll("details details").forEach((node) => { if (node.querySelector("summary")?.textContent.includes("Generation settings")) node.open = true; });
  if (layout === "proposed") {
    for (const node of dialog.querySelectorAll("details[data-demo-details]")) {
      const key = node.dataset.demoDetails;
      node.open = opened.get(key) ?? (key === "generation" ? state.generationOpen : state.advancedOpen && key.startsWith("advanced-"));
    }
  }
  note.textContent = `${layout === "current" ? "Retired design" : "Development list"} · ${state.generationOpen ? "Model families open" : "closed"}`;
  if (state.open && !dialog.open) dialog.showModal();
  if (!state.open && dialog.open) dialog.close();
  const next = dialog.querySelector(".demo-reel-content");
  if (next) next.scrollTop = params.get("scroll") ? Number(params.get("scroll")) : scroll;
  if (params.get("focus") === "checkpoints") dialog.querySelector("[data-demo-checkpoints]")?.scrollIntoView({ block: "start" });
}

dialog.addEventListener("click", (event) => {
  if (layout === "proposed" && act(event.target.closest("[data-demo-action]"))) render();
});
dialog.addEventListener("change", (event) => {
  if (layout !== "proposed") return;
  const checkpoint = event.target.dataset?.demoCheckpoint;
  if (checkpoint) {
    const picked = new Set(developedIds(config));
    if (event.target.checked) picked.add(checkpoint);
    else picked.delete(checkpoint);
    // Stored in the grading account's A-then-B order, never in click order: the
    // reel gallery and the picture picker both list checkpoints that way.
    config.qualifying_checkpoint_ids = checkpointUniverse().map((row) => row.id).filter((id) => picked.has(id));
    render();
    dialog.querySelector(`[data-demo-checkpoint="${CSS.escape(checkpoint)}"]`)?.focus();
    return;
  }
  const family = event.target.dataset.demoRecipeEnabled;
  if (!family) return;
  if (event.target.checked) {
    const library = config.libraries.find((entry) => entry.key === family);
    const sourceKey = library.sources[0].source_key;
    const inputs = (details[sourceKey]?.interface?.inputs || []).filter((control) => control.semantic_role !== "positive_prompt" && !["lora_stack", "seed", "image", "image_upload", "mask_upload"].includes(control.type));
    config.recipes[family] = { source_key: sourceKey, strength: 1, parameters: Object.fromEntries(inputs.map((control) => [control.id, structuredClone(control.default)])) };
  } else delete config.recipes[family];
  render();
});

window.previewOpenDialog = () => { state.open = true; render(); };
window.previewSetGeneration = (open) => { state.generationOpen = Boolean(open); render(); };
window.previewSetAdvanced = (open) => { state.advancedOpen = Boolean(open); render(); };
window.previewSetError = (on) => { state.error = Boolean(on); render(); };

render();
