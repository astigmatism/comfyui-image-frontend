import {
  controlPresentation,
  escapeHtml,
  formatLocalDate,
  interfaceInputs,
  isAdvancedInput,
  resolutionConstraints,
  resolutionGridConstraints,
  resolutionSummary,
  seedAllowsRandom,
  seedFormValue,
  sortInterfaceInputs,
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
          <label class="field"><span>New password</span><input name="new_password" type="password" minlength="8" autocomplete="new-password" required /></label>
          <label class="field"><span>Confirm new password</span><input name="confirm_password" type="password" minlength="8" autocomplete="new-password" required /></label>
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
        <button type="button" class="button low favorites-launch-button" data-action="open-favorites" aria-label="Favorites"><span aria-hidden="true">♡</span><span class="favorites-launch-label">Favorites</span></button>
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
      <dialog id="photo-viewer" class="photo-viewer" aria-label="Full-screen image viewer"></dialog>
      <dialog id="favorites-dialog" class="favorites-dialog"></dialog>
      <dialog id="admin-dialog" class="admin-dialog"></dialog>
      <div id="toast-region" class="toast-region" aria-live="polite" aria-atomic="true"></div>
    </div>`;
}

export function generationPanelMarkup(state, profile, contract) {
  const comfy = state.services.find((item) => item.service === "comfyui");
  const clientErrors = state.fieldErrors || {};
  const sources = state.sources || state.workflows || [];
  const activeKey = state.activeSourceKey || state.activeProfileId;
  const values = state.parameters || state.controls || {};
  const inputs = sortInterfaceInputs(interfaceInputs(contract));
  const basic = inputs.filter((item) => !isAdvancedInput(item));
  const advanced = inputs.filter((item) => isAdvancedInput(item));
  const advancedHasError = advanced.some((item) => clientErrors[item.id]);
  const sourceUnavailable = Boolean(profile && profile.available === false);
  const sourceUnresolved =
    !activeKey || !profile || !contract || state.sourceDetailLoading || state.sourceDetailError;
  const disabled =
    state.submitting ||
    state.sourceCatalogStatus === "loading" ||
    sourceUnresolved ||
    sourceUnavailable ||
    comfy?.available === false ||
    Object.keys(clientErrors).length > 0;
  const sourceOptions = sources
    .map(
      (item) => {
        const key = sourceKey(item);
        const instance = item.instance_id ? ` · ${item.instance_id}` : "";
        const suffix = item.available === false ? " — Unavailable" : item.cached ? " — Cached" : "";
        return `<option value="${escapeHtml(key)}" ${key === activeKey ? "selected" : ""}>${escapeHtml(`${item.display_name}${instance}${suffix}`)}</option>`;
      },
    )
    .join("");
  const presets = contract?.presets || [];
  return `
    <div class="panel-layout">
      <div class="panel-fixed">
        <button id="generate-button" class="button primary full" data-action="generate" ${disabled ? "disabled" : ""}>${state.submitting ? "Queueing…" : "Generate"}</button>
        <label class="field compact"><span>Generation source</span><select id="workflow-source" ${sources.length && !state.submitting ? "" : "disabled"}><option value="">${sourceSelectorLabel(state, sources)}</option>${sourceOptions}</select></label>
        ${presets.length ? presetMarkup(presets, state.selectedPreset) : ""}
        ${sourceStateMarkup(state, profile)}
        ${state.formError ? `<div class="form-error summary" role="alert">${escapeHtml(state.formError)}</div>` : ""}
      </div>
      <div class="panel-scroll" id="panel-scroll">
        ${groupedControlsMarkup(basic, values, contract, clientErrors)}
        ${advanced.length ? `<details class="advanced-group"${advancedHasError ? " open" : ""}><summary>Advanced</summary><div class="advanced-controls">${groupedControlsMarkup(advanced, values, contract, clientErrors)}</div></details>` : ""}
        ${controlEmptyStateMarkup(state, profile, contract)}
      </div>
    </div>`;
}

function sourceKey(source) {
  return source?.source_key || source?.profile_id || "";
}

function sourceSelectorLabel(state, sources) {
  if (state.sourceCatalogStatus === "loading" && !sources.length) return "Discovering published sources…";
  if (state.sourceCatalogStatus === "error" && !sources.length) return "Source discovery unavailable";
  return sources.length ? "Select a source" : "No published sources";
}

function warningText(warning) {
  if (typeof warning === "string") return warning;
  if (warning?.message && warning?.code) return `${warning.code}: ${warning.message}`;
  return warning?.message || warning?.code || JSON.stringify(warning);
}

function sourceStateMarkup(state, source) {
  const notices = [];
  if (state.sourceCatalogStatus === "loading") {
    notices.push(
      `<div class="source-notice" role="status">${source ? "Refreshing published generation sources…" : "Discovering published generation sources…"}</div>`,
    );
  }
  if (state.sourceCatalogStatus === "error") {
    notices.push(
      `<div class="source-notice warning" role="status">${escapeHtml(state.sourceCatalogMessage || "Source discovery is temporarily unavailable.")}</div>`,
    );
  }
  if (state.sourceDetailLoading) {
    notices.push('<div class="source-notice" role="status">Loading the selected source interface…</div>');
  }
  if (state.sourceDetailError) {
    notices.push(`<div class="source-notice error" role="alert">${escapeHtml(state.sourceDetailError)}</div>`);
  }
  if (source?.available === false) {
    notices.push(
      `<div class="source-notice error" role="status">${escapeHtml(source.message || "This source is unavailable and cannot generate images.")}</div>`,
    );
  }
  if (source?.cached) {
    notices.push(
      '<div class="source-notice" role="status">Using the last fully validated cached source descriptor.</div>',
    );
  }
  const warnings = Array.isArray(source?.warnings) ? source.warnings.filter(Boolean) : [];
  if (warnings.length) {
    notices.push(
      `<div class="source-notice warning" role="status"><strong>Source warning${warnings.length === 1 ? "" : "s"}</strong><ul>${warnings.map((warning) => `<li>${escapeHtml(warningText(warning))}</li>`).join("")}</ul></div>`,
    );
  }
  return notices.join("");
}

function controlEmptyStateMarkup(state, source, contract) {
  if (contract) return "";
  if (state.sourceCatalogStatus === "loading" || state.sourceDetailLoading) {
    return '<p class="empty-copy">Published controls are loading.</p>';
  }
  if (!source && !(state.sources || state.workflows || []).length) {
    return '<p class="empty-copy">No published generation sources are available.</p>';
  }
  return '<p class="empty-copy">Choose an available generation source to load its controls.</p>';
}

function groupedControlsMarkup(inputs, values, contract, errors) {
  const resolutionPair = pairedResolutionInputs(inputs, values, contract);
  const firstResolutionInput = resolutionPair
    ? inputs.find((input) => input === resolutionPair.width || input === resolutionPair.height)
    : null;
  let currentGroup = null;
  let markup = "";
  for (const input of inputs) {
    if (resolutionPair && (input === resolutionPair.width || input === resolutionPair.height)) {
      if (input !== firstResolutionInput) continue;
    }
    const group = String(input.group || "");
    if (group !== currentGroup) {
      if (currentGroup !== null) markup += "</section>";
      markup += `<section class="control-group" data-interface-group="${escapeHtml(group)}">${group ? `<h3 class="control-group-heading">${escapeHtml(group)}</h3>` : ""}`;
      currentGroup = group;
    }
    markup +=
      resolutionPair && input === firstResolutionInput
        ? pairedResolutionMarkup(resolutionPair.width, resolutionPair.height, values, contract, errors)
        : controlMarkup(input, values, contract, errors);
  }
  if (currentGroup !== null) markup += "</section>";
  return markup;
}

function pairedResolutionInputs(inputs, values, contract) {
  const visible = inputs.filter((input) => controlPresentation(input, values, contract?.capability_states || {}).visible);
  const widths = visible.filter((input) => input.type === "integer" && input.semantic_role === "width");
  const heights = visible.filter((input) => input.type === "integer" && input.semantic_role === "height");
  return widths.length === 1 && heights.length === 1 ? { width: widths[0], height: heights[0] } : null;
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
  const label = control.id === "prompt.text" && !control.semantic_role ? "Prompt" : control.label || control.id;
  const descriptionId = description ? `${id}-description` : null;
  const errorId = error ? `${id}-error` : null;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ");
  const shared = `data-control-id="${escapeHtml(control.id)}" ${disabled ? "disabled" : ""} ${required ? 'required aria-required="true"' : ""} ${error ? 'aria-invalid="true"' : ""} ${describedBy ? `aria-describedby="${describedBy}"` : ""}`;
  const common = `id="${id}" ${shared}`;
  const labelContent = `${escapeHtml(label)}${required ? '<b class="required-mark" aria-hidden="true">*</b>' : ""}`;
  let input = "";
  let field = "";
  switch (control.type) {
    case "multiline_string":
      input = `<textarea ${common} rows="${escapeHtml(control.ui?.rows || (control.id === "prompt.text" ? 6 : 3))}">${escapeHtml(value ?? "")}</textarea>`;
      break;
    case "string":
      input =
        control.semantic_role === "positive_prompt"
          ? `<textarea ${common} rows="${escapeHtml(control.ui?.rows || 6)}">${escapeHtml(value ?? "")}</textarea>`
          : `<input ${common} type="text" value="${escapeHtml(value ?? "")}" />`;
      break;
    case "integer":
      input = `<input ${common} type="number" value="${escapeHtml(value ?? "")}" min="${escapeHtml(controlConstraint(control, "minimum") ?? "")}" max="${escapeHtml(controlConstraint(control, "maximum") ?? "")}" step="${escapeHtml(controlConstraint(control, "step") ?? (control.type === "integer" ? 1 : "any"))}" />`;
      break;
    case "number": {
      const minimum = controlConstraint(control, "minimum");
      const maximum = controlConstraint(control, "maximum");
      const step = controlConstraint(control, "step") ?? "any";
      const exact = `<input ${common} data-number-entry type="number" value="${escapeHtml(value ?? "")}" min="${escapeHtml(minimum ?? "")}" max="${escapeHtml(maximum ?? "")}" step="${escapeHtml(step)}" aria-label="${escapeHtml(label)}" />`;
      if (
        minimum !== undefined &&
        minimum !== null &&
        minimum !== "" &&
        maximum !== undefined &&
        maximum !== null &&
        maximum !== "" &&
        Number.isFinite(Number(minimum)) &&
        Number.isFinite(Number(maximum)) &&
        Number(maximum) > Number(minimum)
      ) {
        input = `<div class="number-control"><input id="${id}-slider" ${shared} data-number-slider type="range" value="${escapeHtml(value ?? minimum)}" min="${escapeHtml(minimum)}" max="${escapeHtml(maximum)}" step="${escapeHtml(step)}" aria-label="${escapeHtml(label)} slider" />${exact}</div>`;
        field = `<fieldset class="field semantic-fieldset" ${describedBy ? `aria-describedby="${describedBy}"` : ""}><legend>${labelContent}</legend>${input}</fieldset>`;
      } else {
        input = exact;
      }
      break;
    }
    case "seed":
      input = seedMarkup(control, value, common, disabled);
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
    case "choice": {
      const options = optionValues(control);
      const selected = options.some((option) => option.value === value) ? value : control.default;
      input = `<select ${common}>${options
        .map(
          (option) =>
            `<option value="${escapeHtml(option.value)}" ${option.value === selected ? "selected" : ""}>${escapeHtml(option.label)}</option>`,
        )
        .join("")}</select>`;
      break;
    }
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
  const assistant =
    control.semantic_role === "positive_prompt" || control.id === "prompt.text" ? promptAssistantMarkup() : "";
  return `<div class="control-block ${disabled ? "is-disabled" : ""}" data-control-block="${escapeHtml(control.id)}" data-control-group="${escapeHtml(control.group || "")}">
    ${field}
    ${description ? `<p class="help-text" id="${descriptionId}">${escapeHtml(description)}</p>` : ""}
    ${error ? `<p class="field-error" id="${errorId}" role="alert">${escapeHtml(error)}</p>` : ""}
    ${assistant}
  </div>`;
}

function controlConstraint(control, name) {
  return control?.[name] ?? control?.constraints?.[name];
}

function seedMarkup(control, value, common, disabled) {
  const seed = seedFormValue(control, value);
  const random = seed.mode === "random";
  const modeOptions = seedAllowsRandom(control)
    ? `<option value="random" ${random ? "selected" : ""}>Random</option><option value="fixed" ${!random ? "selected" : ""}>Fixed</option>`
    : '<option value="fixed" selected>Fixed</option>';
  return `<div class="seed-control">
    <select data-seed-mode="${escapeHtml(control.id)}" ${disabled ? "disabled" : ""} aria-label="${escapeHtml(control.label)} mode">${modeOptions}</select>
    <input ${common} type="text" inputmode="numeric" pattern="-?[0-9]*" value="${random ? "" : escapeHtml(seed.value)}" ${random ? "disabled" : ""} data-minimum="${escapeHtml(controlConstraint(control, "minimum") ?? "")}" data-maximum="${escapeHtml(controlConstraint(control, "maximum") ?? "")}" aria-label="${escapeHtml(control.label)} value" />
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
  const canvas = resolutionCanvasMarkup({ controlId: control.id, value, grid, disabled });
  return `<div class="resolution-editor">
    ${canvas}
    <div class="resolution-control">
      <label for="${id}-width"><span>Width</span><input id="${id}-width" ${base} data-resolution-part="width" type="number" value="${value?.width ?? ""}" min="${limits.minimumWidth ?? ""}" max="${limits.maximumWidth ?? ""}" step="${limits.widthStep}" /></label>
      <span aria-hidden="true">×</span>
      <label for="${id}-height"><span>Height</span><input id="${id}-height" ${base} data-resolution-part="height" type="number" value="${value?.height ?? ""}" min="${limits.minimumHeight ?? ""}" max="${limits.maximumHeight ?? ""}" step="${limits.heightStep}" /></label>
    </div>
  </div>`;
}

function pairedResolutionMarkup(widthControl, heightControl, values, contract, errors) {
  const capabilityStates = contract?.capability_states || {};
  const widthPresentation = controlPresentation(widthControl, values, capabilityStates);
  const heightPresentation = controlPresentation(heightControl, values, capabilityStates);
  const disabled = !widthPresentation.enabled || !heightPresentation.enabled;
  const required = widthPresentation.required || heightPresentation.required;
  const widthError = errors[widthControl.id];
  const heightError = errors[heightControl.id];
  const widthDescription = widthPresentation.reason || widthControl.description;
  const heightDescription = heightPresentation.reason || heightControl.description;
  const widthId = `control-${widthControl.id.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  const heightId = `control-${heightControl.id.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  const widthDescriptionId = widthDescription ? `${widthId}-description` : null;
  const heightDescriptionId = heightDescription ? `${heightId}-description` : null;
  const widthErrorId = widthError ? `${widthId}-error` : null;
  const heightErrorId = heightError ? `${heightId}-error` : null;
  const describedBy = [widthDescriptionId, heightDescriptionId, widthErrorId, heightErrorId]
    .filter(Boolean)
    .join(" ");
  const grid = resolutionGridConstraints({
    constraints: {
      maximum_width: controlConstraint(widthControl, "maximum"),
      maximum_height: controlConstraint(heightControl, "maximum"),
    },
  });
  const value = { width: values[widthControl.id], height: values[heightControl.id] };
  const canvas = resolutionCanvasMarkup({
    widthId: widthControl.id,
    heightId: heightControl.id,
    value,
    grid,
    disabled,
  });
  const widthInput = pairedResolutionInputMarkup(
    widthControl,
    "width",
    value.width,
    widthPresentation,
    widthError,
    widthDescriptionId,
    widthErrorId,
    widthId,
  );
  const heightInput = pairedResolutionInputMarkup(
    heightControl,
    "height",
    value.height,
    heightPresentation,
    heightError,
    heightDescriptionId,
    heightErrorId,
    heightId,
  );
  return `<div class="control-block ${disabled ? "is-disabled" : ""}" data-resolution-pair-block="${escapeHtml(`${widthControl.id}:${heightControl.id}`)}" data-control-group="${escapeHtml(widthControl.group || heightControl.group || "")}">
    <fieldset class="field semantic-fieldset" ${describedBy ? `aria-describedby="${describedBy}"` : ""}>
      <legend>Resolution${required ? '<b class="required-mark" aria-hidden="true">*</b>' : ""}</legend>
      <div class="resolution-editor">
        ${canvas}
        <div class="resolution-control">
          ${widthInput}
          <span aria-hidden="true">×</span>
          ${heightInput}
        </div>
      </div>
    </fieldset>
  </div>`;
}

function pairedResolutionInputMarkup(
  control,
  axis,
  value,
  presentation,
  error,
  descriptionId,
  errorId,
  id,
) {
  const description = presentation.reason || control.description;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ");
  const required = presentation.required;
  const label = control.label || (axis === "width" ? "Width" : "Height");
  return `<div class="resolution-axis-field" data-control-block="${escapeHtml(control.id)}">
    <label for="${id}"><span>${escapeHtml(label)}</span><input id="${id}" data-control-id="${escapeHtml(control.id)}" data-resolution-axis="${axis}" type="number" value="${escapeHtml(value ?? "")}" min="${escapeHtml(controlConstraint(control, "minimum") ?? "")}" max="${escapeHtml(controlConstraint(control, "maximum") ?? "")}" step="${escapeHtml(controlConstraint(control, "step") ?? 1)}" ${presentation.enabled ? "" : "disabled"} ${required ? 'required aria-required="true"' : ""} ${error ? 'aria-invalid="true"' : ""} ${describedBy ? `aria-describedby="${describedBy}"` : ""} /></label>
    ${description ? `<p class="help-text" id="${descriptionId}">${escapeHtml(description)}</p>` : ""}
    ${error ? `<p class="field-error" id="${errorId}" role="alert">${escapeHtml(error)}</p>` : ""}
  </div>`;
}

function resolutionCanvasMarkup({ controlId = null, widthId = null, heightId = null, value, grid, disabled }) {
  const summary = resolutionSummary(value?.width, value?.height);
  const positionX = resolutionPosition(summary.width, grid.minimumWidth, grid.maximumWidth);
  const positionY = resolutionPosition(summary.height, grid.minimumHeight, grid.maximumHeight);
  const gridStepX = (grid.widthStep / (grid.maximumWidth - grid.minimumWidth)) * 100;
  const gridStepY = (grid.heightStep / (grid.maximumHeight - grid.minimumHeight)) * 100;
  const disabledAttribute = disabled ? "disabled" : "";
  const identity = controlId
    ? `data-control-id="${escapeHtml(controlId)}"`
    : `data-resolution-width-id="${escapeHtml(widthId)}" data-resolution-height-id="${escapeHtml(heightId)}"`;
  return `<div class="resolution-canvas" data-resolution-grid ${identity} data-resolution-disabled="${disabled}" data-resolution-min-width="${grid.minimumWidth}" data-resolution-max-width="${grid.maximumWidth}" data-resolution-min-height="${grid.minimumHeight}" data-resolution-max-height="${grid.maximumHeight}" data-resolution-width-step="${grid.widthStep}" data-resolution-height-step="${grid.heightStep}" style="--resolution-x: ${positionX}%; --resolution-y: ${positionY}%; --resolution-x-mid: ${positionX / 2}%; --resolution-y-mid: ${positionY / 2}%; --resolution-grid-step-x: ${gridStepX}%; --resolution-grid-step-y: ${gridStepY}%; --resolution-canvas-aspect: ${grid.maximumWidth - grid.minimumWidth} / ${grid.maximumHeight - grid.minimumHeight};" aria-label="Resolution grid from ${grid.minimumWidth} by ${grid.minimumHeight} to ${grid.maximumWidth} by ${grid.maximumHeight}">
      <div class="resolution-selection" aria-hidden="true"></div>
      <button type="button" class="resolution-handle resolution-handle-both" data-resolution-handle="both" ${disabledAttribute} aria-label="Adjust width and height. ${summary.width} by ${summary.height} pixels. Use the arrow keys."></button>
      <button type="button" class="resolution-handle resolution-handle-width" data-resolution-handle="width" ${disabledAttribute} aria-label="Adjust width. ${summary.width} pixels. Use the left and right arrow keys."></button>
      <button type="button" class="resolution-handle resolution-handle-height" data-resolution-handle="height" ${disabledAttribute} aria-label="Adjust height. ${summary.height} pixels. Use the up and down arrow keys."></button>
    </div>
    <p class="resolution-summary" data-resolution-summary aria-live="polite">${summary.text}</p>`;
}

function resolutionPosition(value, minimum, maximum) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || maximum <= minimum) return 0;
  return Math.max(0, Math.min(100, ((numeric - minimum) / (maximum - minimum)) * 100));
}

function optionValues(control) {
  if (control.type === "choice") {
    return (control.choices || []).map((item) => ({
      value: item.value,
      label: item.label,
    }));
  }
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
  const sourceName = generationSourceName(generation);
  const stateClass = String(generation.status || "unknown").replaceAll("_", "-");
  const media = hasImage
    ? `<img loading="lazy" src="${escapeHtml(artifact.thumbnail_url || artifact.content_url)}" alt="${escapeHtml(`${sourceName}, ${statusLabel(generation.status)}`)}" />`
    : statusPlaceholderMarkup(generation);
  const statusOverlay = generation.status === "succeeded" ? "" : `<div class="media-status">${escapeHtml(statusLabel(generation.status))}</div>`;
  const finalCount = Number(generation.final_artifact_count) || 0;
  const imageCount = generation.image_count ?? (finalCount > 0 ? finalCount : generation.artifact_count ?? 0);
  const count = imageCount > 1 ? `<div class="batch-count" aria-label="${imageCount} images">${imageCount}</div>` : "";
  const width = positiveNumber(generation.expected_width) || positiveNumber(artifact?.width);
  const height = positiveNumber(generation.expected_height) || positiveNumber(artifact?.height);
  const aspectStyle = width && height ? ` style="--gallery-media-aspect: ${width} / ${height}"` : "";
  const cancel = generation.cancel_allowed
    ? `<button type="button" class="button card-cancel-button" data-action="cancel-generation" data-generation-id="${escapeHtml(generation.id)}">Cancel</button>`
    : "";
  return `<article class="gallery-card status-${stateClass}" data-generation-id="${escapeHtml(generation.id)}">
    <div class="card-media-frame"${aspectStyle}>
      ${hasImage ? `<button type="button" class="card-media" data-action="open-photo" data-generation-id="${escapeHtml(generation.id)}" aria-label="View ${escapeHtml(sourceName)} full screen">${media}${statusOverlay}${count}</button>` : `<div class="card-media" aria-label="${escapeHtml(`${sourceName}, ${statusLabel(generation.status)}`)}">${media}${statusOverlay}${count}</div>`}
      ${cancel}
    </div>
    ${cardFooterMarkup(generation)}
  </article>`;
}

function positiveNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function generationSourceName(generation) {
  return (
    generation?.generation_source?.display_name ||
    generation?.workflow_display_name ||
    generation?.generation_source?.source_key ||
    "Published source"
  );
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
  const sourceName = generationSourceName(generation);
  const artifact = generation.display_artifact;
  const download = artifact?.kind === "image"
    ? `<a class="download-button" href="${escapeHtml(artifact.content_url)}" download aria-label="Download current image" title="Download current image">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v12m-5-5 5 5 5-5M5 20h14" /></svg>
    </a>`
    : `<button type="button" class="download-button" disabled aria-label="Download unavailable" title="No image is available to download">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v12m-5-5 5 5 5-5M5 20h14" /></svg>
    </button>`;
  return `<footer class="card-footer"><button type="button" class="card-metadata" data-action="open-detail" data-generation-id="${escapeHtml(generation.id)}" title="Open generation details for ${escapeHtml(sourceName)}">${escapeHtml(sourceName)}</button><div class="card-actions">${download}${favoriteButtonMarkup(generation)}<button type="button" class="recall-button" data-action="recall" data-generation-id="${escapeHtml(generation.id)}" ${generation.recall_available ? "" : "disabled"} aria-label="Recall settings" title="${escapeHtml(generation.recall_unavailable_reason || "Load this exact request into the generation panel")}">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5m4-1v5l3 2" /></svg>
  </button></div></footer>`;
}

export function photoViewerMarkup(generation, navigation = {}) {
  const artifact = generation?.display_artifact;
  const sourceName = generationSourceName(generation);
  const hasImage = artifact?.kind === "image";
  const active = ["queued", "dispatching", "running", "cancel_requested"].includes(generation?.status);
  const status = generation?.current_stage_label || statusLabel(generation?.status);
  const media = hasImage
    ? `<img src="${escapeHtml(artifact.content_url)}" alt="${escapeHtml(`${sourceName}, ${statusLabel(generation.status)}`)}" />`
    : `<div class="photo-viewer-placeholder"><strong>No image is available.</strong></div>`;
  return `<div class="photo-viewer-frame" data-photo-generation-id="${escapeHtml(generation?.id || "")}">
    <div class="photo-viewer-media">${media}</div>
    <button type="button" class="photo-viewer-close photo-viewer-control" data-action="close-photo" aria-label="Close full-screen viewer">×</button>
    <button type="button" class="photo-viewer-nav photo-viewer-older photo-viewer-control" data-action="navigate-photo" data-direction="older" ${navigation.hasOlder ? "" : "disabled"} aria-label="View older generation">‹</button>
    <button type="button" class="photo-viewer-nav photo-viewer-newer photo-viewer-control" data-action="navigate-photo" data-direction="newer" ${navigation.hasNewer ? "" : "disabled"} aria-label="View newer generation">›</button>
    ${active ? `<div class="photo-viewer-status" role="status">${escapeHtml(status)}</div>` : ""}
  </div>`;
}

export function favoriteButtonMarkup(generation) {
  const active = Boolean(generation.is_favorite);
  const label = active ? "Remove from Favorites" : "Add to Favorites";
  return `<button type="button" class="favorite-button" data-action="toggle-favorite" data-generation-id="${escapeHtml(generation.id)}" aria-label="${label}" aria-pressed="${active}" title="${label}">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 21s-7.2-4.4-9.5-8.7C.7 8.8 2.2 4.5 6.1 3.4c2.2-.6 4.5.2 5.9 2 1.4-1.8 3.7-2.6 5.9-2 3.9 1.1 5.4 5.4 3.6 8.9C19.2 16.6 12 21 12 21Z" /></svg>
  </button>`;
}

export function favoritesMarkup(favorites, nextCursor = null) {
  const list = favorites.length
    ? `<div class="favorites-list">${favorites.map(favoriteItemMarkup).join("")}</div>`
    : '<section class="empty-favorites"><div class="empty-favorite-heart" aria-hidden="true">♡</div><h3>No favorites yet</h3><p>Use the heart on a gallery item to save it here.</p></section>';
  return `<div class="dialog-frame favorites-frame">
    <header class="dialog-header"><div><h2>Favorites</h2><p>Your saved generations, visible only to you.</p></div><button type="button" class="icon-button" data-action="close-favorites" aria-label="Close Favorites">×</button></header>
    <div class="favorites-content">${list}${nextCursor ? '<div class="favorites-load-more"><button type="button" class="button secondary" data-action="load-more-favorites">Load more</button></div>' : ""}</div>
    <footer class="dialog-actions"><button type="button" class="button primary" data-action="close-favorites">Close</button></footer>
  </div>`;
}

function favoriteItemMarkup(favorite) {
  const generation = favorite.generation;
  const artifact = generation.display_artifact;
  const sourceName = generationSourceName(generation);
  const media = artifact?.kind === "image"
    ? `<img loading="lazy" src="${escapeHtml(artifact.thumbnail_url || artifact.content_url)}" alt="${escapeHtml(`Favorite from ${sourceName}`)}" />`
    : `<div class="favorite-placeholder"><span aria-hidden="true">◇</span><strong>No retained image</strong></div>`;
  return `<article class="favorite-item" data-favorite-id="${escapeHtml(favorite.id)}" data-generation-id="${escapeHtml(generation.id)}">
    <div class="favorite-thumbnail">${media}</div>
    <div class="favorite-details">
      <div class="favorite-heading"><div><h3>${escapeHtml(sourceName)}</h3><p>Generated ${escapeHtml(formatLocalDate(generation.accepted_at))} · ${escapeHtml(statusLabel(generation.status))}</p></div></div>
      <p class="favorite-prompt">${escapeHtml(favorite.final_prompt || "No prompt was retained.")}</p>
      <div class="favorite-actions">
        <button type="button" class="button secondary" data-action="recall-favorite" data-generation-id="${escapeHtml(generation.id)}" ${generation.recall_available ? "" : "disabled"} title="${escapeHtml(generation.recall_unavailable_reason || "Load this exact request into the generation panel")}">Recall</button>
        <button type="button" class="button destructive" data-action="delete-favorite" data-generation-id="${escapeHtml(generation.id)}">Delete</button>
      </div>
    </div>
  </article>`;
}

export function detailMarkup(detail) {
  const artifacts = detail.artifacts || [];
  const imageGroups = detailImageGroups(detail, artifacts);
  const imageCount = Object.values(imageGroups).reduce((count, images) => count + images.length, 0);
  const source = detail.generation_source || {};
  const revision = source.revision || source;
  const legacyWorkflow = detail.workflow || {};
  const effective = detail.effective_parameters || detail.effective_controls || {};
  const warnings = messageValues(detail.warnings);
  const errors = [...messageValues(detail.errors), ...(detail.error_message ? [detail.error_message] : [])];
  const sourceName = generationSourceName(detail);
  return `<form method="dialog" class="dialog-frame">
    <header class="dialog-header"><div><h2>${escapeHtml(sourceName)}</h2><p>${escapeHtml(statusLabel(detail.status))}</p></div><button class="icon-button" value="close" aria-label="Close details">×</button></header>
    <div class="detail-content">
      ${generationInputsMarkup(detail)}
      ${messageAlertMarkup("warning", warnings, "Generation warnings")}
      ${messageAlertMarkup("error", errors, "Generation errors")}
      <div class="result-image-groups" aria-label="Generation images">${imageCount ? [
        detailImageGroupMarkup("Primary result", "The workflow-authored final output.", imageGroups.final),
        detailImageGroupMarkup("Prototypes and earlier passes", "Useful preview stages retained by the workflow.", imageGroups.preview),
        detailImageGroupMarkup("Comparisons and alternates", "Authored comparison stages and alternate treatments.", imageGroups.comparison),
        detailImageGroupMarkup("Auxiliary images", "Other images intentionally published by the workflow.", imageGroups.auxiliary),
        detailImageGroupMarkup("Additional images", "Native ComfyUI images outside the authored publisher hierarchy.", imageGroups.additional),
      ].join("") : '<section class="artifact-viewer"><div class="status-placeholder"><strong>No image artifact was retained.</strong></div></section>'}</div>
      ${resultDetailsMarkup("Declared output metadata", detail.declared_outputs, "No declared outputs were returned.")}
      ${resultSectionMarkup("Additional outputs", detail.unmapped_outputs, "No additional native outputs were returned.")}
      ${hasResult(detail.warnings) || hasResult(detail.errors) ? `<details class="provenance"><summary>Warning and error details</summary><pre class="result-json">${escapeHtml(prettyJson({ warnings: detail.warnings || [], errors: detail.errors || [] }))}</pre></details>` : ""}
      <section class="timeline"><h3>Artifact timeline</h3>${artifacts.length ? `<ol>${artifacts.map((artifact) => `<li><span class="timeline-dot state-${escapeHtml(artifact.state)}"></span><div><strong>${escapeHtml(artifact.role || artifact.output_id || "Output")}</strong><small>${escapeHtml(artifact.state || "available")} · sequence ${artifact.sequence ?? "—"}${Number.isInteger(artifact.batch_index) ? ` · batch ${artifact.batch_index + 1}` : ""}</small></div></li>`).join("")}</ol>` : '<p class="muted">No application-owned artifact timeline was recorded.</p>'}</section>
      <details class="provenance"><summary>Technical provenance</summary><dl>
        <dt>Source key</dt><dd><code>${escapeHtml(source.source_key || source.source_id || legacyWorkflow.workflow_id || "—")}</code></dd>
        <dt>Instance</dt><dd>${escapeHtml(source.instance_id || "—")}</dd>
        <dt>Publication</dt><dd><code>${escapeHtml(revision.publication_id || legacyWorkflow.workflow_version || "—")}</code></dd>
        <dt>Workflow hash</dt><dd><code>${escapeHtml(revision.workflow_sha256 || legacyWorkflow.ui_graph_sha256 || "—")}</code></dd>
        <dt>API hash</dt><dd><code>${escapeHtml(revision.api_sha256 || legacyWorkflow.api_graph_sha256 || "—")}</code></dd>
        <dt>Manifest hash</dt><dd><code>${escapeHtml(revision.manifest_sha256 || legacyWorkflow.contract_sha256 || "—")}</code></dd>
        <dt>ComfyUI prompt ID</dt><dd><code>${escapeHtml(detail.prompt_id || "Not assigned")}</code></dd>
        ${hasResult(detail.comfyui_status) ? `<dt>ComfyUI status</dt><dd><pre class="result-json">${escapeHtml(prettyJson(detail.comfyui_status))}</pre></dd>` : ""}
        <dt>Effective parameters</dt><dd><pre class="result-json">${escapeHtml(prettyJson(effective))}</pre></dd>
        ${detail.resolved_seeds ? `<dt>Resolved seeds</dt><dd><pre class="result-json">${escapeHtml(prettyJson(detail.resolved_seeds))}</pre></dd>` : ""}
        ${detail.final_prompt ? `<dt>Final submitted prompt</dt><dd class="provenance-prompt">${escapeHtml(detail.final_prompt)}</dd>` : ""}
      </dl></details>
      <details class="provenance raw-history"><summary>Raw ComfyUI history</summary><pre class="result-json">${escapeHtml(prettyJson(detail.raw_history || {}))}</pre></details>
    </div>
    <footer class="dialog-actions">
      ${detail.cancel_allowed ? `<button type="button" class="button secondary" data-action="cancel-generation" data-generation-id="${escapeHtml(detail.id)}">Cancel generation</button>` : ""}
      <button type="button" class="button destructive" data-action="delete-generation" data-generation-id="${escapeHtml(detail.id)}" ${detail.delete_pending ? "disabled" : ""}>${detail.delete_pending ? "Deletion pending…" : "Delete permanently"}</button>
      <button class="button primary" value="close">Close</button>
    </footer>
  </form>`;
}

function generationInputsMarkup(detail) {
  const effective = detail.effective_parameters || detail.effective_controls || {};
  const definitions = Array.isArray(detail.input_definitions) ? detail.input_definitions : [];
  const known = new Set(definitions.map((input) => String(input.id)));
  const inputs = [
    ...definitions,
    ...Object.keys(effective)
      .filter((id) => !known.has(id))
      .map((id) => ({ id, label: humanizeInputId(id) })),
  ];
  const promptInput = inputs.find(
    (input) => input.semantic_role === "positive_prompt" || input.id === "prompt.text" || input.id === "prompt",
  );
  const prompt = detail.final_prompt || (promptInput ? effective[promptInput.id] : "");
  const widthInput = inputs.find((input) => input.semantic_role === "width");
  const heightInput = inputs.find((input) => input.semantic_role === "height");
  const resolutionInput = inputs.find((input) => input.type === "resolution");
  const omitted = new Set([promptInput?.id, widthInput?.id, heightInput?.id, resolutionInput?.id].filter(Boolean));
  const facts = [];
  const resolution = resolutionDisplayValue(effective, widthInput, heightInput, resolutionInput);
  if (resolution) facts.push({ label: "Resolution", value: resolution });
  for (const input of inputs) {
    if (omitted.has(input.id) || !Object.hasOwn(effective, input.id)) continue;
    const value = detail.resolved_seeds?.[input.id] ?? effective[input.id];
    facts.push({ label: input.label || humanizeInputId(input.id), value: inputDisplayValue(input, value) });
  }
  const promptMarkup = prompt
    ? `<div class="generation-prompt"><span>Prompt</span><p>${escapeHtml(prompt)}</p></div>`
    : `<div class="generation-prompt empty"><span>Prompt</span><p>No prompt was retained.</p></div>`;
  const factsMarkup = facts.length
    ? `<dl class="generation-input-grid">${facts.map((fact) => `<div><dt>${escapeHtml(fact.label)}</dt><dd>${escapeHtml(fact.value)}</dd></div>`).join("")}</dl>`
    : '<p class="muted generation-input-empty">No additional submitted inputs were retained.</p>';
  return `<section class="generation-inputs" aria-labelledby="generation-inputs-heading">
    <h3 id="generation-inputs-heading">Generation inputs</h3>
    ${promptMarkup}
    ${factsMarkup}
  </section>`;
}

function resolutionDisplayValue(effective, widthInput, heightInput, resolutionInput) {
  if (widthInput && heightInput && effective[widthInput.id] != null && effective[heightInput.id] != null) {
    return `${effective[widthInput.id]} × ${effective[heightInput.id]}`;
  }
  const value = resolutionInput ? effective[resolutionInput.id] : null;
  if (value && typeof value === "object" && value.width != null && value.height != null) {
    return `${value.width} × ${value.height}`;
  }
  return "";
}

function inputDisplayValue(input, value) {
  if (input.type === "choice" && Array.isArray(input.choices)) {
    const choice = input.choices.find((item) => (item?.value ?? item) === value);
    if (choice && typeof choice === "object") return choice.label || choice.value;
  }
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (value == null || value === "") return "Not set";
  if (typeof value === "object") return prettyJson(value);
  return String(value);
}

function humanizeInputId(value) {
  const words = String(value || "Input").replaceAll(/[._-]+/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function messageValues(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.flatMap(messageValues);
  if (typeof value === "string") return [value];
  if (typeof value === "object" && value.message) {
    return [value.code ? `${value.code}: ${value.message}` : String(value.message)];
  }
  if (typeof value === "object") {
    return Object.entries(value).map(([key, item]) => `${key}: ${warningText(item)}`);
  }
  return [String(value)];
}

function messageAlertMarkup(kind, messages, heading) {
  const unique = [...new Set(messages.filter(Boolean))];
  if (!unique.length) return "";
  return `<div class="inline-alert ${kind} result-messages" role="${kind === "error" ? "alert" : "status"}"><strong>${escapeHtml(heading)}</strong><ul>${unique.map((message) => `<li>${escapeHtml(message)}</li>`).join("")}</ul></div>`;
}

function hasResult(value) {
  if (Array.isArray(value)) return value.length > 0;
  return Boolean(value && typeof value === "object" && Object.keys(value).length);
}

function resultSectionMarkup(heading, value, emptyMessage) {
  return `<section class="result-section"><h3>${escapeHtml(heading)}</h3>${hasResult(value) ? `<pre class="result-json">${escapeHtml(prettyJson(value))}</pre>` : `<p class="muted">${escapeHtml(emptyMessage)}</p>`}</section>`;
}

function resultDetailsMarkup(heading, value, emptyMessage) {
  return `<details class="provenance result-details"><summary>${escapeHtml(heading)}</summary>${hasResult(value) ? `<pre class="result-json">${escapeHtml(prettyJson(value))}</pre>` : `<p class="muted">${escapeHtml(emptyMessage)}</p>`}</details>`;
}

function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2) ?? "{}";
  } catch {
    return String(value ?? "{}");
  }
}

function detailImageGroups(detail, artifacts) {
  const groups = { final: [], preview: [], comparison: [], auxiliary: [], additional: [] };
  const declared = declaredOutputList(detail.declared_outputs);
  const artifactImages = artifacts.filter((item) => item.kind === "image" && item.content_url);
  const usedArtifactIds = new Set();
  const usedFallbackKeys = new Set();

  for (const output of declared) {
    const outputId = String(output.id || output.output_id || "");
    const role = ["final", "preview", "comparison", "auxiliary"].includes(output.role)
      ? output.role
      : "auxiliary";
    const candidates = [];
    collectOutputImages(output.artifacts, output.label || outputId || role, candidates);
    for (const artifact of artifactImages) {
      if (String(artifact.output_id || "") === outputId) candidates.push(artifact);
    }
    for (const image of uniqueLogicalImages(candidates)) {
      rememberLogicalImage(image, usedArtifactIds, usedFallbackKeys);
      groups[role].push({
        ...image,
        output_id: image.output_id || outputId,
        role,
        label: output.label || outputId || roleLabel(role),
        description: output.description || "",
      });
    }
  }

  for (const artifact of artifactImages) {
    if (logicalImageWasUsed(artifact, usedArtifactIds, usedFallbackKeys)) continue;
    groups.additional.push({ ...artifact, label: artifact.output_id || "Native output" });
    rememberLogicalImage(artifact, usedArtifactIds, usedFallbackKeys);
  }
  const nativeImages = [];
  collectOutputImages(detail.unmapped_outputs, "Native output", nativeImages);
  for (const image of uniqueLogicalImages(nativeImages)) {
    if (logicalImageWasUsed(image, usedArtifactIds, usedFallbackKeys)) continue;
    groups.additional.push(image);
    rememberLogicalImage(image, usedArtifactIds, usedFallbackKeys);
  }
  return groups;
}

function declaredOutputList(value) {
  if (Array.isArray(value)) return value.filter((item) => item && typeof item === "object");
  if (!value || typeof value !== "object") return [];
  return Object.entries(value)
    .filter(([, item]) => item && typeof item === "object")
    .map(([id, item]) => ({ id, ...item }));
}

function uniqueLogicalImages(images) {
  const seen = new Set();
  return images.filter((image) => {
    if (!image?.content_url) return false;
    const key = logicalImageKey(image);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function logicalImageKey(image) {
  if (image.id) return `id:${image.id}`;
  return [image.output_id || "", image.batch_index ?? "", image.content_url || ""].join(":");
}

function rememberLogicalImage(image, ids, fallbackKeys) {
  if (image.id) ids.add(String(image.id));
  else fallbackKeys.add(logicalImageKey(image));
}

function logicalImageWasUsed(image, ids, fallbackKeys) {
  return image.id ? ids.has(String(image.id)) : fallbackKeys.has(logicalImageKey(image));
}

function collectOutputImages(value, label, images) {
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectOutputImages(item, `${label} ${index + 1}`, images));
    return;
  }
  if (!value || typeof value !== "object") return;
  const contentUrl = value.content_url || value.asset_url;
  if (typeof contentUrl === "string" && contentUrl.startsWith("/")) {
    images.push({
      ...value,
      content_url: contentUrl,
      role: value.role || value.output_id || label,
      state: value.state || "available",
    });
  }
  for (const [key, item] of Object.entries(value)) {
    if (["content_url", "asset_url", "thumbnail_url"].includes(key)) continue;
    collectOutputImages(item, value.output_id || value.role || key || label, images);
  }
}

function detailImageGroupMarkup(heading, description, images) {
  if (!images.length) return "";
  return `<section class="result-image-group"><header><h3>${escapeHtml(heading)}</h3><p>${escapeHtml(description)}</p></header><div class="artifact-viewer">${images.map(detailImageMarkup).join("")}</div></section>`;
}

function detailImageMarkup(image) {
  const label = image.label || image.output_id || roleLabel(image.role) || "Generated image";
  const state = image.state || "available";
  const batch = Number.isInteger(image.batch_index) ? ` · batch ${image.batch_index + 1}` : "";
  const description = image.description ? `<span>${escapeHtml(image.description)}</span>` : "";
  return `<figure><a class="artifact-image-link" href="${escapeHtml(image.content_url)}" target="_blank" rel="noopener"><img src="${escapeHtml(image.content_url)}" alt="${escapeHtml(`${label}, ${state}`)}" /></a><figcaption><span><strong>${escapeHtml(label)}</strong> · ${escapeHtml(state)}${batch}</span>${description}<a class="artifact-download" href="${escapeHtml(image.content_url)}" download>Download image</a></figcaption></figure>`;
}

function roleLabel(role) {
  return {
    final: "Final",
    preview: "Prototype",
    comparison: "Comparison",
    auxiliary: "Auxiliary",
    unmapped: "Native output",
  }[role] || "Generated image";
}

export function serviceBannerMarkup(services) {
  const comfy = services.find((item) => item.service === "comfyui");
  if (!comfy || comfy.available) return "";
  return `<div class="service-banner" role="status"><strong>ComfyUI unavailable.</strong><span>${escapeHtml(comfy.message || "Generation is paused; history remains available.")}</span></div>`;
}
