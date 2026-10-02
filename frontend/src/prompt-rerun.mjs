// Prompt Re-run: queue the exact prompts of selected gallery items again with
// new settings. The server reads each selected generation's retained prompt;
// the browser only chooses the settings and the destination folder name.
import {
  MAX_BATCH_GENERATION_ITEMS,
  MAX_GENERATION_QUANTITY,
  MIN_GENERATION_QUANTITY,
  clampGenerationQuantity,
  clientValidate,
  escapeHtml,
  interfaceInputs,
  normalizeSourceModelSelections,
  parametersForRequest,
  positivePromptInput,
  reconcileInterfaceValues,
  recordRecentResolution,
  removeRecentResolution,
  sourceModelParameterVariants,
  sourceModelSelectors,
} from "./lib.mjs";
import { controlMarkup, controlSectionMarkup, pairedResolutionMarkup, sourcePickerMarkup } from "./render.mjs";
import { loraStackError, loraStackMarkup } from "./lora-stack.mjs";

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

export function rerunInputs(contract) {
  const inputs = interfaceInputs(contract);
  return {
    prompt: positivePromptInput(contract),
    width: inputs.find((input) => input.semantic_role === "width" && input.type === "integer") || null,
    height: inputs.find((input) => input.semantic_role === "height" && input.type === "integer") || null,
    resolution: inputs.find((input) => input.type === "resolution") || null,
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
export function promptRerunDraft({ source, parameters, selections, quantity, collectionId, checkpointTiers = {}, loraMemory = {}, loraImages = {}, recentResolutions = [], promptAssistant = {}, now = Date.now() }) {
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
    sectionOpen: {},
    checkpointTiers: structuredClone(checkpointTiers),
    loraMemory: structuredClone(loraMemory),
    loraImages: structuredClone(loraImages),
    recentResolutions: structuredClone(recentResolutions),
    refine: false,
    creativeDirection: promptAssistant.creativeDirection || "",
    instructions: promptAssistant.instructionOverrides?.refine ?? promptAssistant.defaultInstructions?.refine ?? "",
    defaultInstructions: promptAssistant.defaultInstructions?.refine || "",
    think: promptAssistant.think !== false,
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
    keepOriginalResolution: Boolean(rerunInputs(contract).width && rerunInputs(contract).height && draft.keepOriginalResolution),
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
  if (inputs.resolution) {
    const resolutionError = clientValidate(contract, draft.values)[inputs.resolution.id];
    if (resolutionError) errors[inputs.resolution.id] = resolutionError;
  }
  for (const control of inputs.loras) {
    const value = draft.values[control.id] || [];
    const error = loraStackError(control, value);
    if (error) errors[control.id] = error;
  }
  if (draft.seedMode === "original" && draft.quantity !== 1) errors.quantity = "Reusing original seeds queues one image per prompt.";
  if (!rerunPromptCount(draft, preview)) errors.prompts = "None of the selected images has a prompt to re-run.";
  if (draft.refine) {
    if (!draft.creativeDirection.trim()) errors.direction = "Enter Creative Direction for these prompts.";
    if (!draft.instructions.trim()) errors.instructions = "Enter refinement instructions or reset to the default.";
    else if (draft.instructions.length > 8000) errors.instructions = "Use at most 8000 characters.";
  }
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
    ...(draft.refine ? { refinement: {
      creative_direction: draft.creativeDirection.trim(), instructions: draft.instructions.trim(), think: draft.think,
    } } : {}),
  };
}

// Error slots always exist so typing can update them without replacing inputs.
function fieldError(errors, key) {
  return `<p class="field-error" data-rerun-error="${escapeHtml(key)}">${escapeHtml(errors[key] || "")}</p>`;
}

export function promptRerunSubmitLabel(draft, preview, loading = false) {
  if (loading) return "Queueing…";
  if (preview && draft.refine) return "Refine & Queue";
  return preview ? `Queue ${plural(promptRerunPlannedTotal(draft, preview), "generation")}` : "Loading…";
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
  const text = draft.refine ? `Refine ${plural(rerunPromptCount(draft, preview), "prompt")} · Generate up to ${plural(total, "image")}` : `Queues ${plural(total, "generation")}`;
  return `<span data-rerun-total>${preview ? text : ""}</span>${fieldError(errors, "total")}${fieldError(errors, "prompts")}`;
}

function creativeDirectionMarkup(draft, errors) {
  return controlSectionMarkup({
    key: "creative-direction", title: "Creative Direction", status: draft.refine ? "Refine each prompt" : "Off",
    idPrefix: "rerun-", className: "control-section-creative-direction",
    open: Boolean(draft.sectionOpen?.["creative-direction"]),
    actions: `<div class="control-section-actions feature-section-switch"><label class="switch"><input type="checkbox" role="switch" name="rerun_refine" aria-label="Use Creative Direction" ${draft.refine ? "checked" : ""} /><span aria-hidden="true"></span><em>${draft.refine ? "On" : "Off"}</em></label></div>`,
    content: `<p class="help-text">Refine each saved prompt with the same direction. Images queue as each refinement finishes.</p>
      <label class="field">Creative Direction<textarea name="rerun_direction" rows="3" aria-label="Creative Direction" aria-invalid="${Boolean(errors.direction)}">${escapeHtml(draft.creativeDirection)}</textarea>${fieldError(errors, "direction")}</label>
      <details class="prompt-preprocessor" ${draft.refine && errors.instructions ? "open" : ""}><summary>Prompt pre-processor</summary><div class="prompt-preprocessor-content">
        <p class="prompt-preprocessor-context">Instructions for refining your prompt</p>
        <label class="field">Refinement instructions<textarea name="rerun_instructions" rows="8" maxlength="8000" aria-label="Refinement instructions" aria-invalid="${Boolean(errors.instructions)}">${escapeHtml(draft.instructions)}</textarea>${fieldError(errors, "instructions")}</label>
        <div class="prompt-preprocessor-tools"><button type="button" class="button low" data-rerun-action="reset-instructions" ${draft.defaultInstructions ? "" : "disabled"}>Reset to default</button></div>
        <label class="prompt-assistant-thinking-option"><input type="checkbox" name="rerun_think" ${draft.think ? "checked" : ""} /> Thinking mode</label>
      </div></details>`,
  });
}

export function promptRerunMarkup(draft, preview, { parentName = "Home", loading = false, error = "" } = {}) {
  const errors = preview ? validatePromptRerunDraft(draft, preview) : {};
  const contract = draft.source?.interface || draft.source?.contract;
  const inputs = rerunInputs(contract);
  const blocked = loading || !preview || Object.keys(errors).length > 0;
  const section = (key, title, status, content, actions = "", hasError = false) => controlSectionMarkup({
    key, title, status, content, actions, idPrefix: "rerun-",
    className: `control-section-${key === "resolution" ? "resolution" : "lora"}`,
    open: Boolean(hasError || draft.sectionOpen?.[key]),
  });
  const loras = inputs.loras.map((control) => section(
    `lora-${control.id}`, control.label || "LoRAs",
    `${(draft.values[control.id] || []).filter((entry) => entry.strength > 0).length} active`,
    loraStackMarkup(control, draft.values[control.id], draft.loraImages?.[control.id]) + fieldError(errors, control.id),
    `<div class="control-section-actions"><button type="button" class="icon-button prompt-editor-launch" data-lora-open data-control-context="rerun" data-lora-control-id="${escapeHtml(control.id)}" aria-label="Open LoRA manager" title="Open LoRA manager" aria-haspopup="dialog" aria-controls="lora-manager-dialog"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5" /></svg></button></div>`,
    Boolean(errors[control.id]),
  )).join("");
  const paired = inputs.width && inputs.height;
  const value = paired ? { width: draft.values[inputs.width.id], height: draft.values[inputs.height.id] } : draft.values[inputs.resolution?.id];
  const resolutionErrors = paired ? { [inputs.width.id]: errors.width, [inputs.height.id]: errors.height } : errors;
  const resolutionOptions = { idPrefix: "rerun-", liveErrors: true, hideLegend: true, hideLabel: true, recentResolutions: draft.recentResolutions, disabled: paired && draft.keepOriginalResolution };
  const resolution = paired || inputs.resolution ? section("resolution", "Resolution",
    paired && draft.keepOriginalResolution ? "Original sizes" : `${value?.width ?? "—"} × ${value?.height ?? "—"}`,
    `${paired ? `<label class="rerun-check rerun-original-resolution"><input type="checkbox" name="rerun_keep_resolution" ${draft.keepOriginalResolution ? "checked" : ""} /><span>Keep each original image's resolution</span></label>` : ""}
    ${paired ? pairedResolutionMarkup(inputs.width, inputs.height, draft.values, contract, resolutionErrors, resolutionOptions) : controlMarkup(inputs.resolution, draft.values, contract, errors, resolutionOptions)}
    ${paired && draft.keepOriginalResolution ? '<p class="help-text">Images without a recorded or valid size use the chosen resolution above.</p>' : ""}`,
    "", Boolean(errors.width || errors.height || errors[inputs.resolution?.id]),
  ) : "";
  return `<div class="dialog-frame rerun-dialog-frame" data-control-context="rerun">
    <header class="dialog-header"><div><h2>Prompt Re-run</h2><p data-rerun-summary aria-live="polite">${escapeHtml(previewSummary(preview, draft))}</p></div><button type="button" class="icon-button source-picker-dialog-close" data-rerun-action="close" aria-label="Close Prompt Re-run" ${loading ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button></header>
    <div class="rerun-dialog-content" ${loading ? "inert" : ""}>
      ${sourcePickerMarkup({ selectedGenerationTargetCount: rerunVariants(draft).length }, draft.source ? [draft.source] : [], draft.sourceKey, loading, { idPrefix: "rerun-" })}
      ${fieldError(errors, "source")}${fieldError(errors, "checkpoints")}
      <div class="rerun-sections">${creativeDirectionMarkup(draft, errors)}${loras}${resolution}</div>
      <div class="rerun-options">
        ${inputs.seed ? `<fieldset class="field rerun-seed"><legend>Seed</legend>
          <label class="rerun-check"><input type="radio" name="rerun_seed" value="random" ${draft.seedMode === "random" ? "checked" : ""} /><span>Random per image</span></label>
          <label class="rerun-check"><input type="radio" name="rerun_seed" value="original" ${draft.seedMode === "original" ? "checked" : ""} /><span>Reuse original seed</span></label></fieldset>` : ""}
        <label class="field rerun-quantity">Generations per prompt<input type="number" name="rerun_quantity" min="${MIN_GENERATION_QUANTITY}" max="${draft.seedMode === "original" ? 1 : MAX_GENERATION_QUANTITY}" step="1" value="${draft.quantity}" ${draft.seedMode === "original" ? "disabled" : ""} />${fieldError(errors, "quantity")}</label>
      </div>
      <div class="rerun-destination">
        <label class="field">Folder name<input type="text" name="rerun_folder" maxlength="100" value="${escapeHtml(draft.folderName)}" aria-invalid="${Boolean(errors.folder)}" /><small class="muted">Created inside ${escapeHtml(parentName)}</small>${fieldError(errors, "folder")}</label>
        <label class="rerun-check"><input type="checkbox" name="rerun_skip_duplicates" ${draft.skipDuplicates ? "checked" : ""} /><span>Skip duplicate prompts</span></label>
      </div>
      <p class="field-error" data-operation-error role="alert">${escapeHtml(error)}</p>
    </div>
    <footer class="dialog-actions rerun-dialog-footer"><div class="rerun-total" role="status">${promptRerunSummaryMarkup(draft, preview, errors)}</div><div class="rerun-footer-buttons"><button type="button" class="button secondary" data-rerun-action="close" ${loading ? "disabled" : ""}>Cancel</button><button type="button" class="button primary" data-rerun-action="submit" ${blocked ? "disabled" : ""}>${promptRerunSubmitLabel(draft, preview, loading)}</button></div></footer>
  </div>`;
}

// Apply one form control's change to a draft, returning a new draft.
export function updateRerunDraft(draft, name, value) {
  const next = { ...draft, values: structuredClone(draft.values), selections: structuredClone(draft.selections) };
  if (name === "rerun_folder") next.folderName = String(value);
  else if (name === "rerun_quantity") next.quantity = clampGenerationQuantity(value);
  else if (name === "rerun_keep_resolution") next.keepOriginalResolution = Boolean(value);
  else if (name === "rerun_skip_duplicates") next.skipDuplicates = Boolean(value);
  else if (name === "rerun_refine") {
    next.refine = Boolean(value);
    if (next.refine) next.sectionOpen = { ...next.sectionOpen, "creative-direction": true };
  }
  else if (name === "rerun_direction") next.creativeDirection = String(value);
  else if (name === "rerun_instructions") next.instructions = String(value);
  else if (name === "rerun_think") next.think = Boolean(value);
  else if (name === "rerun_seed") {
    next.seedMode = value === "original" ? "original" : "random";
    if (next.seedMode === "original") next.quantity = 1;
  }
  return next;
}

// DOM controller. Shared pickers receive callbacks into this session, never the panel.
export function createPromptRerun(dialog, deps) {
  let draft = null;
  let preview = null;
  let selectionBody = null;
  let loading = false;
  let error = "";
  let sessionToken = 0;
  let sourceToken = 0;
  let recentTimer = null;

  const render = () => {
    if (!draft) return;
    const errors = preview ? validatePromptRerunDraft(draft, preview) : {};
    const inputs = rerunInputs(draft.source?.interface || draft.source?.contract);
    if (errors.width || errors.height || errors[inputs.resolution?.id]) draft.sectionOpen.resolution = true;
    for (const control of inputs.loras) if (errors[control.id]) draft.sectionOpen[`lora-${control.id}`] = true;
    const focused = dialog.contains(document.activeElement) ? document.activeElement : null;
    const selector = focused?.id ? `#${CSS.escape(focused.id)}` : focused?.name
      ? `[name="${focused.name}"]${["radio", "checkbox"].includes(focused.type) ? `[value="${CSS.escape(focused.value)}"]` : ""}` : null;
    const position = focused?.type === "text" ? focused.selectionStart : null;
    const scrollTop = dialog.querySelector(".rerun-dialog-content")?.scrollTop || 0;
    const preprocessorOpen = dialog.querySelector(".prompt-preprocessor")?.open || false;
    dialog.innerHTML = promptRerunMarkup(draft, preview, { parentName: deps.collectionName(draft.parentCollectionId), loading, error });
    dialog.querySelector(".prompt-preprocessor").open = preprocessorOpen || Boolean(draft.refine && errors.instructions);
    dialog.querySelector(".rerun-dialog-content").scrollTop = scrollTop;
    if (selector) {
      const replacement = dialog.querySelector(selector);
      replacement?.focus({ preventScroll: true });
      if (position !== null) replacement?.setSelectionRange(position, position);
    }
  };

  function refreshDerived() {
    if (!draft) return;
    const errors = preview ? validatePromptRerunDraft(draft, preview) : {};
    if (draft.refine && errors.instructions) dialog.querySelector(".prompt-preprocessor").open = true;
    for (const slot of dialog.querySelectorAll("[data-rerun-error]")) {
      if (slot.closest(".rerun-total")) continue;
      slot.textContent = errors[slot.dataset.rerunError] || "";
      const field = slot.closest("label")?.querySelector("input, textarea");
      if (field) field.setAttribute("aria-invalid", String(Boolean(errors[slot.dataset.rerunError])));
    }
    const inputs = rerunInputs(draft.source?.interface || draft.source?.contract);
    for (const slot of dialog.querySelectorAll("[data-control-error]")) {
      const id = slot.dataset.controlError;
      const key = id === inputs.width?.id ? "width" : id === inputs.height?.id ? "height" : id;
      slot.textContent = errors[key] || "";
    }
    for (const field of dialog.querySelectorAll("[data-control-id]")) {
      const id = field.dataset.controlId;
      const key = id === inputs.width?.id ? "width" : id === inputs.height?.id ? "height" : id;
      field.setAttribute("aria-invalid", String(Boolean(errors[key])));
    }
    if (errors.width || errors.height || errors[inputs.resolution?.id]) setSectionOpen("resolution", true);
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

  function setSectionOpen(key, open) {
    if (!draft) return;
    draft.sectionOpen[key] = open;
    const section = dialog.querySelector(`[data-control-section="${CSS.escape(key)}"]`);
    section?.classList.toggle("is-expanded", open);
    section?.querySelector(".control-section-trigger")?.setAttribute("aria-expanded", String(open));
    const body = section?.querySelector(".control-section-body");
    body?.setAttribute("aria-hidden", String(!open));
    body?.toggleAttribute("inert", !open);
  }

  async function open({ body }) {
    const session = ++sessionToken;
    sourceToken += 1;
    selectionBody = structuredClone(body);
    preview = null;
    error = "";
    loading = false;
    draft = promptRerunDraft(deps.initial());
    render();
    dialog.oncancel = (event) => { if (loading) event.preventDefault(); };
    dialog.onclose = () => {
      sessionToken += 1;
      sourceToken += 1;
      clearTimeout(recentTimer);
      draft = null;
      deps.closePickers?.();
      deps.onClose?.();
    };
    dialog.showModal();
    dialog.querySelector("#rerun-workflow-source")?.focus();
    try {
      const result = await deps.api("/api/gallery/prompt-rerun/preview", { method: "POST", body: JSON.stringify(body) });
      if (session !== sessionToken) return;
      preview = result;
    } catch (failure) {
      if (session !== sessionToken) return;
      error = failure.message || "The selected prompts could not be read.";
    }
    // Preview arrivals must not replace an opener underneath a stacked picker.
    refreshDerived();
    const errorSlot = dialog.querySelector("[data-operation-error]");
    if (errorSlot) errorSlot.textContent = error;
  }

  async function applySource(selection, session = sessionToken, force = false) {
    if (!draft || loading || session !== sessionToken) return false;
    const request = ++sourceToken;
    const changed = selection.sourceKey !== draft.sourceKey;
    const source = changed || force ? await deps.loadSource(selection.sourceKey) : draft.source;
    if (session !== sessionToken || request !== sourceToken || !draft) return false;
    if (changed || force) {
      draft = retargetRerunDraft(draft, source, deps.savedParameters(selection.sourceKey, source), selection.modelSelectionsBySource[selection.sourceKey]);
      const extras = deps.sourceSettings?.(selection.sourceKey) || {};
      draft.loraMemory = structuredClone(extras.loraMemory || {});
      draft.loraImages = structuredClone(extras.loraImages || {});
      draft.recentResolutions = structuredClone(extras.recentResolutions || []);
      clearTimeout(recentTimer);
    } else {
      draft.selections = structuredClone(selection.modelSelectionsBySource[draft.sourceKey] || {});
    }
    for (const selector of sourceModelSelectors(source)) {
      const values = draft.selections[selector.parameter_id] || [];
      if (values.length && !values.includes(draft.values[selector.parameter_id])) draft.values[selector.parameter_id] = values[0];
    }
    draft.checkpointTiers = structuredClone(selection.checkpointTiers);
    error = "";
    render();
    return true;
  }

  async function submit() {
    if (!draft || loading || !preview || Object.keys(validatePromptRerunDraft(draft, preview)).length) return;
    const session = sessionToken;
    loading = true;
    error = "";
    render();
    const body = promptRerunRequest(draft, selectionBody);
    try {
      const result = await deps.submit(body, promptRerunPlannedTotal(draft, preview));
      if (session !== sessionToken) return;
      loading = false;
      if (result) dialog.close();
      else render();
    } catch (failure) {
      if (session !== sessionToken || !draft) return;
      loading = false;
      if (["source_republished", "source_unavailable"].includes(failure.code)) {
        try {
          await applySource({ sourceKey: draft.sourceKey, modelSelectionsBySource: { [draft.sourceKey]: draft.selections }, checkpointTiers: draft.checkpointTiers }, session, true);
        } catch { /* Keep the failed draft available for correction. */ }
      }
      if (session !== sessionToken || !draft) return;
      error = failure.message || "Prompt Re-run could not be queued.";
      render();
    }
  }

  dialog.addEventListener("click", (event) => {
    const action = event.target.closest("[data-rerun-action]")?.dataset.rerunAction;
    if (!action) return;
    event.preventDefault();
    if (action === "close" && !loading) dialog.close();
    else if (action === "submit") void submit();
    else if (action === "reset-instructions" && draft && !loading) {
      draft.instructions = draft.defaultInstructions;
      dialog.querySelector('[name="rerun_instructions"]').value = draft.instructions;
      refreshDerived();
    }
  });
  const handle = (event) => {
    const target = event.target;
    if (!draft || !target.name?.startsWith("rerun_") || loading) return;
    draft = updateRerunDraft(draft, target.name, target.type === "checkbox" ? target.checked : target.value);
    if (target.type === "text" || target.type === "number" || target.tagName === "TEXTAREA") refreshDerived();
    else render();
  };
  dialog.addEventListener("input", (event) => { if (["text", "number", "textarea"].includes(event.target.type)) handle(event); });
  dialog.addEventListener("change", handle);
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.matches('input[type="text"], input[type="number"]')) {
      event.preventDefault();
      void submit();
    }
  });

  function recordResolution(value) {
    clearTimeout(recentTimer);
    recentTimer = null;
    if (draft && !loading) draft.recentResolutions = recordRecentResolution(draft.recentResolutions, value);
  }

  return {
    open, isOpen: () => dialog.open,
    close: () => { if (!loading && dialog.open) dialog.close(); },
    toggleSection: (trigger) => setSectionOpen(trigger.closest("[data-control-section]").dataset.controlSection, trigger.getAttribute("aria-expanded") !== "true"),
    sourcePickerContext: () => {
      if (!draft || loading) return null;
      const session = sessionToken;
      return {
        owner: "rerun", sourceKey: draft.sourceKey,
        selections: structuredClone(draft.selections), checkpointTiers: structuredClone(draft.checkpointTiers),
        sources: () => deps.sources().filter((source) => (source.output_kind || "image") === "image").map((source) => source.source_key === draft?.sourceKey ? { ...source, ...draft.source } : source),
        apply: (selection) => applySource(selection, session),
        cancel: () => { sourceToken += 1; },
      };
    },
    loraContext: (id) => {
      if (!draft || loading) return null;
      const control = rerunInputs(draft.source?.interface || draft.source?.contract).loras.find((item) => item.id === id);
      if (!control) return null;
      return { control, sourceKey: draft.sourceKey, sourceName: draft.source.display_name,
        publicationRevision: draft.source.revision, values: draft.values[id], memory: draft.loraMemory[id] || {},
        images: draft.loraImages[id] || {}, subjectAvailable: null };
    },
    applyLoras: (id, values, memory, key) => {
      if (!draft || loading || draft.sourceKey !== key) throw new Error("The workflow changed. Reopen the LoRA manager.");
      draft.values[id] = structuredClone(values);
      draft.loraMemory[id] = structuredClone(memory);
      render();
    },
    onLoraImages: (key, id, images) => {
      if (draft?.sourceKey !== key) return;
      draft.loraImages[id] = structuredClone(images);
      render();
    },
    resolutionContext: () => {
      if (!draft || loading) return null;
      const owner = draft.source;
      const inputs = rerunInputs(draft.source?.interface || draft.source?.contract);
      return {
        values: draft.values, recent: draft.recentResolutions,
        set: (width, height) => {
          if (draft?.source !== owner || (inputs.width && inputs.height && draft.keepOriginalResolution)) return;
          if (inputs.width && inputs.height) {
            draft.values[inputs.width.id] = width;
            draft.values[inputs.height.id] = height;
          } else if (inputs.resolution) draft.values[inputs.resolution.id] = { width, height };
          refreshDerived();
        },
        record: recordResolution,
        remove: (width, height) => {
          clearTimeout(recentTimer);
          draft.recentResolutions = removeRecentResolution(draft.recentResolutions, width, height);
        },
        queueRecord: (value, after) => {
          clearTimeout(recentTimer);
          recentTimer = setTimeout(() => { if (draft?.source === owner) { recordResolution(value); after(); } }, 600);
        },
      };
    },
  };
}
