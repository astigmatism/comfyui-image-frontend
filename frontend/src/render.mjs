import {
  controlPresentation,
  escapeHtml,
  formatTimelineMonth,
  formatLocalDate,
  interfaceInputs,
  isAdvancedInput,
  resolutionConstraints,
  resolutionGridConstraints,
  resolutionSummary,
  seedAllowsRandom,
  seedFormValue,
  sortGenerationsNewestFirst,
  sortInterfaceInputs,
  statusLabel,
  validTimelineMonth,
} from "./lib.mjs";

const MAX_GENERATION_ETA_ANCHORS = 256;
const generationEtaAnchors = new Map();

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
      <dialog id="photo-viewer" class="photo-viewer" aria-label="Image viewer"><div class="photo-viewer-host"></div></dialog>
      <dialog id="favorites-dialog" class="favorites-dialog"></dialog>
      <dialog id="admin-dialog" class="admin-dialog"></dialog>
      <dialog id="prompt-editor-dialog" class="prompt-editor-dialog" aria-label="Focused prompt editor"></dialog>
      <dialog id="source-picker-dialog" class="source-picker-dialog" aria-label="Generation sources"></dialog>
      <div id="toast-region" class="toast-region" aria-live="polite" aria-atomic="true"></div>
    </div>`;
}

export function generationPanelMarkup(state, profile, contract) {
  const clientErrors = state.fieldErrors || {};
  const sources = state.sources || state.workflows || [];
  const activeKey = state.activeSourceKey || state.activeProfileId;
  const sharedSourceKeys = new Set(state.comparisonSourceKeys || []);
  sharedSourceKeys.delete(activeKey);
  const sharedSourceCount = sources.filter(
    (item) => item.available !== false && sharedSourceKeys.has(sourceKey(item)),
  ).length;
  const selectedSourceCount = activeKey ? sharedSourceCount + 1 : sharedSourceCount;
  const values = state.parameters || state.controls || {};
  const inputs = sortInterfaceInputs(interfaceInputs(contract));
  const basic = inputs.filter((item) => !isAdvancedInput(item));
  const advanced = inputs.filter((item) => isAdvancedInput(item));
  const advancedHasError = advanced.some((item) => clientErrors[item.id]);
  const disabled = generationSubmissionDisabled(state, profile, contract, clientErrors);
  const presets = contract?.presets || [];
  const sourceSelectorDisabled =
    !sources.length || (state.submitting && !state.autoGenerate);
  return `
    <div class="panel-layout">
      <div class="panel-fixed">
        <div class="generation-actions">
          <button id="generate-button" class="button primary full" data-action="generate" ${disabled ? "disabled" : ""}>${state.submitting ? (sharedSourceCount ? `Queueing ${selectedSourceCount}…` : "Queueing…") : "Generate"}</button>
          <div class="auto-generation-options">
            <label class="switch auto-generation-switch" for="auto-generate">
              <input id="auto-generate" type="checkbox" role="switch" ${state.autoGenerate ? "checked" : ""} />
              <span aria-hidden="true"></span>
              <em>Auto-generate</em>
            </label>
            <label class="switch auto-generation-switch" for="auto-generate-creative-direction">
              <input id="auto-generate-creative-direction" type="checkbox" role="switch" ${state.autoGenerateCreativeDirection ? "checked" : ""} />
              <span aria-hidden="true"></span>
              <em>Creative Direction</em>
            </label>
          </div>
        </div>
        ${sourcePickerMarkup(state, sources, activeKey, sharedSourceKeys, sourceSelectorDisabled)}
        ${presets.length ? presetMarkup(presets, state.selectedPreset) : ""}
        ${sourceStateMarkup(state, profile)}
        ${state.formError ? `<div class="form-error summary" role="alert">${escapeHtml(state.formError)}</div>` : ""}
      </div>
      <div class="panel-scroll" id="panel-scroll">
        ${collapsibleControlsMarkup(basic, values, contract, clientErrors, state.controlSectionOpen)}
        ${
          advanced.length
            ? controlSectionMarkup({
                key: "advanced",
                title: "Advanced",
                content: `<div class="advanced-controls">${groupedControlsMarkup(advanced, values, contract, clientErrors, { omitGroupHeadings: true })}</div>`,
                open: advancedHasError || controlSectionIsOpen(state.controlSectionOpen, "advanced", false),
                className: "advanced-group",
              })
            : ""
        }
        ${controlEmptyStateMarkup(state, profile, contract)}
      </div>
    </div>`;
}

export function generationSubmissionDisabled(state, profile, contract, clientErrors = {}) {
  return Boolean(
    state.autoGenerate || generationRequestBlocked(state, profile, contract, clientErrors),
  );
}

export function generationRequestBlocked(state, profile, contract, clientErrors = {}) {
  const services = state.services || [];
  const comfy = services.find((item) => item.service === "comfyui");
  const serviceStateBlocksGeneration =
    state.servicesStatus === undefined
      ? comfy?.available === false
      : state.servicesStatus !== "ready" || comfy?.available !== true;
  const sourceCatalogBlocksGeneration =
    state.sourceCatalogStatus !== undefined && state.sourceCatalogStatus !== "ready";
  const activeKey = state.activeSourceKey || state.activeProfileId;
  return Boolean(
    state.submitting ||
      state.imageUploadsPending > 0 ||
      sourceCatalogBlocksGeneration ||
      !activeKey ||
      !profile ||
      !contract ||
      state.sourceDetailLoading ||
      state.sourceDetailError ||
      profile.available === false ||
      serviceStateBlocksGeneration ||
      Object.keys(clientErrors).length > 0,
  );
}

function sourceKey(source) {
  return source?.source_key || source?.profile_id || "";
}

function sourceDisplayName(source) {
  if (!source) return "";
  const instanceId = String(source.instance_id || "").trim();
  const instance = instanceId && instanceId.toLowerCase() !== "default" ? ` · ${instanceId}` : "";
  const suffix = source.available === false ? " — Unavailable" : source.cached ? " — Cached" : "";
  return `${source.display_name}${instance}${suffix}`;
}

function sourceSelectorLabel(state, sources) {
  if (state.sourceCatalogStatus === "loading" && !sources.length) return "Discovering published sources…";
  if (state.sourceCatalogStatus === "error" && !sources.length) return "Source discovery unavailable";
  return sources.length ? "Select a source" : "No published sources";
}

function sourcePickerMarkup(
  state,
  sources,
  activeKey,
  sharedSourceKeys,
  disabled,
) {
  const activeSource = sources.find((item) => sourceKey(item) === activeKey) || null;
  const activeName = activeSource ? sourceDisplayName(activeSource) : sourceSelectorLabel(state, sources);
  const sharedCount = sources.filter(
    (item) => item.available !== false && sharedSourceKeys.has(sourceKey(item)),
  ).length;
  const sourceCountCopy = sharedCount ? `${sharedCount + 1} sources selected` : "";
  return `
    <div class="field compact source-picker-field">
      <span id="generation-source-label">Generation source</span>
      <div class="source-picker">
        <button id="workflow-source" class="source-picker-trigger" type="button" data-action="open-generation-source-dialog" data-source-key="${escapeHtml(activeKey || "")}" aria-haspopup="dialog" aria-controls="source-picker-dialog" aria-labelledby="generation-source-label generation-source-value" ${disabled ? "disabled" : ""}>
          <span class="source-picker-current"><strong id="generation-source-value">${escapeHtml(activeName)}</strong>${sourceCountCopy ? `<small>${escapeHtml(sourceCountCopy)}</small>` : ""}</span>
          <svg class="source-picker-launch-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5.5h12M4 10h12M4 14.5h12" /><circle cx="7" cy="5.5" r="1.5" /><circle cx="13" cy="10" r="1.5" /><circle cx="9" cy="14.5" r="1.5" /></svg>
        </button>
      </div>
    </div>`;
}

const SOURCE_SORT_KEYS = new Set([
  "display_name",
  "rating",
  "architecture",
  "introduced",
  "generation_type",
]);

export function sourcePickerDialogMarkup(
  sources,
  {
    primaryKey,
    selectedKeys = new Set(),
    sourceRatings = {},
    sortKey = "display_name",
    sortDirection = "ascending",
    generationTypeFilters = null,
  } = {},
) {
  const selected = selectedKeys instanceof Set ? selectedKeys : new Set(selectedKeys || []);
  const normalizedSortKey = SOURCE_SORT_KEYS.has(sortKey) ? sortKey : "display_name";
  const normalizedDirection = sortDirection === "descending" ? "descending" : "ascending";
  const generationTypes = sourceGenerationTypeOptions(sources);
  const activeGenerationTypes =
    generationTypeFilters === null
      ? new Set(generationTypes.map((item) => item.key))
      : generationTypeFilters instanceof Set
        ? generationTypeFilters
        : new Set(generationTypeFilters || []);
  const availableSources = sources.filter((source) => source.available !== false);
  const selectedCount = availableSources.filter((source) => selected.has(sourceKey(source))).length;
  const visibleSources = sources.filter((source) =>
    activeGenerationTypes.has(sourceGenerationTypeKey(source)),
  );
  const visibleAvailableSources = visibleSources.filter((source) => source.available !== false);
  const everyVisibleSourceSelected = visibleAvailableSources.every((source) =>
    selected.has(sourceKey(source)),
  );
  const visibleAdditionalSourceSelected = visibleAvailableSources.some(
    (source) => sourceKey(source) !== primaryKey && selected.has(sourceKey(source)),
  );
  const sortedSources = [...visibleSources].sort((first, second) => {
    const firstValue = sourceSortValue(first, normalizedSortKey, sourceRatings);
    const secondValue = sourceSortValue(second, normalizedSortKey, sourceRatings);
    const firstMissing = !firstValue || firstValue === "—";
    const secondMissing = !secondValue || secondValue === "—";
    if (firstMissing !== secondMissing) return firstMissing ? 1 : -1;
    const compared = firstValue.localeCompare(
      secondValue,
      undefined,
      { numeric: true, sensitivity: "base" },
    );
    if (compared) return normalizedDirection === "descending" ? -compared : compared;
    return sourceDisplayName(first).localeCompare(sourceDisplayName(second), undefined, {
      numeric: true,
      sensitivity: "base",
    });
  });
  const rows = sortedSources
    .map((source) => sourcePickerRowMarkup(source, primaryKey, selected, sourceRatings))
    .join("");
  const generationTypeFiltersMarkup = generationTypes
    .map(
      (item) => `<label class="source-filter-chip"><input type="checkbox" data-source-generation-type-filter="${escapeHtml(item.key)}" aria-label="Show ${escapeHtml(item.label)}" ${activeGenerationTypes.has(item.key) ? "checked" : ""} /><span>${escapeHtml(item.label)}<small>${item.count}</small></span></label>`,
    )
    .join("");
  const countCopy = `${selectedCount} of ${availableSources.length} available source${availableSources.length === 1 ? "" : "s"} selected`;
  return `<form class="dialog-frame source-picker-dialog-frame" method="dialog">
    <header class="dialog-header source-picker-dialog-header">
      <div><h2 id="source-picker-title">Generation sources</h2><p>Choose one primary source and optionally include others in the same generation.</p></div>
      <button type="button" class="icon-button source-picker-dialog-close" data-action="cancel-generation-source-dialog" aria-label="Cancel source selection" title="Cancel source selection"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
    </header>
    <div class="source-picker-dialog-content">
      <div class="source-picker-dialog-toolbar">
        <p data-source-selection-count>${escapeHtml(countCopy)}</p>
        <div class="source-picker-dialog-controls">
          <div class="source-type-filters" role="group" aria-label="Filter by generation type"><span>Show</span>${generationTypeFiltersMarkup}</div>
          <div class="source-picker-dialog-tools" aria-label="Bulk selection">
            <button type="button" class="button low" data-action="select-all-generation-sources" title="Select all visible sources" ${!visibleAvailableSources.length || everyVisibleSourceSelected ? "disabled" : ""}>Select all</button>
            <button type="button" class="button low" data-action="deselect-all-generation-sources" title="Deselect all visible additional sources" ${visibleAdditionalSourceSelected ? "" : "disabled"}>Deselect all</button>
          </div>
        </div>
      </div>
      <div class="source-picker-table-wrap">
        <table class="source-picker-table">
          <thead><tr>
            <th class="source-picker-include-column" scope="col">Include</th>
            <th class="source-picker-primary-column" scope="col">Primary</th>
            ${sourceSortHeading("display_name", "Source", normalizedSortKey, normalizedDirection)}
            ${sourceSortHeading("rating", "Rating", normalizedSortKey, normalizedDirection, "source-picker-rating-column")}
            ${sourceSortHeading("architecture", "Architecture", normalizedSortKey, normalizedDirection)}
            ${sourceSortHeading("introduced", "Introduced", normalizedSortKey, normalizedDirection)}
            ${sourceSortHeading("generation_type", "Generation type", normalizedSortKey, normalizedDirection)}
            <th scope="col"><span class="source-column-heading">Technologies</span></th>
          </tr></thead>
          <tbody>${rows || `<tr><td class="source-picker-empty" colspan="8">${sources.length ? "No generation sources match the selected generation types." : "No generation sources are available."}</td></tr>`}</tbody>
        </table>
      </div>
      <p class="source-picker-dialog-help">The primary source provides the control values. Additional sources reuse compatible prompt, resolution, and seed settings when available.</p>
    </div>
    <footer class="dialog-actions">
      <button type="button" class="button secondary" data-action="cancel-generation-source-dialog">Cancel</button>
      <button type="button" class="button primary" data-action="apply-generation-source-dialog" ${primaryKey ? "" : "disabled"}>Apply</button>
    </footer>
  </form>`;
}

function sourceSortHeading(key, label, activeKey, direction, className = "") {
  const active = key === activeKey;
  const nextDirection = active && direction === "ascending" ? "descending" : "ascending";
  const indicator = active
    ? direction === "ascending"
      ? '<svg class="source-sort-indicator" data-sort-direction-indicator="ascending" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 13V3M4 7l4-4 4 4" /></svg>'
      : '<svg class="source-sort-indicator" data-sort-direction-indicator="descending" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10m-4-4 4 4 4-4" /></svg>'
    : "";
  return `<th${className ? ` class="${escapeHtml(className)}"` : ""} scope="col" ${active ? `aria-sort="${direction}"` : ""}><button type="button" class="source-sort-button" data-action="sort-generation-sources" data-source-sort-key="${escapeHtml(key)}" data-source-sort-direction="${nextDirection}"><span>${escapeHtml(label)}</span>${indicator}</button></th>`;
}

function sourcePickerRowMarkup(source, primaryKey, selectedKeys, sourceRatings) {
  const key = sourceKey(source);
  const primary = key === primaryKey;
  const selected = selectedKeys.has(key) || primary;
  const unavailable = source.available === false;
  const metadata = sourceMetadataPresentation(source);
  const status = unavailable ? source.message || "Unavailable" : source.cached ? "Cached" : "Available";
  return `<tr class="${selected ? "is-selected " : ""}${primary ? "is-primary " : ""}${unavailable ? "is-unavailable" : ""}" data-source-row-key="${escapeHtml(key)}">
    <td class="source-picker-include-column"><label class="source-dialog-choice" title="${escapeHtml(primary ? `${source.display_name} is always included as the primary source` : `Include ${source.display_name}`)}"><input type="checkbox" data-source-draft-key="${escapeHtml(key)}" aria-label="Include ${escapeHtml(source.display_name)}" ${selected ? "checked" : ""} ${primary || unavailable ? "disabled" : ""} /><span aria-hidden="true"></span></label></td>
    <td class="source-picker-primary-column"><label class="source-dialog-primary" title="Make ${escapeHtml(source.display_name)} the primary source"><input type="radio" name="source-picker-primary" data-source-primary-key="${escapeHtml(key)}" aria-label="Make ${escapeHtml(source.display_name)} the primary source" ${primary ? "checked" : ""} ${unavailable ? "disabled" : ""} /><span aria-hidden="true"></span></label></td>
    <th class="source-picker-name-cell" scope="row"><strong>${escapeHtml(sourceDisplayName(source))}</strong><small>${escapeHtml(status)}</small></th>
    <td class="source-picker-rating-column">${sourceRatingMarkup(source, sourceRatings)}</td>
    <td>${escapeHtml(metadata.architecture)}</td>
    <td>${escapeHtml(metadata.introduced)}</td>
    <td>${escapeHtml(metadata.generationType)}</td>
    <td class="source-picker-technologies-cell">${escapeHtml(metadata.technologies)}</td>
  </tr>`;
}

function sourceRatingMarkup(source, sourceRatings) {
  const key = sourceKey(source);
  const rating = sourceRatingValue(sourceRatings, key);
  const name = source.display_name;
  const stars = Array.from({ length: 5 }, (_, index) => {
    const value = index + 1;
    const label = `Set ${name} rating to ${value} star${value === 1 ? "" : "s"}`;
    return `<button type="button" class="source-rating-star${value <= rating ? " is-filled" : ""}" data-action="rate-generation-source" data-source-rating-key="${escapeHtml(key)}" data-source-rating="${value}" aria-label="${escapeHtml(label)}" aria-pressed="${value === rating}" title="${escapeHtml(label)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3.25 2.7 5.47 6.04.88-4.37 4.26 1.03 6.02L12 17.04l-5.4 2.84 1.03-6.02L3.26 9.6l6.04-.88L12 3.25Z" /></svg></button>`;
  }).join("");
  return `<div class="source-rating-stars" role="group" aria-label="Rating for ${escapeHtml(name)}" data-source-rating-value="${rating}">${stars}</div>`;
}

function sourceRatingValue(sourceRatings, key) {
  const rating = Number(sourceRatings?.[key]);
  return Number.isInteger(rating) && rating >= 1 && rating <= 5 ? rating : 0;
}

function sourceMetadataPresentation(source) {
  const generation = source?.generation_source || {};
  const baseModel = generation.base_model || {};
  const introduction = modelIntroductionPresentation(baseModel, generation);
  const technologies = Array.isArray(generation.technologies)
    ? generation.technologies
    : Array.isArray(source?.technical_inventory?.technologies)
      ? source.technical_inventory.technologies
      : [];
  return {
    architecture: metadataLabel(baseModel.architecture_label || baseModel.architecture),
    introduced: introduction.label,
    introducedSortValue: introduction.sortValue,
    generationType: metadataLabel(generation.generation_type),
    technologies:
      technologies
        .map((technology) => metadataLabel(technology?.label || technology?.id, ""))
        .filter(Boolean)
        .join(", ") || "—",
  };
}

function modelIntroductionPresentation(baseModel, generation) {
  const canonicalMonth = validTimelineMonth(
    baseModel?.timeline?.architecture?.introduced_month,
  );
  if (canonicalMonth) {
    return {
      label: formatTimelineMonth(canonicalMonth),
      sortValue: canonicalMonth,
    };
  }

  const candidates = [
    baseModel.introduced_at,
    baseModel.introduced,
    baseModel.introduction_date,
    baseModel.release_date,
    baseModel.released_at,
    baseModel.released,
    baseModel.first_released_at,
    baseModel.launch_date,
    baseModel.release_year,
    generation.model_introduced_at,
    generation.model_release_date,
    generation.model_release_year,
  ];
  for (const candidate of candidates) {
    const year = modelIntroductionYear(candidate);
    if (year) return { label: year, sortValue: year };
  }
  return { label: "—", sortValue: "" };
}

function modelIntroductionYear(value) {
  if (typeof value === "number" && Number.isInteger(value)) {
    return value >= 1900 && value <= 2200 ? String(value) : "";
  }
  const text = String(value ?? "").trim();
  if (!text) return "";
  const yearMatch = text.match(/(?:^|\D)(19\d{2}|20\d{2}|21\d{2})(?:\D|$)/u);
  if (yearMatch) return yearMatch[1];
  const timestamp = Date.parse(text);
  return Number.isNaN(timestamp) ? "" : String(new Date(timestamp).getUTCFullYear());
}

function sourceGenerationTypeKey(source) {
  const value = String(source?.generation_source?.generation_type || "").trim().toLowerCase();
  return value || "__unknown__";
}

function sourceGenerationTypeOptions(sources) {
  const counts = new Map();
  for (const source of sources) {
    const key = sourceGenerationTypeKey(source);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts]
    .map(([key, count]) => ({
      key,
      count,
      label: key === "__unknown__" ? "Unknown" : metadataLabel(key),
    }))
    .sort((first, second) => first.label.localeCompare(second.label, undefined, { sensitivity: "base" }));
}

function metadataLabel(value, fallback = "—") {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  return text
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/gu, (character) => character.toUpperCase());
}

function sourceSortValue(source, key, sourceRatings) {
  const metadata = sourceMetadataPresentation(source);
  if (key === "rating") {
    const rating = sourceRatingValue(sourceRatings, sourceKey(source));
    return rating ? String(rating) : "";
  }
  if (key === "architecture") return metadata.architecture;
  if (key === "introduced") return metadata.introducedSortValue;
  if (key === "generation_type") return metadata.generationType;
  return sourceDisplayName(source);
}

function warningText(warning) {
  if (typeof warning === "string") return warning;
  if (warning?.message && warning?.code) return `${warning.code}: ${warning.message}`;
  return warning?.message || warning?.code || JSON.stringify(warning);
}

function sourceStateMarkup(state, source) {
  const notices = [];
  if (state.servicesStatus === "loading") {
    notices.push(
      '<div class="source-notice" role="status">Checking generation service availability…</div>',
    );
  }
  if (state.servicesStatus === "error") {
    notices.push(
      `<div class="source-notice warning" role="status">${escapeHtml(state.servicesMessage || "Service status is temporarily unavailable; generation remains paused.")}</div>`,
    );
  }
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
  if (state.sourceCatalogStatus === "error" || state.sourceDetailError) {
    notices.push(
      '<div class="source-notice"><button type="button" class="button secondary low" data-action="retry-generation-sources">Retry generation sources</button></div>',
    );
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

function collapsibleControlsMarkup(inputs, values, contract, errors, openState = {}) {
  const resolutionPair = pairedResolutionInputs(inputs, values, contract);
  const firstResolutionInput = resolutionPair
    ? inputs.find((input) => input === resolutionPair.width || input === resolutionPair.height)
    : null;
  const sections = [];
  for (const input of inputs) {
    if (resolutionPair && (input === resolutionPair.width || input === resolutionPair.height)) {
      if (input !== firstResolutionInput) continue;
      sections.push({
        key: "resolution",
        kind: "resolution",
        title: "Resolution",
        controls: [resolutionPair.width, resolutionPair.height],
        resolutionPair,
      });
      continue;
    }
    const descriptor = controlSectionDescriptor(input);
    const previous = sections.at(-1);
    if (previous && previous.key === descriptor.key && !previous.resolutionPair) {
      previous.controls.push(input);
    } else {
      sections.push({ ...descriptor, controls: [input] });
    }
  }
  return sections
    .map((section) => {
      const first = section.controls[0];
      const content = section.resolutionPair
        ? pairedResolutionMarkup(
            section.resolutionPair.width,
            section.resolutionPair.height,
            values,
            contract,
            errors,
            { hideLegend: true },
          )
        : section.controls
            .map((input) =>
              controlMarkup(input, values, contract, errors, {
                hideLabel:
                  section.kind === "prompt" ||
                  section.kind === "seed" ||
                  input.type === "image" ||
                  input.type === "resolution",
              }),
            )
            .join("");
      return controlSectionMarkup({
        key: section.key,
        title: section.title,
        required: section.controls.some((input) => input.required),
        content,
        status: controlSectionStatus(section, values),
        open: controlSectionIsOpen(openState, section.key, true),
        className: `control-section-${section.kind}`,
        actions:
          section.kind === "prompt"
            ? promptSectionActionsMarkup(first, values, contract)
            : "",
      });
    })
    .join("");
}

function controlSectionDescriptor(input) {
  const label = input.id === "prompt.text" && !input.semantic_role ? "Prompt" : input.label || input.id;
  if (input.semantic_role === "positive_prompt" || input.id === "prompt.text") {
    return { key: "prompt", kind: "prompt", title: "Prompt" };
  }
  if (input.type === "seed" || input.semantic_role === "seed") {
    return { key: "seed", kind: "seed", title: "Seed" };
  }
  if (input.type === "resolution") {
    return { key: "resolution", kind: "resolution", title: "Resolution" };
  }
  if (/upscal/i.test(`${input.id} ${label} ${input.semantic_role || ""}`)) {
    return { key: "upscaling", kind: "upscaling", title: "Upscaling" };
  }
  const group = String(input.group || "").trim();
  const title = group && group.toLowerCase() !== "basic" ? group : label;
  const identity = group && group.toLowerCase() !== "basic" ? group : input.id;
  return {
    key: `group-${sectionSlug(identity)}`,
    kind: "group",
    title,
  };
}

function sectionSlug(value) {
  return String(value || "controls")
    .trim()
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/g, "-")
    .replaceAll(/^-|-$/g, "") || "controls";
}

function controlSectionIsOpen(openState, key, defaultOpen) {
  return Object.prototype.hasOwnProperty.call(openState || {}, key)
    ? Boolean(openState[key])
    : defaultOpen;
}

function controlSectionStatus(section, values) {
  if (section.kind === "resolution") {
    const value = section.resolutionPair
      ? {
          width: values[section.resolutionPair.width.id],
          height: values[section.resolutionPair.height.id],
        }
      : values[section.controls[0]?.id] || {};
    const summary = resolutionSummary(value?.width, value?.height);
    return `${summary.width} × ${summary.height}`;
  }
  if (section.kind === "seed") {
    const control = section.controls[0];
    return seedFormValue(control, values[control.id]).mode === "random" ? "Random" : "Fixed";
  }
  return "";
}

function controlSectionMarkup({
  key,
  title,
  content,
  open,
  required = false,
  status = "",
  actions = "",
  className = "",
}) {
  const slug = sectionSlug(key);
  const triggerId = `control-section-${slug}-trigger`;
  const bodyId = `control-section-${slug}-body`;
  return `<section class="control-section ${className} ${open ? "is-expanded" : ""}" data-control-section="${escapeHtml(key)}">
    <div class="control-section-header">
      <button type="button" class="control-section-trigger" id="${triggerId}" data-action="toggle-control-section" aria-controls="${bodyId}" aria-expanded="${open}">
        <span class="control-section-title">${escapeHtml(title)}${required ? '<b class="required-mark" aria-hidden="true">*</b>' : ""}</span>
        <svg class="control-section-indicator" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M5 12h14"/><path class="control-section-indicator-vertical" d="M12 5v14"/></svg>
      </button>
      ${status ? `<span class="control-section-status" data-control-section-status="${escapeHtml(key)}">${escapeHtml(status)}</span>` : ""}
      ${actions}
    </div>
    <div class="control-section-body" id="${bodyId}" aria-labelledby="${triggerId}" aria-hidden="${!open}" ${open ? "" : "inert"}>
      <div class="control-section-clip"><div class="control-section-content">${content}</div></div>
    </div>
  </section>`;
}

function promptSectionActionsMarkup(control, values, contract) {
  if (!control) return "";
  const presentation = controlPresentation(control, values, contract?.capability_states || {});
  const disabled = !presentation.enabled;
  const id = `control-${control.id.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  const label = control.id === "prompt.text" && !control.semantic_role ? "Prompt" : control.label || control.id;
  return `<div class="prompt-field-actions control-section-actions">${speechButtonMarkup(id, label, disabled)}<button type="button" class="icon-button prompt-editor-launch" data-action="open-prompt-editor" data-prompt-control-id="${escapeHtml(control.id)}" aria-label="Open focused prompt editor" title="Open focused prompt editor" ${disabled ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5" /></svg></button></div>`;
}

function groupedControlsMarkup(inputs, values, contract, errors, options = {}) {
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
      const groupHeading =
        !options.omitGroupHeadings && group && group.trim().toLowerCase() !== "basic"
          ? `<h3 class="control-group-heading">${escapeHtml(group)}</h3>`
          : "";
      markup += `<section class="control-group" data-interface-group="${escapeHtml(group)}">${groupHeading}`;
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

export function controlMarkup(control, values, contract, errors = {}, options = {}) {
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
  const label = control.id === "prompt.text" && !control.semantic_role ? "Prompt" : control.label || control.id;
  const isPrompt = control.semantic_role === "positive_prompt" || control.id === "prompt.text";
  const errorId = error ? `${id}-error` : null;
  const describedBy = errorId || "";
  const shared = `data-control-id="${escapeHtml(control.id)}" ${disabled ? "disabled" : ""} ${required ? 'required aria-required="true"' : ""} ${error ? 'aria-invalid="true"' : ""} ${describedBy ? `aria-describedby="${describedBy}"` : ""}`;
  const common = `id="${id}" ${shared}${options.hideLabel && isPrompt ? ` aria-label="${escapeHtml(label)}"` : ""}`;
  const labelContent = `${escapeHtml(label)}${required ? '<b class="required-mark" aria-hidden="true">*</b>' : ""}`;
  let input = "";
  let field = "";
  switch (control.type) {
    case "multiline_string":
      input = `<textarea ${common} rows="${escapeHtml(control.ui?.rows || (control.id === "prompt.text" ? 10 : 3))}">${escapeHtml(value ?? "")}</textarea>`;
      break;
    case "string":
      input =
        control.semantic_role === "positive_prompt"
          ? `<textarea ${common} rows="${escapeHtml(control.ui?.rows || 10)}">${escapeHtml(value ?? "")}</textarea>`
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
      field = `<fieldset class="field semantic-fieldset" ${describedBy ? `aria-describedby="${describedBy}"` : ""}><legend${options.hideLabel ? ' class="visually-hidden"' : ""}>${labelContent}</legend>${input}</fieldset>`;
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
    case "image":
      input = imageInputMarkup(control, value, common, id, disabled);
      field = `<fieldset class="field semantic-fieldset" ${describedBy ? `aria-describedby="${describedBy}"` : ""}><legend${options.hideLabel ? ' class="visually-hidden"' : ""}>${labelContent}</legend>${input}</fieldset>`;
      break;
    case "resolution":
      input = resolutionMarkup(control, value, disabled, required, error, describedBy, id);
      field = `<fieldset class="field semantic-fieldset" ${describedBy ? `aria-describedby="${describedBy}"` : ""}><legend${options.hideLabel ? ' class="visually-hidden"' : ""}>${labelContent}</legend>${input}</fieldset>`;
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
  if (!field) {
    field = isPrompt
      ? options.hideLabel
        ? `<div class="field prompt-field prompt-field-section-content">${input}</div>`
        : `<div class="field prompt-field"><div class="prompt-field-heading"><label for="${id}">${labelContent}</label><div class="prompt-field-actions">${speechButtonMarkup(id, label, disabled)}<button type="button" class="icon-button prompt-editor-launch" data-action="open-prompt-editor" data-prompt-control-id="${escapeHtml(control.id)}" aria-label="Open focused prompt editor" title="Open focused prompt editor" ${disabled ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5" /></svg></button></div></div>${input}</div>`
      : `<label class="field" for="${id}"><span>${labelContent}</span>${input}</label>`;
  }
  const assistant = isPrompt ? promptAssistantMarkup() : "";
  return `<div class="control-block ${disabled ? "is-disabled" : ""}" data-control-block="${escapeHtml(control.id)}" data-control-group="${escapeHtml(control.group || "")}">
    ${field}
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

function imageInputMarkup(control, value, common, id, disabled) {
  const selection = value && typeof value === "object" ? value : value ? { asset_id: value } : null;
  const assetId = selection?.asset_id || "";
  const previewUrl =
    selection?.preview_url ||
    (assetId ? `/api/uploads/${encodeURIComponent(assetId)}/content` : "");
  const accept = Array.isArray(control.media?.accepted_mime_types)
    ? control.media.accepted_mime_types.join(",")
    : "image/png,image/jpeg,image/webp";
  const dimensions =
    selection?.width && selection?.height
      ? `${selection.width} × ${selection.height}`
      : "Ready to generate";
  const selected = assetId
    ? `<div class="image-input-selection"><img src="${escapeHtml(previewUrl)}" alt="Selected ${escapeHtml(control.label || control.id)}" /><div class="image-input-details"><strong>${escapeHtml(selection.name || "Image selected")}</strong><span>${escapeHtml(dimensions)}</span></div></div>`
    : `<div class="image-input-empty"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 15v4h14v-4" /></svg><strong>Drop an image here</strong><span>From your computer or the gallery</span></div>`;
  return `<div class="image-input-dropzone ${assetId ? "has-selection" : ""}" data-image-drop-control="${escapeHtml(control.id)}" data-max-bytes="${escapeHtml(control.media?.max_bytes || "")}" data-max-width="${escapeHtml(control.media?.max_width || "")}" data-max-height="${escapeHtml(control.media?.max_height || "")}" data-accepted-mime-types="${escapeHtml(accept)}" ${disabled ? 'aria-disabled="true"' : ""}>
    ${selected}
    <div class="image-input-actions ${assetId ? "" : "is-single"}">
      <label class="button secondary image-input-browse" for="${id}">Browse<input ${common} class="visually-hidden" type="file" accept="${escapeHtml(accept)}" data-upload-kind="reference-images" data-image-input="true" /></label>
      ${assetId ? `<button type="button" class="button low image-input-remove" data-clear-upload="${escapeHtml(control.id)}" ${disabled ? "disabled" : ""}>Remove</button>` : ""}
    </div>
  </div>`;
}

function resolutionMarkup(control, value, disabled, required, error, describedBy, id) {
  const base = `data-control-id="${escapeHtml(control.id)}" ${disabled ? "disabled" : ""} ${required ? 'required aria-required="true"' : ""} ${error ? 'aria-invalid="true"' : ""} ${describedBy ? `aria-describedby="${describedBy}"` : ""}`;
  const limits = resolutionConstraints(control);
  const grid = resolutionGridConstraints(control);
  const canvas = resolutionCanvasMarkup({ controlId: control.id, value, grid, disabled });
  return `<div class="resolution-editor">
    <div class="resolution-preview">${canvas}</div>
    <div class="resolution-control">
      <label for="${id}-width"><span>Width</span><input id="${id}-width" ${base} data-resolution-part="width" type="number" value="${value?.width ?? ""}" min="${limits.minimumWidth ?? ""}" max="${limits.maximumWidth ?? ""}" step="${limits.widthStep}" /></label>
      <label for="${id}-height"><span>Height</span><input id="${id}-height" ${base} data-resolution-part="height" type="number" value="${value?.height ?? ""}" min="${limits.minimumHeight ?? ""}" max="${limits.maximumHeight ?? ""}" step="${limits.heightStep}" /></label>
    </div>
    ${resolutionSummaryMarkup(value)}
  </div>`;
}

function pairedResolutionMarkup(widthControl, heightControl, values, contract, errors, options = {}) {
  const capabilityStates = contract?.capability_states || {};
  const widthPresentation = controlPresentation(widthControl, values, capabilityStates);
  const heightPresentation = controlPresentation(heightControl, values, capabilityStates);
  const disabled = !widthPresentation.enabled || !heightPresentation.enabled;
  const required = widthPresentation.required || heightPresentation.required;
  const widthError = errors[widthControl.id];
  const heightError = errors[heightControl.id];
  const widthId = `control-${widthControl.id.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  const heightId = `control-${heightControl.id.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;
  const widthErrorId = widthError ? `${widthId}-error` : null;
  const heightErrorId = heightError ? `${heightId}-error` : null;
  const describedBy = [widthErrorId, heightErrorId].filter(Boolean).join(" ");
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
    widthErrorId,
    widthId,
  );
  const heightInput = pairedResolutionInputMarkup(
    heightControl,
    "height",
    value.height,
    heightPresentation,
    heightError,
    heightErrorId,
    heightId,
  );
  return `<div class="control-block ${disabled ? "is-disabled" : ""}" data-resolution-pair-block="${escapeHtml(`${widthControl.id}:${heightControl.id}`)}" data-control-group="${escapeHtml(widthControl.group || heightControl.group || "")}">
    <fieldset class="field semantic-fieldset" ${describedBy ? `aria-describedby="${describedBy}"` : ""}>
      <legend${options.hideLegend ? ' class="visually-hidden"' : ""}>Resolution${required ? '<b class="required-mark" aria-hidden="true">*</b>' : ""}</legend>
      <div class="resolution-editor">
        <div class="resolution-preview">${canvas}</div>
        <div class="resolution-control">
          ${widthInput}
          ${heightInput}
        </div>
        ${resolutionSummaryMarkup(value)}
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
  errorId,
  id,
) {
  const describedBy = errorId || "";
  const required = presentation.required;
  const label = control.label || (axis === "width" ? "Width" : "Height");
  return `<div class="resolution-axis-field" data-control-block="${escapeHtml(control.id)}">
    <label for="${id}"><span>${escapeHtml(label)}</span><input id="${id}" data-control-id="${escapeHtml(control.id)}" data-resolution-axis="${axis}" type="number" value="${escapeHtml(value ?? "")}" min="${escapeHtml(controlConstraint(control, "minimum") ?? "")}" max="${escapeHtml(controlConstraint(control, "maximum") ?? "")}" step="${escapeHtml(controlConstraint(control, "step") ?? 1)}" ${presentation.enabled ? "" : "disabled"} ${required ? 'required aria-required="true"' : ""} ${error ? 'aria-invalid="true"' : ""} ${describedBy ? `aria-describedby="${describedBy}"` : ""} /></label>
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
    </div>`;
}

function resolutionSummaryMarkup(value) {
  const summary = resolutionSummary(value?.width, value?.height);
  return `<p class="resolution-summary" data-resolution-summary aria-live="polite">${summary.text}</p>`;
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
  return `<section class="prompt-assistant" id="prompt-assistant" aria-label="Creative Direction">
    <div class="assistant-body">
      ${speechTextareaMarkup("creative-direction", "Creative Direction", "", 3)}
      <div class="prompt-assistant-mode-options" role="radiogroup" aria-label="Creative Direction action"><label><input type="radio" name="assistant-mode" value="refine" checked /> Refine Current Prompt</label><label><input type="radio" name="assistant-mode" value="create" /> New Prompt from Creative Direction</label></div>
      <button type="button" class="button secondary" data-action="compose-prompt">Apply Creative Direction</button>
    </div>
  </section>`;
}

export function promptEditorMarkup(controlId, label, value, promptAssistant = {}) {
  const text = String(value ?? "");
  const words = text.trim() ? text.trim().split(/\s+/u).length : 0;
  const assistantMode = promptAssistant.mode === "create" ? "create" : "refine";
  const creativeDirection = String(promptAssistant.creativeDirection ?? "");
  const assistantAvailable = promptAssistant.available !== false;
  return `<form class="dialog-frame prompt-editor-frame" method="dialog">
    <header class="dialog-header prompt-editor-header">
      <div><h2 id="prompt-editor-title">Focused prompt editor</h2><p>Review and refine ${escapeHtml(label || "your prompt")} in a dedicated workspace.</p></div>
      <button type="button" class="icon-button prompt-editor-close" data-action="cancel-prompt-editor" aria-label="Cancel prompt editing" title="Cancel prompt editing"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
    </header>
    <div class="prompt-editor-content">
      <div class="prompt-editor-toolbar">
        <div class="prompt-editor-stats" aria-label="Draft statistics"><span data-prompt-word-count>${words.toLocaleString()} ${words === 1 ? "word" : "words"}</span><span aria-hidden="true">·</span><span data-prompt-character-count>${text.length.toLocaleString()} ${text.length === 1 ? "character" : "characters"}</span></div>
        <div class="prompt-editor-tools">
          ${speechButtonMarkup("prompt-editor-textarea", "Prompt editor")}
          <button type="button" class="button low" data-action="select-prompt-editor-text">Select all</button>
          <button type="button" class="button low" data-action="clear-prompt-editor-text">Clear</button>
        </div>
      </div>
      <textarea id="prompt-editor-textarea" data-prompt-editor-input data-prompt-control-id="${escapeHtml(controlId)}" aria-label="Prompt editor" spellcheck="true" autocapitalize="sentences">${escapeHtml(text)}</textarea>
      <section class="prompt-editor-assistant" aria-label="Creative Direction">
        <div class="prompt-editor-assistant-controls">
          ${speechTextareaMarkup("prompt-editor-creative-direction", "Creative Direction", creativeDirection, 3)}
          <div class="prompt-editor-assistant-action-row">
            <div class="prompt-editor-assistant-mode-options" role="radiogroup" aria-label="Creative Direction action"><label><input type="radio" name="prompt-editor-assistant-mode" value="refine" ${assistantMode === "refine" ? "checked" : ""} /> Refine Current Prompt</label><label><input type="radio" name="prompt-editor-assistant-mode" value="create" ${assistantMode === "create" ? "checked" : ""} /> New Prompt from Creative Direction</label></div>
            <button type="button" class="button secondary" data-action="compose-prompt-editor" ${assistantAvailable ? "" : "disabled"}>Apply Creative Direction</button>
          </div>
        </div>
      </section>
      <p class="prompt-editor-hint"><kbd>Ctrl</kbd>/<kbd>⌘</kbd> + <kbd>Enter</kbd> applies the draft.</p>
    </div>
    <footer class="dialog-actions">
      <button type="button" class="button secondary" data-action="cancel-prompt-editor">Cancel</button>
      <button type="button" class="button primary" data-action="apply-prompt-editor">Apply</button>
    </footer>
  </form>`;
}

function speechTextareaMarkup(id, label, value, rows) {
  return `<div class="field speech-field"><div class="speech-field-heading"><label for="${escapeHtml(id)}">${escapeHtml(label)}</label>${speechButtonMarkup(id, label)}</div><textarea id="${escapeHtml(id)}" rows="${escapeHtml(rows)}">${escapeHtml(value)}</textarea></div>`;
}

function speechButtonMarkup(targetId, label, controlDisabled = false) {
  return `<button type="button" class="icon-button speech-button" data-action="toggle-speech-recording" data-speech-target="${escapeHtml(targetId)}" data-speech-label="${escapeHtml(label)}" data-speech-control-disabled="${controlDisabled}" aria-label="Start voice input for ${escapeHtml(label)}" aria-pressed="false" title="Start voice input for ${escapeHtml(label)}" ${controlDisabled ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 10.5a6.5 6.5 0 0 0 13 0M12 17v4M8.5 21h7" /></svg></button>`;
}

export function galleryMarkup(generations, { status = "ready", message = null } = {}) {
  const cards = sortGenerationsNewestFirst(generations).map(galleryCardMarkup).join("");
  if (status === "loading") {
    return `<section class="gallery-status" role="status"><h2>Loading gallery…</h2><p>Retained history will appear here.</p></section>${cards}`;
  }
  if (status === "error") {
    return `<section class="gallery-status gallery-error" role="alert"><h2>Gallery temporarily unavailable</h2><p>${escapeHtml(message || "Retained history could not be loaded.")}</p><button type="button" class="button secondary" data-action="retry-gallery">Retry gallery</button></section>${cards}`;
  }
  if (!generations.length) {
    return `<section class="empty-gallery"><h2>No generations yet</h2><p>Choose a source, set a prompt, and queue the first image.</p></section>`;
  }
  return cards;
}

export function galleryCardMarkup(generation) {
  const artifact = generation.display_artifact;
  const hasImage = artifact?.kind === "image";
  const sourceName = generationSourceName(generation);
  const stateClass = String(generation.status || "unknown").replaceAll("_", "-");
  const media = hasImage
    ? `<img loading="lazy" src="${escapeHtml(artifact.thumbnail_url || artifact.content_url)}" alt="${escapeHtml(`${sourceName}, ${statusLabel(generation.status)}`)}" draggable="true" data-gallery-artifact-id="${escapeHtml(artifact.id)}" />`
    : statusPlaceholderMarkup(generation);
  const progress = generationProgressMarkup(generation);
  const statusOverlay = generation.status === "succeeded" || progress ? "" : `<div class="media-status">${escapeHtml(statusLabel(generation.status))}</div>`;
  const finalCount = Number(generation.final_artifact_count) || 0;
  const imageCount = generation.image_count ?? (finalCount > 0 ? finalCount : generation.artifact_count ?? 0);
  const count = imageCount > 1 ? `<div class="batch-count${progress ? " batch-count-with-progress" : ""}" aria-label="${imageCount} images">${imageCount}</div>` : "";
  const width = positiveNumber(generation.expected_width) || positiveNumber(artifact?.width);
  const height = positiveNumber(generation.expected_height) || positiveNumber(artifact?.height);
  const aspectStyle = width && height ? ` style="--gallery-media-aspect: ${width} / ${height}"` : "";
  const cancel = generation.cancel_allowed
    ? `<button type="button" class="button card-cancel-button" data-action="cancel-generation" data-generation-id="${escapeHtml(generation.id)}">Cancel</button>`
    : "";
  return `<article class="gallery-card status-${stateClass}" data-generation-id="${escapeHtml(generation.id)}">
    <div class="card-media-frame"${aspectStyle}>
      ${hasImage ? `<button type="button" class="card-media" data-action="open-photo" data-generation-id="${escapeHtml(generation.id)}" aria-label="View ${escapeHtml(sourceName)} image">${media}${statusOverlay}${count}</button>` : `<div class="card-media" aria-label="${escapeHtml(`${sourceName}, ${statusLabel(generation.status)}`)}">${media}${statusOverlay}${count}</div>`}
      <div class="generation-progress-slot" data-generation-progress-slot>${progress}</div>
      ${cancel}
    </div>
    ${cardFooterMarkup(generation)}
  </article>`;
}

export function generationProgressMarkup(generation, { now = Date.now() } = {}) {
  const progress = activeGenerationProgress(generation);
  if (!progress) return "";
  const label = String(progress.label || "Processing");
  const eta = activeGenerationEta(generation, now);
  const copy = generationProgressCopyMarkup(label, eta);
  const determinate = progress.kind === "node";
  if (!determinate) {
    return `<div class="generation-progress generation-progress-indeterminate">
      ${copy}
      <div class="progress-bar progress-bar-indeterminate" role="progressbar" aria-label="${escapeHtml(`${label} progress`)}">
        <span class="progress-bar-fill" aria-hidden="true"></span>
      </div>
    </div>`;
  }
  const value = finiteProgressNumber(progress.value);
  const maximum = finiteProgressNumber(progress.maximum);
  const fraction = Math.max(0, Math.min(1, Number(progress.fraction) || 0));
  if (value === null || maximum === null || maximum <= 0) {
    return generationProgressMarkup(
      {
        ...generation,
        progress: { ...progress, kind: "indeterminate", value: null, maximum: null, fraction: null },
      },
      { now },
    );
  }
  const displayValue = Math.max(0, Math.min(maximum, value));
  const valueLabel = formatProgressNumber(displayValue);
  const maximumLabel = formatProgressNumber(maximum);
  const valueText = `${valueLabel} of ${maximumLabel} for ${label}`;
  const accessibleValueText = eta ? `${valueText}, ${eta.accessibleText}` : valueText;
  return `<div class="generation-progress generation-progress-determinate">
    ${copy}
    <div class="progress-bar progress-bar-determinate" role="progressbar" aria-label="${escapeHtml(`${label} progress`)}" aria-valuemin="0" aria-valuemax="${escapeHtml(maximum)}" aria-valuenow="${escapeHtml(displayValue)}" aria-valuetext="${escapeHtml(accessibleValueText)}" data-progress-valuetext-base="${escapeHtml(valueText)}" style="--progress-value: ${escapeHtml((fraction * 100).toFixed(2))}%">
      <span class="progress-bar-fill" aria-hidden="true"></span>
    </div>
  </div>`;
}

function generationProgressCopyMarkup(label, eta) {
  const completion = eta && eta.completionTimestamp !== null
    ? ` data-generation-eta-completion="${escapeHtml(eta.completionTimestamp)}"`
    : "";
  const etaMarkup = eta
    ? `<span class="generation-progress-eta" data-generation-eta${completion}>${escapeHtml(eta.text)}</span>`
    : "";
  return `<div class="generation-progress-copy"><strong class="generation-progress-label">${escapeHtml(label)}</strong>${etaMarkup}</div>`;
}

function activeGenerationEta(generation, now) {
  if (!generation || !["dispatching", "running"].includes(generation.status)) return null;
  const eta = generation.progress?.eta;
  if (!eta || typeof eta !== "object" || Array.isArray(eta)) return null;
  const serverCompletionTimestamp = etaCompletionTimestamp(eta.completion_at);
  const reportedRemainingSeconds = nonnegativeFiniteNumber(eta.remaining_seconds);
  const reportedOrAbsoluteRemainingSeconds =
    reportedRemainingSeconds ??
    (serverCompletionTimestamp === null ? null : (serverCompletionTimestamp - now) / 1000);
  if (reportedOrAbsoluteRemainingSeconds === null) return null;
  const completionTimestamp = anchoredEtaCompletionTimestamp(
    generation,
    eta,
    serverCompletionTimestamp,
    reportedRemainingSeconds,
    now,
  );
  const remainingSeconds =
    completionTimestamp !== null && reportedRemainingSeconds !== null
      ? (completionTimestamp - now) / 1000
      : reportedOrAbsoluteRemainingSeconds;
  const text = formatGenerationEta(remainingSeconds);
  if (!text) return null;
  return {
    text,
    accessibleText: generationEtaAccessibleText(text),
    completionTimestamp,
  };
}

function anchoredEtaCompletionTimestamp(
  generation,
  eta,
  serverCompletionTimestamp,
  reportedRemainingSeconds,
  now,
) {
  if (serverCompletionTimestamp === null) return null;
  if (reportedRemainingSeconds === null) return serverCompletionTimestamp;

  const generationId = typeof generation.id === "string" ? generation.id.trim() : "";
  const etaUpdatedAt = typeof eta.updated_at === "string" ? eta.updated_at.trim() : "";
  const etaUpdatedTimestamp = etaCompletionTimestamp(etaUpdatedAt);
  if (!generationId || !etaUpdatedAt || etaUpdatedTimestamp === null) {
    return now + reportedRemainingSeconds * 1_000;
  }

  const cached = generationEtaAnchors.get(generationId);
  if (cached && etaUpdatedTimestamp <= cached.etaUpdatedTimestamp) {
    generationEtaAnchors.delete(generationId);
    generationEtaAnchors.set(generationId, cached);
    return cached.completionTimestamp;
  }

  const completionTimestamp = now + reportedRemainingSeconds * 1_000;
  generationEtaAnchors.delete(generationId);
  generationEtaAnchors.set(generationId, {
    etaUpdatedAt,
    etaUpdatedTimestamp,
    completionTimestamp,
  });
  while (generationEtaAnchors.size > MAX_GENERATION_ETA_ANCHORS) {
    generationEtaAnchors.delete(generationEtaAnchors.keys().next().value);
  }
  return completionTimestamp;
}

function etaCompletionTimestamp(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function nonnegativeFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function generationEtaAccessibleText(text) {
  return text === "Finishing…" ? "finishing" : text.replace(/^About/, "about");
}

function activeGenerationProgress(generation) {
  if (!generation || !["dispatching", "running", "cancel_requested"].includes(generation.status)) return null;
  if (generation.status === "cancel_requested") {
    return { kind: "indeterminate", label: "Stopping generation" };
  }
  const progress = generation.progress;
  if (progress && ["node", "indeterminate"].includes(progress.kind)) return progress;
  if (generation.status === "dispatching") return { kind: "indeterminate", label: "Starting generation" };
  return {
    kind: "indeterminate",
    label: generation.current_stage_label || "Processing",
  };
}

function finiteProgressNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatProgressNumber(value) {
  return Number.isInteger(value) ? String(value) : Number(value).toFixed(1).replace(/\.0$/, "");
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
  const duration = formatGenerationDuration(generation.generation_duration_seconds);
  const metadata = duration ? `${sourceName} · ${duration}` : sourceName;
  const artifact = generation.display_artifact;
  const deletePending = Boolean(generation.delete_pending);
  const download = artifact?.kind === "image"
    ? `<a class="download-button" href="${escapeHtml(artifact.content_url)}" download aria-label="Download current image" title="Download current image">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v12m-5-5 5 5 5-5M5 20h14" /></svg>
    </a>`
    : `<button type="button" class="download-button" disabled aria-label="Download unavailable" title="No image is available to download">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v12m-5-5 5 5 5-5M5 20h14" /></svg>
    </button>`;
  const recallTitle = generation.recall_warning
    || generation.recall_unavailable_reason
    || "Load this request into the generation panel";
  return `<footer class="card-footer"><button type="button" class="card-metadata" data-action="open-detail" data-generation-id="${escapeHtml(generation.id)}" title="Open generation details for ${escapeHtml(sourceName)}">${escapeHtml(metadata)}</button><div class="card-actions">${download}${favoriteButtonMarkup(generation)}<button type="button" class="recall-button" data-action="recall" data-generation-id="${escapeHtml(generation.id)}" ${generation.recall_available ? "" : "disabled"} aria-label="Recall settings" title="${escapeHtml(recallTitle)}">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5m4-1v5l3 2" /></svg>
  </button><button type="button" class="delete-generation-button" data-action="delete-generation" data-generation-id="${escapeHtml(generation.id)}" ${deletePending ? "disabled" : ""} aria-label="${deletePending ? "Deletion pending" : "Delete generation"}" title="${deletePending ? "Cancellation and deletion are being reconciled" : "Permanently delete this generation"}">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" /></svg>
  </button></div></footer>`;
}

export function formatGenerationDuration(value) {
  if (value === null || value === undefined) return null;
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const roundedSeconds = Math.round(seconds);
  const minutes = Math.floor(roundedSeconds / 60);
  const remainingSeconds = roundedSeconds % 60;
  return minutes ? `${minutes}m ${remainingSeconds}s` : `${remainingSeconds}s`;
}

export function formatGenerationEta(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value <= 0) return "Finishing…";
  return `About ${formatGenerationDuration(Math.ceil(value))} left`;
}

export function photoViewerMarkup(
  generation,
  navigation = {},
  requestedViewMode = "fill",
  requestedPlaybackMode = "hold",
) {
  const artifact = generation?.display_artifact;
  const sourceName = generationSourceName(generation);
  const hasImage = artifact?.kind === "image";
  const viewMode = ["actual", "fit"].includes(requestedViewMode) ? requestedViewMode : "fill";
  const playbackMode = requestedPlaybackMode === "slideshow" ? "slideshow" : "hold";
  const active = ["queued", "dispatching", "running", "cancel_requested"].includes(generation?.status);
  const status = generation?.progress?.label || generation?.current_stage_label || statusLabel(generation?.status);
  const media = hasImage
    ? `<img src="${escapeHtml(artifact.content_url)}" alt="${escapeHtml(`${sourceName}, ${statusLabel(generation.status)}`)}" draggable="false" />`
    : `<div class="photo-viewer-placeholder"><strong>No image is available.</strong></div>`;
  return `<div class="photo-viewer-frame" data-photo-generation-id="${escapeHtml(generation?.id || "")}">
    <div class="photo-viewer-media" data-photo-view-mode="${viewMode}">${media}</div>
    <div class="photo-viewer-toolbar">
      <div class="photo-viewer-toggle photo-viewer-slideshow photo-viewer-control" data-photo-toggle-state="${playbackMode}" role="group" aria-label="Playback mode">
        <button type="button" class="photo-viewer-toggle-label" data-action="set-photo-playback" data-photo-playback-mode="hold" aria-pressed="${playbackMode === "hold"}">Hold</button>
        <button type="button" class="photo-viewer-toggle-switch" data-action="toggle-photo-slideshow" role="switch" aria-label="Slideshow mode" aria-checked="${playbackMode === "slideshow"}"><span class="photo-viewer-toggle-thumb" aria-hidden="true"></span></button>
        <button type="button" class="photo-viewer-toggle-label" data-action="set-photo-playback" data-photo-playback-mode="slideshow" aria-pressed="${playbackMode === "slideshow"}">Slideshow</button>
      </div>
      <div class="photo-viewer-view-controls" role="group" aria-label="Image sizing">
        <button type="button" class="photo-viewer-one-to-one photo-viewer-control" data-action="set-photo-view" data-photo-view-mode="actual" aria-pressed="${viewMode === "actual"}" title="Show one image pixel per screen pixel">1:1</button>
        <div class="photo-viewer-toggle photo-viewer-mode photo-viewer-control" data-photo-toggle-state="${viewMode}">
          <button type="button" class="photo-viewer-toggle-label" data-action="set-photo-view" data-photo-view-mode="fit" aria-pressed="${viewMode === "fit"}">Fit</button>
          <button type="button" class="photo-viewer-toggle-switch" data-action="toggle-photo-view" role="switch" aria-label="Fill image" aria-checked="${viewMode === "fill"}"><span class="photo-viewer-toggle-thumb" aria-hidden="true"></span></button>
          <button type="button" class="photo-viewer-toggle-label" data-action="set-photo-view" data-photo-view-mode="fill" aria-pressed="${viewMode === "fill"}">Fill</button>
        </div>
      </div>
    </div>
    <button type="button" class="photo-viewer-fullscreen photo-viewer-control" data-action="toggle-photo-fullscreen" aria-pressed="false">Full screen</button>
    <button type="button" class="photo-viewer-close photo-viewer-control" data-action="close-photo" aria-label="Close image viewer">×</button>
    ${navigation.hasNewer ? '<button type="button" class="photo-viewer-nav photo-viewer-newer photo-viewer-control" data-action="navigate-photo" data-direction="newer" aria-label="View newer generation">‹</button>' : ""}
    ${navigation.hasOlder ? '<button type="button" class="photo-viewer-nav photo-viewer-older photo-viewer-control" data-action="navigate-photo" data-direction="older" aria-label="View older generation">›</button>' : ""}
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
  const recallTitle = generation.recall_warning
    || generation.recall_unavailable_reason
    || "Load this request into the generation panel";
  return `<article class="favorite-item" data-favorite-id="${escapeHtml(favorite.id)}" data-generation-id="${escapeHtml(generation.id)}">
    <div class="favorite-thumbnail">${media}</div>
    <div class="favorite-details">
      <div class="favorite-heading"><div><h3>${escapeHtml(sourceName)}</h3><p>Generated ${escapeHtml(formatLocalDate(generation.accepted_at))} · ${escapeHtml(statusLabel(generation.status))}</p></div></div>
      <p class="favorite-prompt">${escapeHtml(favorite.final_prompt || "No prompt was retained.")}</p>
      <div class="favorite-actions">
        <button type="button" class="button secondary" data-action="recall-favorite" data-generation-id="${escapeHtml(generation.id)}" ${generation.recall_available ? "" : "disabled"} title="${escapeHtml(recallTitle)}">Recall</button>
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

export function serviceBannerMarkup(services, status = "ready", message = null) {
  if (status === "loading") {
    return '<div class="service-banner" role="status"><strong>Checking generation service.</strong><span>Gallery history remains available while generation status loads.</span></div>';
  }
  if (status === "error") {
    return `<div class="service-banner" role="status"><strong>Service status unavailable.</strong><span>${escapeHtml(message || "Generation remains paused; history is still available.")}</span></div>`;
  }
  const comfy = services.find((item) => item.service === "comfyui");
  if (!comfy || comfy.available) return "";
  return `<div class="service-banner" role="status"><strong>ComfyUI unavailable.</strong><span>${escapeHtml(comfy.message || "Generation is paused; history remains available.")}</span></div>`;
}
