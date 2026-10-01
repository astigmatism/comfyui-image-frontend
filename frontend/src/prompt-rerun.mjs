// Prompt Re-run: queue the exact prompts of selected gallery items again with
// new settings. The server reads each selected generation's retained prompt;
// the browser only chooses the settings and the destination folder name.
import {
  MAX_BATCH_GENERATION_ITEMS,
  MAX_GENERATION_QUANTITY,
  MIN_GENERATION_QUANTITY,
  clampGenerationQuantity,
  escapeHtml,
  interfaceInputs,
  normalizeSourceModelSelections,
  parametersForRequest,
  positivePromptInput,
  reconcileInterfaceValues,
  sourceModelParameterVariants,
  sourceModelSelectors,
} from "./lib.mjs";

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

export function rerunInputs(contract) {
  const inputs = interfaceInputs(contract);
  return {
    prompt: positivePromptInput(contract),
    width: inputs.find((input) => input.semantic_role === "width" && input.type === "integer") || null,
    height: inputs.find((input) => input.semantic_role === "height" && input.type === "integer") || null,
    seed: inputs.find((input) => input.type === "seed") || null,
    loras: inputs.filter((input) => input.type === "lora_stack" && Array.isArray(input.items)),
  };
}

function dateLabel(now) {
  const date = new Date(now);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function defaultRerunFolderName(source, selections, now = Date.now()) {
  const selector = sourceModelSelectors(source)[0];
  const values = selector ? selections?.[selector.parameter_id] || [] : [];
  if (values.length === 1) {
    const label = selector.choices.find((choice) => choice.value === values[0])?.label || values[0];
    return `${label} re-run`.slice(0, 100);
  }
  return `Prompt Re-run ${dateLabel(now)}`;
}

// The modal starts from the control panel's current settings and never writes back.
export function promptRerunDraft({ source, parameters, selections, quantity, collectionId, now = Date.now() }) {
  const contract = source?.interface || source?.contract || null;
  const normalizedSelections = normalizeSourceModelSelections(source, selections || {}, parameters || {});
  return {
    sourceKey: source?.source_key || null,
    source,
    values: reconcileInterfaceValues(contract, structuredClone(parameters || {})),
    selections: normalizedSelections,
    quantity: clampGenerationQuantity(quantity ?? MIN_GENERATION_QUANTITY),
    keepOriginalResolution: false,
    seedMode: "random",
    skipDuplicates: true,
    folderName: defaultRerunFolderName(source, normalizedSelections, now),
    parentCollectionId: collectionId || null,
  };
}

// Switching the modal's source keeps any settings saved for that source in the panel.
export function retargetRerunDraft(draft, source, savedParameters = {}, selections = {}) {
  const contract = source?.interface || source?.contract || null;
  return {
    ...draft,
    sourceKey: source?.source_key || null,
    source,
    values: reconcileInterfaceValues(contract, structuredClone(savedParameters || {})),
    selections: normalizeSourceModelSelections(source, selections, savedParameters || {}),
    seedMode: rerunInputs(contract).seed ? draft.seedMode : "random",
  };
}

export function rerunPromptCount(draft, preview) {
  if (!preview) return 0;
  return draft.skipDuplicates ? preview.unique_prompt_count : preview.prompt_count;
}

export function rerunVariants(draft) {
  return sourceModelParameterVariants(draft.source, draft.selections);
}

export function promptRerunPlannedTotal(draft, preview) {
  return rerunPromptCount(draft, preview) * rerunVariants(draft).length * draft.quantity;
}

function integerError(input, value) {
  if (!input) return null;
  if (!Number.isSafeInteger(value)) return "Enter a whole number.";
  if (input.minimum !== undefined && value < input.minimum) return `At least ${input.minimum}.`;
  if (input.maximum !== undefined && value > input.maximum) return `At most ${input.maximum}.`;
  const step = Number(input.step);
  if (Number.isSafeInteger(step) && step > 0 && (value - (Number(input.minimum) || 0)) % step !== 0) {
    return `Use a multiple of ${step}.`;
  }
  return null;
}

export function validatePromptRerunDraft(draft, preview) {
  const errors = {};
  const contract = draft.source?.interface || draft.source?.contract;
  const inputs = rerunInputs(contract);
  if (!draft.source || draft.source.available === false) errors.source = "Choose an available generation source.";
  else if (!inputs.prompt) errors.source = "This source has no prompt input.";
  if (!draft.folderName.trim()) errors.folder = "Name the folder for the results.";
  else if (draft.folderName.trim().length > 100) errors.folder = "Use at most 100 characters.";
  const selector = sourceModelSelectors(draft.source)[0];
  if (selector && !(draft.selections[selector.parameter_id] || []).length) errors.checkpoints = "Choose at least one checkpoint.";
  if (!draft.keepOriginalResolution) {
    for (const key of ["width", "height"]) {
      const error = integerError(inputs[key], draft.values[inputs[key]?.id]);
      if (error) errors[key] = error;
    }
  }
  for (const control of inputs.loras) {
    const value = draft.values[control.id] || [];
    if (value.some((entry) => !Number.isFinite(entry.strength) || entry.strength < control.minimum || entry.strength > control.maximum)) {
      errors[control.id] = `Strengths must be between ${control.minimum} and ${control.maximum}.`;
    }
  }
  if (draft.seedMode === "original" && draft.quantity !== 1) errors.quantity = "Reusing original seeds queues one image per prompt.";
  if (!rerunPromptCount(draft, preview)) errors.prompts = "None of the selected images has a prompt to re-run.";
  const total = promptRerunPlannedTotal(draft, preview);
  if (total > MAX_BATCH_GENERATION_ITEMS) {
    errors.total = `${total} planned generations exceeds the ${MAX_BATCH_GENERATION_ITEMS}-item limit. Lower the count per prompt, choose fewer checkpoints, or select fewer images.`;
  }
  return errors;
}

export function promptRerunRequest(draft, selectionBody) {
  const contract = draft.source?.interface || draft.source?.contract;
  const inputs = rerunInputs(contract);
  const parameters = parametersForRequest(contract, draft.values);
  if (inputs.prompt) delete parameters[inputs.prompt.id];
  if (inputs.seed) delete parameters[inputs.seed.id];
  return {
    ...selectionBody,
    folder_name: draft.folderName.trim(),
    parent_collection_id: draft.parentCollectionId,
    source_key: draft.sourceKey,
    revision: structuredClone(draft.source.revision),
    parameters,
    model_variants: rerunVariants(draft),
    quantity: draft.quantity,
    keep_original_resolution: draft.keepOriginalResolution,
    seed_mode: inputs.seed ? draft.seedMode : "random",
    skip_duplicates: draft.skipDuplicates,
  };
}

// Error slots always exist so typing can update them without replacing inputs.
function fieldError(errors, key) {
  return `<p class="field-error" data-rerun-error="${escapeHtml(key)}">${escapeHtml(errors[key] || "")}</p>`;
}

export function promptRerunSubmitLabel(draft, preview, loading = false) {
  if (loading) return "Queueing…";
  return preview ? `Queue ${plural(promptRerunPlannedTotal(draft, preview), "generation")}` : "Loading…";
}

function numberAttributes(input) {
  return ["minimum:min", "maximum:max", "step:step"]
    .map((pair) => pair.split(":"))
    .filter(([name]) => input?.[name] !== undefined)
    .map(([name, attribute]) => `${attribute}="${escapeHtml(input[name])}"`)
    .join(" ");
}

function previewSummary(preview, draft) {
  if (!preview) return "Reading the selected prompts…";
  const parts = [`${plural(rerunPromptCount(draft, preview), "prompt")} from ${plural(preview.generation_count, "generation")}`];
  if (preview.duplicate_count) parts.push(draft.skipDuplicates ? `${preview.duplicate_count} duplicate${preview.duplicate_count === 1 ? "" : "s"} skipped` : `${preview.duplicate_count} repeated`);
  if (preview.skipped_count) parts.push(`${preview.skipped_count} without a prompt`);
  return parts.join(" · ");
}

export function promptRerunSummaryMarkup(draft, preview, errors = validatePromptRerunDraft(draft, preview)) {
  const total = promptRerunPlannedTotal(draft, preview);
  return `<span data-rerun-total>${preview ? `Queues ${plural(total, "generation")}` : ""}</span>${fieldError(errors, "total")}${fieldError(errors, "prompts")}`;
}

export function promptRerunMarkup(draft, preview, { sources = [], parentName = "Home", loading = false, error = "" } = {}) {
  const errors = preview ? validatePromptRerunDraft(draft, preview) : {};
  const contract = draft.source?.interface || draft.source?.contract;
  const inputs = rerunInputs(contract);
  const selector = sourceModelSelectors(draft.source)[0];
  const selected = new Set(selector ? draft.selections[selector.parameter_id] || [] : []);
  const total = promptRerunPlannedTotal(draft, preview);
  const imageSources = sources.filter((source) => (source.output_kind || "image") === "image");
  const excerpts = (preview?.prompts || []).map((item) => `<li>${escapeHtml(item.excerpt)}${item.width && item.height ? ` <small>${item.width} × ${item.height}</small>` : ""}</li>`).join("");
  const more = preview && preview.prompts.length < rerunPromptCount(draft, preview) ? `<li class="muted">and ${rerunPromptCount(draft, preview) - preview.prompts.length} more</li>` : "";
  const blocked = loading || !preview || Object.keys(errors).length > 0;
  return `<div class="dialog-frame">
    <header class="dialog-header"><div><h2>Prompt Re-run</h2><p data-rerun-summary>${escapeHtml(previewSummary(preview, draft))}</p></div><button type="button" class="icon-button" data-rerun-action="close" aria-label="Close">×</button></header>
    <div class="rerun-dialog-content">
      <p class="help-text">Generates the exact prompts of the selection again. Creative Direction and the prompt generator are not used. Other settings come from the control panel.</p>
      ${excerpts ? `<details class="rerun-prompts"><summary>Prompts</summary><ol>${excerpts}${more}</ol></details>` : ""}
      <div class="rerun-grid">
        <label class="field rerun-wide">Folder name<input type="text" name="rerun_folder" maxlength="100" value="${escapeHtml(draft.folderName)}" aria-invalid="${Boolean(errors.folder)}" /><small class="muted">Created inside ${escapeHtml(parentName)}</small>${fieldError(errors, "folder")}</label>
        <label class="field rerun-wide">Generation source<select name="rerun_source">${imageSources.map((source) => `<option value="${escapeHtml(source.source_key)}" ${source.source_key === draft.sourceKey ? "selected" : ""} ${source.available === false ? "disabled" : ""}>${escapeHtml(source.display_name || source.source_key)}${source.available === false ? " — Unavailable" : ""}</option>`).join("")}</select>${fieldError(errors, "source")}</label>
        ${selector ? `<fieldset class="field rerun-wide rerun-checkpoints"><legend>${escapeHtml(selector.label)}</legend><div class="rerun-choice-list">${selector.choices.map((choice) => `<label class="rerun-check"><input type="checkbox" name="rerun_checkpoint" value="${escapeHtml(choice.value)}" ${selected.has(choice.value) ? "checked" : ""} /><span>${escapeHtml(choice.label)}</span></label>`).join("")}</div>${fieldError(errors, "checkpoints")}</fieldset>` : ""}
        ${inputs.loras.map((control) => {
          const entries = draft.values[control.id] || [];
          return `<fieldset class="field rerun-wide rerun-loras" data-rerun-lora="${escapeHtml(control.id)}"><legend>${escapeHtml(control.label || "LoRAs")}</legend>${entries.map((entry) => {
            const item = control.items.find((candidate) => candidate.id === entry.id);
            return `<label class="rerun-lora-row"><input type="checkbox" name="rerun_lora_enabled" data-lora-id="${escapeHtml(entry.id)}" ${entry.strength !== 0 ? "checked" : ""} aria-label="Enable ${escapeHtml(item?.label || entry.id)}" /><span>${escapeHtml(item?.label || entry.id)}</span><input type="number" name="rerun_lora_strength" data-lora-id="${escapeHtml(entry.id)}" value="${escapeHtml(entry.strength)}" ${numberAttributes(control)} aria-label="${escapeHtml(item?.label || entry.id)} strength" /></label>`;
          }).join("")}${fieldError(errors, control.id)}</fieldset>`;
        }).join("")}
        ${inputs.width && inputs.height ? `<fieldset class="field rerun-wide rerun-resolution"><legend>Resolution</legend>
          <label class="rerun-check"><input type="checkbox" name="rerun_keep_resolution" ${draft.keepOriginalResolution ? "checked" : ""} /><span>Keep each original image's resolution</span></label>
          <div class="rerun-axes"><label class="field compact">Width<input type="number" name="rerun_width" value="${escapeHtml(draft.values[inputs.width.id] ?? "")}" ${numberAttributes(inputs.width)} ${draft.keepOriginalResolution ? "disabled" : ""} aria-invalid="${Boolean(errors.width)}" />${fieldError(errors, "width")}</label>
          <label class="field compact">Height<input type="number" name="rerun_height" value="${escapeHtml(draft.values[inputs.height.id] ?? "")}" ${numberAttributes(inputs.height)} ${draft.keepOriginalResolution ? "disabled" : ""} aria-invalid="${Boolean(errors.height)}" />${fieldError(errors, "height")}</label></div>
          ${draft.keepOriginalResolution ? '<small class="muted">Images without a recorded or valid size use the width and height above.</small>' : ""}</fieldset>` : ""}
        ${inputs.seed ? `<fieldset class="field rerun-seed"><legend>Seed</legend>
          <label class="rerun-check"><input type="radio" name="rerun_seed" value="random" ${draft.seedMode === "random" ? "checked" : ""} /><span>Random per image</span></label>
          <label class="rerun-check"><input type="radio" name="rerun_seed" value="original" ${draft.seedMode === "original" ? "checked" : ""} /><span>Reuse original seed</span></label></fieldset>` : ""}
        <label class="field rerun-quantity">Generations per prompt<input type="number" name="rerun_quantity" min="${MIN_GENERATION_QUANTITY}" max="${draft.seedMode === "original" ? 1 : MAX_GENERATION_QUANTITY}" step="1" value="${draft.quantity}" ${draft.seedMode === "original" ? "disabled" : ""} />${fieldError(errors, "quantity")}</label>
        <label class="rerun-check rerun-wide"><input type="checkbox" name="rerun_skip_duplicates" ${draft.skipDuplicates ? "checked" : ""} /><span>Skip duplicate prompts</span></label>
      </div>
      <div class="rerun-total" role="status">${promptRerunSummaryMarkup(draft, preview, errors)}</div>
      <p class="field-error" data-operation-error role="alert">${escapeHtml(error)}</p>
    </div>
    <footer class="dialog-actions"><button type="button" class="button secondary" data-rerun-action="close">Cancel</button><button type="button" class="button primary" data-rerun-action="submit" ${blocked ? "disabled" : ""}>${promptRerunSubmitLabel(draft, preview, loading)}</button></footer>
  </div>`;
}

// Apply one form control's change to a draft, returning a new draft.
export function updateRerunDraft(draft, name, value, extra = {}) {
  const next = { ...draft, values: structuredClone(draft.values), selections: structuredClone(draft.selections) };
  const contract = draft.source?.interface || draft.source?.contract;
  const inputs = rerunInputs(contract);
  const integer = (raw) => (raw === "" ? null : Number(raw));
  if (name === "rerun_folder") next.folderName = String(value);
  else if (name === "rerun_quantity") next.quantity = clampGenerationQuantity(value);
  else if (name === "rerun_keep_resolution") next.keepOriginalResolution = Boolean(value);
  else if (name === "rerun_skip_duplicates") next.skipDuplicates = Boolean(value);
  else if (name === "rerun_seed") {
    next.seedMode = value === "original" ? "original" : "random";
    if (next.seedMode === "original") next.quantity = 1;
  } else if (name === "rerun_width" && inputs.width) next.values[inputs.width.id] = integer(value);
  else if (name === "rerun_height" && inputs.height) next.values[inputs.height.id] = integer(value);
  else if (name === "rerun_checkpoint") {
    const selector = sourceModelSelectors(draft.source)[0];
    if (selector) {
      const chosen = new Set(next.selections[selector.parameter_id] || []);
      if (extra.checked) chosen.add(value); else chosen.delete(value);
      next.selections[selector.parameter_id] = selector.choices.map((choice) => choice.value).filter((item) => chosen.has(item));
      const values = next.selections[selector.parameter_id];
      if (values.length && !values.includes(next.values[selector.parameter_id])) next.values[selector.parameter_id] = values[0];
    }
  } else if (name === "rerun_lora_strength" || name === "rerun_lora_enabled") {
    const control = inputs.loras.find((item) => item.id === extra.controlId);
    if (control) {
      next.values[control.id] = (next.values[control.id] || []).map((entry) => {
        if (entry.id !== extra.loraId) return entry;
        if (name === "rerun_lora_strength") return { ...entry, strength: value === "" ? Number.NaN : Number(value) };
        const restored = Number(extra.restoreStrength);
        return { ...entry, strength: value ? (restored > 0 ? restored : 1) : 0 };
      });
    }
  }
  return next;
}

// DOM controller. `deps` supplies application state and services.
export function createPromptRerun(dialog, deps) {
  let draft = null;
  let preview = null;
  let selectionBody = null;
  let loading = false;
  let error = "";
  let token = 0;
  const loraMemory = new Map();

  const render = () => {
    if (!draft) return;
    const focused = document.activeElement && dialog.contains(document.activeElement) ? document.activeElement : null;
    const name = focused?.name;
    const loraId = focused?.dataset?.loraId;
    const value = focused?.value;
    const position = focused && "selectionStart" in focused ? (() => { try { return focused.selectionStart; } catch { return null; } })() : null;
    dialog.innerHTML = promptRerunMarkup(draft, preview, { sources: deps.sources(), parentName: deps.collectionName(draft.parentCollectionId), loading, error });
    if (name) {
      const selector = loraId ? `[name="${name}"][data-lora-id="${CSS.escape(loraId)}"]` : focused.type === "radio" || focused.type === "checkbox" ? `[name="${name}"][value="${CSS.escape(value)}"]` : `[name="${name}"]`;
      const replacement = dialog.querySelector(selector);
      replacement?.focus({ preventScroll: true });
      if (position !== null && replacement && "setSelectionRange" in replacement) {
        try { replacement.setSelectionRange(position, position); } catch { /* number inputs */ }
      }
    }
  };

  function refreshDerived() {
    const errors = preview ? validatePromptRerunDraft(draft, preview) : {};
    for (const slot of dialog.querySelectorAll("[data-rerun-error]")) {
      if (slot.closest(".rerun-total")) continue;
      slot.textContent = errors[slot.dataset.rerunError] || "";
      const field = slot.closest("label")?.querySelector("input");
      if (field) field.setAttribute("aria-invalid", String(Boolean(errors[slot.dataset.rerunError])));
    }
    const status = dialog.querySelector(".rerun-total");
    if (status) status.innerHTML = promptRerunSummaryMarkup(draft, preview, errors);
    const summary = dialog.querySelector("[data-rerun-summary]");
    if (summary) summary.textContent = previewSummary(preview, draft);
    const button = dialog.querySelector('[data-rerun-action="submit"]');
    if (button) {
      button.disabled = loading || !preview || Object.keys(errors).length > 0;
      button.textContent = promptRerunSubmitLabel(draft, preview, loading);
    }
  }

  async function open({ body }) {
    const request = ++token;
    selectionBody = body;
    preview = null;
    error = "";
    loading = false;
    loraMemory.clear();
    draft = promptRerunDraft(deps.initial());
    render();
    dialog.oncancel = (event) => { if (loading) event.preventDefault(); };
    dialog.onclose = () => { token += 1; draft = null; deps.onClose?.(); };
    dialog.showModal();
    dialog.querySelector('[name="rerun_folder"]')?.focus();
    try {
      const result = await deps.api("/api/gallery/prompt-rerun/preview", { method: "POST", body: JSON.stringify(body) });
      if (request !== token) return;
      preview = result;
    } catch (failure) {
      if (request !== token) return;
      error = failure.message || "The selected prompts could not be read.";
    }
    render();
  }

  async function changeSource(key) {
    const request = ++token;
    try {
      const source = await deps.loadSource(key);
      if (request !== token || !draft) return;
      draft = retargetRerunDraft(draft, source, deps.savedParameters(key, source), deps.savedSelections(source));
      loraMemory.clear();
      error = "";
    } catch (failure) {
      if (request !== token || !draft) return;
      error = failure.message || "That generation source could not be loaded.";
    }
    render();
  }

  async function submit() {
    if (!draft || loading || !preview || Object.keys(validatePromptRerunDraft(draft, preview)).length) return;
    loading = true;
    error = "";
    render();
    const body = promptRerunRequest(draft, selectionBody);
    try {
      const result = await deps.submit(body, promptRerunPlannedTotal(draft, preview));
      loading = false;
      if (result) dialog.close();
      else render();
    } catch (failure) {
      loading = false;
      if (!draft) return;
      error = failure.message || "Prompt Re-run could not be queued.";
      if (["source_republished", "source_unavailable"].includes(failure.code)) await changeSource(draft.sourceKey);
      render();
    }
  }

  dialog.addEventListener("click", (event) => {
    const action = event.target.closest("[data-rerun-action]")?.dataset.rerunAction;
    if (!action) return;
    event.preventDefault();
    if (action === "close" && !loading) dialog.close();
    else if (action === "submit") void submit();
  });
  const handle = (event, commit) => {
    const target = event.target;
    if (!draft || !target.name?.startsWith("rerun_") || loading) return;
    if (target.name === "rerun_source") { if (commit) void changeSource(target.value); return; }
    const controlId = target.closest("[data-rerun-lora]")?.dataset.rerunLora;
    const loraId = target.dataset.loraId;
    const memoryKey = `${controlId}:${loraId}`;
    if (target.name === "rerun_lora_strength" && Number(target.value) > 0) loraMemory.set(memoryKey, Number(target.value));
    if (target.name === "rerun_lora_enabled" && !target.checked) {
      const current = (draft.values[controlId] || []).find((entry) => entry.id === loraId)?.strength;
      if (current > 0) loraMemory.set(memoryKey, current);
    }
    const value = target.type === "checkbox" && target.name !== "rerun_checkpoint" ? target.checked : target.value;
    draft = updateRerunDraft(draft, target.name, value, { checked: target.checked, controlId, loraId, restoreStrength: loraMemory.get(memoryKey) });
    // Text and number fields refresh only derived state: replacing the focused
    // input would move the caret, and a blur-time re-render would swallow the
    // click that caused the blur.
    if (target.type === "text" || target.type === "number") {
      refreshDerived();
      return;
    }
    render();
  };
  dialog.addEventListener("input", (event) => { if (event.target.type === "text" || event.target.type === "number") handle(event, false); });
  dialog.addEventListener("change", (event) => handle(event, true));
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.matches('input[type="text"], input[type="number"]')) {
      event.preventDefault();
      void submit();
    }
  });

  return { open, isOpen: () => dialog.open, close: () => { if (!loading && dialog.open) dialog.close(); } };
}
