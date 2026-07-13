import {
  controlPresentation,
  escapeHtml,
  footerText,
  resolutionConstraints,
  resolutionGridConstraints,
  resolutionSummary,
  statusLabel,
} from "./lib.mjs";

export function loginMarkup(appTitle) {
  return `
    <main class="auth-page">
      <section class="auth-card" aria-labelledby="login-heading">
        <div class="brand-mark" aria-hidden="true"></div>
        <h1 id="login-heading">${escapeHtml(appTitle)}</h1>
        <p class="muted">Sign in with your local appliance account.</p>
        <form id="login-form" novalidate>
          <label class="field"><span>Username</span><input name="username" autocomplete="username" required /></label>
          <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required /></label>
          <div id="auth-error" class="form-error" role="alert"></div>
          <button class="button primary full" type="submit">Sign in</button>
        </form>
      </section>
    </main>`;
}

export function passwordChangeMarkup(appTitle, forced = false) {
  return `
    <main class="auth-page">
      <section class="auth-card" aria-labelledby="password-heading">
        <h1 id="password-heading">${forced ? "Choose a new password" : "Change password"}</h1>
        <p class="muted">${forced ? `The temporary password for ${escapeHtml(appTitle)} must be replaced before continuing.` : "Use a long, unique local password."}</p>
        <form id="password-form" novalidate>
          ${forced ? "" : '<label class="field"><span>Current password</span><input name="current_password" type="password" autocomplete="current-password" required /></label>'}
          <label class="field"><span>New password</span><input name="new_password" type="password" minlength="12" autocomplete="new-password" required /></label>
          <label class="field"><span>Confirm new password</span><input name="confirm_password" type="password" minlength="12" autocomplete="new-password" required /></label>
          <div id="auth-error" class="form-error" role="alert"></div>
          <div class="button-row">
            ${forced ? "" : '<button type="button" class="button secondary" data-action="cancel-password">Cancel</button>'}
            <button class="button primary" type="submit">Save password</button>
          </div>
        </form>
      </section>
    </main>`;
}

export function shellMarkup(state) {
  const admin = state.session.user.role === "admin";
  return `
    <div class="app-shell ${state.panelOpen ? "panel-open" : ""}">
      <header class="topbar">
        <button class="icon-button panel-toggle" data-action="toggle-panel" aria-label="Open generation controls" aria-expanded="${state.panelOpen}">☰</button>
        <div class="app-title">${escapeHtml(state.session.app_title)}</div>
        <div class="topbar-spacer"></div>
        <label class="scale-control">
          <span>Gallery scale</span>
          <input id="gallery-scale" type="range" min="0" max="100" step="1" value="${state.galleryScale}" aria-valuetext="${state.galleryScale}%" />
        </label>
        <details class="account-menu">
          <summary aria-label="Account menu">${escapeHtml(state.session.user.username)}</summary>
          <div class="menu-popover" role="menu">
            <button role="menuitem" data-action="change-password">Change password</button>
            ${admin ? '<button role="menuitem" data-action="open-admin">Administration</button>' : ""}
            <button role="menuitem" data-action="logout">Sign out</button>
          </div>
        </details>
      </header>
      <aside class="control-panel" aria-label="Generation controls">
        <div id="generation-panel"></div>
      </aside>
      <button class="panel-scrim" data-action="close-panel" aria-label="Close generation controls"></button>
      <main class="gallery-viewport" id="gallery-viewport">
        <div id="service-banner"></div>
        <div id="gallery" class="gallery-grid" aria-live="polite"></div>
        <div id="gallery-sentinel" class="gallery-sentinel"><button class="button secondary" data-action="load-more">Load more</button></div>
      </main>
      <dialog id="detail-dialog" class="detail-dialog"></dialog>
      <dialog id="admin-dialog" class="admin-dialog"></dialog>
      <div id="toast-region" class="toast-region" aria-live="polite" aria-atomic="true"></div>
    </div>`;
}

export function generationPanelMarkup(state, profile, contract) {
  const comfy = state.services.find((item) => item.service === "comfyui");
  const clientErrors = state.fieldErrors || {};
  const disabled =
    state.submitting ||
    !profile ||
    comfy?.available === false ||
    Object.keys(clientErrors).length > 0;
  const sourceOptions = state.workflows
    .map(
      (item) =>
        `<option value="${escapeHtml(item.profile_id)}" ${item.profile_id === state.activeProfileId ? "selected" : ""}>${escapeHtml(item.display_name)}</option>`,
    )
    .join("");
  const presets = contract?.presets || [];
  const basic = (contract?.controls || []).filter((item) => item.tier === "basic");
  const advanced = (contract?.controls || []).filter((item) => item.tier === "advanced");
  return `
    <div class="panel-layout">
      <div class="panel-fixed">
        <button id="generate-button" class="button primary full" data-action="generate" ${disabled ? "disabled" : ""}>${state.submitting ? "Queueing…" : "Generate"}</button>
        <label class="field compact"><span>Generation source</span><select id="workflow-source" ${state.workflows.length ? "" : "disabled"}><option value="">${state.workflows.length ? "Select a source" : "No compatible workflows"}</option>${sourceOptions}</select></label>
        ${presets.length ? presetMarkup(presets, state.selectedPreset) : ""}
        ${state.formError ? `<div class="form-error summary" role="alert">${escapeHtml(state.formError)}</div>` : ""}
      </div>
      <div class="panel-scroll" id="panel-scroll">
        ${basic.map((control) => controlMarkup(control, state.controls, contract, clientErrors)).join("")}
        ${advanced.length ? `<details class="advanced-group"><summary>Advanced</summary><div class="advanced-controls">${advanced.map((control) => controlMarkup(control, state.controls, contract, clientErrors)).join("")}</div></details>` : ""}
        ${!contract ? '<p class="empty-copy">Choose a generation source to load its controls.</p>' : ""}
      </div>
    </div>`;
}

function presetMarkup(presets, selected) {
  return `<label class="field compact"><span>Preset</span><select id="preset-select"><option value="">Workflow defaults</option>${presets
    .map(
      (preset) =>
        `<option value="${escapeHtml(preset.id)}" ${selected === preset.id ? "selected" : ""}>${escapeHtml(preset.label)}</option>`,
    )
    .join("")}</select></label>`;
}

export function controlMarkup(control, values, contract, errors = {}) {
  const presentation = controlPresentation(
    control,
    values,
    contract?.capability_states || {},
  );
  if (!presentation.visible) return "";
  const id = `control-${control.id.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  const value = values[control.id];
  const disabled = !presentation.enabled;
  const required = presentation.required;
  const error = errors[control.id];
  const description = presentation.reason || control.description;
  const label = control.id === "prompt.text" ? "Prompt" : control.label;
  const descriptionId = description ? `${id}-description` : null;
  const errorId = error ? `${id}-error` : null;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ");
  const common = `id="${id}" data-control-id="${escapeHtml(control.id)}" ${disabled ? "disabled" : ""} ${required ? 'required aria-required="true"' : ""} ${error ? 'aria-invalid="true"' : ""} ${describedBy ? `aria-describedby="${describedBy}"` : ""}`;
  const labelContent = `${escapeHtml(label)}${required ? '<b class="required-mark" aria-hidden="true">*</b>' : ""}`;
  let input = "";
  let field = "";
  switch (control.type) {
    case "multiline_string":
      input = `<textarea ${common} rows="${control.ui?.rows || (control.id === "prompt.text" ? 6 : 3)}">${escapeHtml(value ?? "")}</textarea>`;
      break;
    case "string":
      input = `<input ${common} type="text" value="${escapeHtml(value ?? "")}" />`;
      break;
    case "integer":
    case "number":
      input = `<input ${common} type="number" value="${value ?? ""}" min="${control.constraints?.minimum ?? ""}" max="${control.constraints?.maximum ?? ""}" step="${control.constraints?.step ?? (control.type === "integer" ? 1 : "any")}" />`;
      break;
    case "seed":
      input = seedMarkup(control, value, common);
      field = `<fieldset class="field semantic-fieldset" ${describedBy ? `aria-describedby="${describedBy}"` : ""}><legend>${labelContent}</legend>${input}</fieldset>`;
      break;
    case "boolean":
      input = `<label class="switch"><input ${common} aria-labelledby="${id}-label" type="checkbox" ${value ? "checked" : ""} /><span aria-hidden="true"></span><em>${value ? "On" : "Off"}</em></label>`;
      field = `<div class="field"><span id="${id}-label">${labelContent}</span>${input}</div>`;
      break;
    case "enum":
    case "asset_selector":
      input = `<select ${common}>${optionValues(control)
        .map(
          (option) =>
            `<option value="${escapeHtml(option.value)}" ${option.value === value ? "selected" : ""}>${escapeHtml(option.label)}</option>`,
        )
        .join("")}</select>`;
      break;
    case "image_upload":
    case "mask_upload":
      input = uploadMarkup(control, value, common);
      field = `<div class="field"><label for="${id}">${labelContent}</label>${input}</div>`;
      break;
    case "resolution":
      input = resolutionMarkup(control, value, disabled, required, error, describedBy, id);
      field = `<fieldset class="field semantic-fieldset" ${describedBy ? `aria-describedby="${describedBy}"` : ""}><legend>${labelContent}</legend>${input}</fieldset>`;
      break;
    case "array":
    case "output_role_set":
      input = `<textarea ${common} rows="3" data-json-control="true">${escapeHtml(JSON.stringify(value ?? []))}</textarea>`;
      break;
    case "object":
      input = `<textarea ${common} rows="4" data-json-control="true">${escapeHtml(JSON.stringify(value ?? {}, null, 2))}</textarea>`;
      break;
    default:
      input = `<p class="control-unavailable">Unsupported semantic control.</p>`;
      field = `<div class="field"><span>${labelContent}</span>${input}</div>`;
  }
  if (!field) field = `<label class="field" for="${id}"><span>${labelContent}</span>${input}</label>`;
  const assistant = control.id === "prompt.text" ? promptAssistantMarkup() : "";
  return `<div class="control-block ${disabled ? "is-disabled" : ""}" data-control-block="${escapeHtml(control.id)}">
    ${field}
    ${description ? `<p class="help-text" id="${descriptionId}">${escapeHtml(description)}</p>` : ""}
    ${error ? `<p class="field-error" id="${errorId}" role="alert">${escapeHtml(error)}</p>` : ""}
    ${assistant}
  </div>`;
}

function seedMarkup(control, value, common) {
  const random = value === "random" || value?.mode === "random";
  return `<div class="seed-control">
    <select data-seed-mode="${escapeHtml(control.id)}" ${common.includes("disabled") ? "disabled" : ""} aria-label="${escapeHtml(control.label)} mode"><option value="random" ${random ? "selected" : ""}>Random</option><option value="fixed" ${!random ? "selected" : ""}>Fixed</option></select>
    <input ${common} type="number" value="${random ? "" : value ?? ""}" ${random ? "disabled" : ""} min="${control.constraints?.minimum ?? 0}" max="${control.constraints?.maximum ?? Number.MAX_SAFE_INTEGER}" step="1" aria-label="${escapeHtml(control.label)} value" />
  </div>`;
}

function uploadMarkup(control, value, common) {
  const kind = control.type === "mask_upload" ? "masks" : "images";
  return `<div class="upload-control">
    <input ${common} type="file" accept="image/*" data-upload-kind="${kind}" />
    ${value ? `<div class="upload-chip"><img src="/api/uploads/${escapeHtml(value)}/content" alt="Selected ${escapeHtml(control.label)}" /><span>Uploaded asset selected</span><button type="button" class="button low" data-clear-upload="${escapeHtml(control.id)}">Remove</button></div>` : ""}
  </div>`;
}

function resolutionMarkup(control, value, disabled, required, error, describedBy, id) {
  const base = `data-control-id="${escapeHtml(control.id)}" ${disabled ? "disabled" : ""} ${required ? 'required aria-required="true"' : ""} ${error ? 'aria-invalid="true"' : ""} ${describedBy ? `aria-describedby="${describedBy}"` : ""}`;
  const limits = resolutionConstraints(control);
  const grid = resolutionGridConstraints(control);
  const summary = resolutionSummary(value?.width, value?.height);
  const positionX = resolutionPosition(summary.width, grid.minimumWidth, grid.maximumWidth);
  const positionY = resolutionPosition(summary.height, grid.minimumHeight, grid.maximumHeight);
  const gridStepX = (grid.widthStep / (grid.maximumWidth - grid.minimumWidth)) * 100;
  const gridStepY = (grid.heightStep / (grid.maximumHeight - grid.minimumHeight)) * 100;
  const disabledAttribute = disabled ? "disabled" : "";
  return `<div class="resolution-editor">
    <div class="resolution-canvas" data-resolution-grid data-control-id="${escapeHtml(control.id)}" data-resolution-disabled="${disabled}" data-resolution-min-width="${grid.minimumWidth}" data-resolution-max-width="${grid.maximumWidth}" data-resolution-min-height="${grid.minimumHeight}" data-resolution-max-height="${grid.maximumHeight}" data-resolution-width-step="${grid.widthStep}" data-resolution-height-step="${grid.heightStep}" style="--resolution-x: ${positionX}%; --resolution-y: ${positionY}%; --resolution-x-mid: ${positionX / 2}%; --resolution-y-mid: ${positionY / 2}%; --resolution-grid-step-x: ${gridStepX}%; --resolution-grid-step-y: ${gridStepY}%; --resolution-canvas-aspect: ${grid.maximumWidth - grid.minimumWidth} / ${grid.maximumHeight - grid.minimumHeight};" aria-label="Resolution grid from ${grid.minimumWidth} by ${grid.minimumHeight} to ${grid.maximumWidth} by ${grid.maximumHeight}">
      <div class="resolution-selection" aria-hidden="true"></div>
      <button type="button" class="resolution-handle resolution-handle-both" data-resolution-handle="both" ${disabledAttribute} aria-label="Adjust width and height. ${summary.width} by ${summary.height} pixels. Use the arrow keys."></button>
      <button type="button" class="resolution-handle resolution-handle-width" data-resolution-handle="width" ${disabledAttribute} aria-label="Adjust width. ${summary.width} pixels. Use the left and right arrow keys."></button>
      <button type="button" class="resolution-handle resolution-handle-height" data-resolution-handle="height" ${disabledAttribute} aria-label="Adjust height. ${summary.height} pixels. Use the up and down arrow keys."></button>
    </div>
    <p class="resolution-summary" data-resolution-summary aria-live="polite">${summary.text}</p>
    <div class="resolution-control">
      <label for="${id}-width"><span>Width</span><input id="${id}-width" ${base} data-resolution-part="width" type="number" value="${value?.width ?? ""}" min="${limits.minimumWidth ?? ""}" max="${limits.maximumWidth ?? ""}" step="${limits.widthStep}" /></label>
      <span aria-hidden="true">×</span>
      <label for="${id}-height"><span>Height</span><input id="${id}-height" ${base} data-resolution-part="height" type="number" value="${value?.height ?? ""}" min="${limits.minimumHeight ?? ""}" max="${limits.maximumHeight ?? ""}" step="${limits.heightStep}" /></label>
    </div>
  </div>`;
}

function resolutionPosition(value, minimum, maximum) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || maximum <= minimum) return 0;
  return Math.max(0, Math.min(100, ((numeric - minimum) / (maximum - minimum)) * 100));
}

function optionValues(control) {
  const values = control.options?.resolved_values || control.options?.values || [];
  return values.map((item) =>
    typeof item === "object" ? { value: item.value, label: item.label || item.value } : { value: item, label: item },
  );
}

function promptAssistantMarkup() {
  return `<details class="prompt-assistant" id="prompt-assistant">
    <summary>Prompt Assistant</summary>
    <div class="assistant-body">
      <label class="field"><span>Creative direction</span><textarea id="creative-direction" rows="3"></textarea></label>
      <fieldset class="compact-choice"><legend>Mode</legend><label><input type="radio" name="assistant-mode" value="refine" checked /> Refine current prompt</label><label><input type="radio" name="assistant-mode" value="create" /> Create from creative direction</label></fieldset>
      <div id="assistant-message" class="help-text"></div>
      <button type="button" class="button secondary" data-action="compose-prompt">Compose Prompt</button>
    </div>
  </details>`;
}

export function galleryMarkup(generations) {
  if (!generations.length) {
    return `<section class="empty-gallery"><h2>No generations yet</h2><p>Choose a source, set a prompt, and queue the first image.</p></section>`;
  }
  return generations.map(galleryCardMarkup).join("");
}

export function galleryCardMarkup(generation) {
  const artifact = generation.display_artifact;
  const hasImage = artifact?.kind === "image";
  const stateClass = generation.status.replaceAll("_", "-");
  const media = hasImage
    ? `<img loading="lazy" src="${escapeHtml(artifact.thumbnail_url || artifact.content_url)}" alt="${escapeHtml(`${generation.workflow_display_name}, ${statusLabel(generation.status)}, ${footerText("", generation.accepted_at).replace(/^ · /, "")}`)}" />`
    : statusPlaceholderMarkup(generation);
  const statusOverlay = generation.status === "succeeded" ? "" : `<div class="media-status">${escapeHtml(statusLabel(generation.status))}</div>`;
  const count = generation.final_artifact_count > 1 ? `<div class="batch-count" aria-label="${generation.final_artifact_count} final images">${generation.final_artifact_count}</div>` : "";
  const width = positiveNumber(generation.expected_width) || positiveNumber(artifact?.width);
  const height = positiveNumber(generation.expected_height) || positiveNumber(artifact?.height);
  const aspectStyle = width && height ? ` style="--gallery-media-aspect: ${width} / ${height}"` : "";
  const cancel = generation.cancel_allowed
    ? `<button type="button" class="button card-cancel-button" data-action="cancel-generation" data-generation-id="${escapeHtml(generation.id)}">Cancel</button>`
    : "";
  return `<article class="gallery-card status-${stateClass}" data-generation-id="${escapeHtml(generation.id)}">
    <div class="card-media-frame"${aspectStyle}>
      <button class="card-media" data-action="open-detail" data-generation-id="${escapeHtml(generation.id)}" aria-label="Open generation details">${media}${statusOverlay}${count}</button>
      ${cancel}
    </div>
    ${cardFooterMarkup(generation)}
  </article>`;
}

function positiveNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function statusPlaceholderMarkup(generation) {
  let label = generation.current_stage_label || statusLabel(generation.status);
  if (generation.status.startsWith("cancelled_")) label = "Cancelled generation";
  else if (generation.status.startsWith("failed_")) label = "Generation failed";
  else if (generation.status === "interrupted") label = "Generation interrupted";
  const queueCopy = generation.status === "queued" ? "<span>Waiting for a fair queue slot</span>" : "";
  return `<div class="status-placeholder"><div class="status-symbol" aria-hidden="true"></div><strong>${escapeHtml(label)}</strong>${queueCopy}</div>`;
}

export function cardFooterMarkup(generation) {
  return `<footer class="card-footer"><div class="card-metadata" title="${escapeHtml(generation.workflow_display_name)}">${escapeHtml(footerText(generation.workflow_display_name, generation.accepted_at))}</div><button class="recall-button" data-action="recall" data-generation-id="${escapeHtml(generation.id)}" ${generation.recall_available ? "" : "disabled"} title="${escapeHtml(generation.recall_unavailable_reason || "Load this exact request into the generation panel")}">Recall settings</button></footer>`;
}

export function detailMarkup(detail) {
  const artifacts = detail.artifacts || [];
  const images = artifacts.filter((item) => item.kind === "image");
  return `<form method="dialog" class="dialog-frame">
    <header class="dialog-header"><div><h2>${escapeHtml(detail.workflow_display_name)}</h2><p>${escapeHtml(statusLabel(detail.status))}</p></div><button class="icon-button" value="close" aria-label="Close details">×</button></header>
    <div class="detail-content">
      ${detail.error_message ? `<div class="inline-alert error" role="alert">${escapeHtml(detail.error_message)}</div>` : ""}
      <section class="artifact-viewer" aria-label="Retained images">${images.length ? images.map((artifact) => `<figure><img src="${escapeHtml(artifact.content_url)}" alt="${escapeHtml(`${artifact.role}, ${artifact.state}`)}" /><figcaption>${escapeHtml(artifact.role)} · ${escapeHtml(artifact.state)}</figcaption></figure>`).join("") : '<div class="status-placeholder"><strong>No image artifact was retained.</strong></div>'}</section>
      <section class="timeline"><h3>Checkpoint timeline</h3>${artifacts.length ? `<ol>${artifacts.map((artifact) => `<li><span class="timeline-dot state-${escapeHtml(artifact.state)}"></span><div><strong>${escapeHtml(artifact.role)}</strong><small>${escapeHtml(artifact.state)} · sequence ${artifact.sequence}${artifact.batch_index ? ` · batch ${artifact.batch_index + 1}` : ""}</small></div></li>`).join("")}</ol>` : '<p class="muted">No declared checkpoints were emitted.</p>'}</section>
      <details class="provenance"><summary>Technical provenance</summary><dl><dt>Workflow</dt><dd>${escapeHtml(detail.workflow.workflow_id)} ${escapeHtml(detail.workflow.workflow_version)}</dd><dt>UI graph</dt><dd><code>${escapeHtml(detail.workflow.ui_graph_sha256)}</code></dd><dt>API graph</dt><dd><code>${escapeHtml(detail.workflow.api_graph_sha256)}</code></dd><dt>Resolved seeds</dt><dd><code>${escapeHtml(JSON.stringify(detail.resolved_seeds))}</code></dd><dt>Final submitted prompt</dt><dd class="provenance-prompt">${escapeHtml(detail.final_prompt)}</dd></dl></details>
    </div>
    <footer class="dialog-actions">
      ${detail.cancel_allowed ? `<button type="button" class="button secondary" data-action="cancel-generation" data-generation-id="${escapeHtml(detail.id)}">Cancel generation</button>` : ""}
      <button type="button" class="button destructive" data-action="delete-generation" data-generation-id="${escapeHtml(detail.id)}" ${detail.delete_pending ? "disabled" : ""}>${detail.delete_pending ? "Deletion pending…" : "Delete permanently"}</button>
      <button class="button primary" value="close">Close</button>
    </footer>
  </form>`;
}

export function serviceBannerMarkup(services) {
  const comfy = services.find((item) => item.service === "comfyui");
  if (!comfy || comfy.available) return "";
  return `<div class="service-banner" role="status"><strong>ComfyUI unavailable.</strong><span>${escapeHtml(comfy.message || "Generation is paused; history remains available.")}</span></div>`;
}
