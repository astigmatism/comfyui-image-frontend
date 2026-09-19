import { promptGroupsMarkup } from "./gallery-groups.mjs";
import { loraStackMarkup } from "./lora-stack.mjs";
import {
  CHECKPOINT_TIER_DEFINITIONS,
  DEFAULT_OPEN_CONTROL_SECTION_KINDS,
  MAX_GENERATION_QUANTITY,
  MIN_GENERATION_QUANTITY,
  controlPresentation,
  collectionAncestors,
  collectionDepth,
  collectionTreeRows,
  escapeHtml,
  formatLocalDate,
  interfaceInputs,
  isAdvancedInput,
  normalizeCheckpointTierLayout,
  normalizeSourceModelSelections,
  resolutionConstraints,
  resolutionGridConstraints,
  resolutionPresetChoiceMarkup,
  resolutionSummary,
  seedAllowsRandom,
  seedFormValue,
  sortGenerationsNewestFirst,
  sortInterfaceInputs,
  sourceModelParameterVariants,
  sourceModelSelectors,
  statusLabel,
} from "./lib.mjs";

import { serverClockOffset } from "./server-clock.mjs";

const MAX_GENERATION_ETA_ANCHORS = 256;
const generationEtaAnchors = new Map();
const OVERDUE_ETA_TEXT = "Taking longer than expected";

export function clearGenerationEtaAnchors(generationId) {
  if (generationId) generationEtaAnchors.delete(generationId);
  else generationEtaAnchors.clear();
}

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
        <div class="topbar-left">
          <button class="icon-button panel-toggle" data-action="toggle-panel" aria-label="Open generation controls" aria-expanded="${state.panelOpen}">☰</button>
          <div class="app-title">${escapeHtml(state.session.app_title)}</div>
        </div>
        <div class="topbar-right">
          <div id="collection-bar-host" class="collection-bar-host">${renderCollectionBar(state.collections, state.currentCollectionId, {
            collectionsStatus: state.collectionsStatus,
          })}</div>
          <div id="gallery-selection-toolbar" class="gallery-selection-toolbar" role="group" aria-label="Selection actions" hidden></div>
          <div class="topbar-spacer"></div>
          <button type="button" class="button low favorites-launch-button" data-action="toggle-favorites-filter" aria-label="Favorites" title="Show only favorites" aria-pressed="${Boolean(state.favoritesFilter)}"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 21s-7.2-4.4-9.5-8.7C.7 8.8 2.2 4.5 6.1 3.4c2.2-.6 4.5.2 5.9 2 1.4-1.8 3.7-2.6 5.9-2 3.9 1.1 5.4 5.4 3.6 8.9C19.2 16.6 12 21 12 21Z" /></svg><span class="favorites-launch-label">Favorites</span></button>
          <label class="scale-control">
            <span>Gallery scale</span>
            <input id="gallery-scale" type="range" min="0" max="100" step="1" value="${state.galleryScale}" aria-label="Gallery scale" aria-valuetext="${state.galleryScale}%" />
          </label>
          <div id="generation-activity-host" class="generation-activity-host" aria-live="polite" aria-atomic="true">${generationActivityMarkup(state)}</div>
          <details class="account-menu">
            <summary aria-label="Account menu">${escapeHtml(state.session.user.username)}</summary>
            <div class="menu-popover" role="menu">
              <button role="menuitem" data-action="change-password">Change password</button>
              ${admin ? '<button role="menuitem" data-action="open-admin">Administration</button>' : ""}
              <button role="menuitem" data-action="logout">Sign out</button>
            </div>
          </details>
        </div>
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
      <dialog id="admin-dialog" class="admin-dialog"></dialog>
      <dialog id="prompt-editor-dialog" class="prompt-editor-dialog" aria-label="Focused prompt editor"></dialog>
      <dialog id="source-picker-dialog" class="source-picker-dialog" aria-label="Generation source"></dialog>
      <dialog id="collection-dialog" class="collection-dialog"></dialog>
      <dialog id="collection-delete-dialog" class="collection-delete-dialog"></dialog>
      <dialog id="move-dialog" class="move-dialog"></dialog>
      <dialog id="gallery-transfer-dialog" class="move-dialog gallery-bulk-dialog gallery-transfer-dialog" aria-label="Move or copy selection"></dialog>
      <dialog id="gallery-delete-dialog" class="collection-delete-dialog gallery-bulk-dialog gallery-delete-dialog" aria-label="Delete selection"></dialog>
      <div id="toast-region" class="toast-region" aria-live="polite" aria-atomic="true"></div>
    </div>`;
}

export function generationPanelMarkup(state, profile, contract) {
  const clientErrors = state.fieldErrors || {};
  const sources = state.sources || state.workflows || [];
  const activeKey = state.activeSourceKey || state.activeProfileId;
  const selectedTargetCount = Number(state.selectedGenerationTargetCount) || (activeKey ? 1 : 0);
  const values = state.parameters || state.controls || {};
  const declaredInputs = sortInterfaceInputs(interfaceInputs(contract));
  const modelSource = contract
    ? { ...(profile || {}), interface: contract }
    : profile;
  const modelSelectors = sourceModelSelectors(modelSource);
  const promotedModelInputIds = new Set(
    modelSelectors
      .filter((selector) =>
        declaredInputs.some(
          (input) => input.id === selector.parameter_id && input.type === "choice",
        ),
      )
      .map((selector) => selector.parameter_id),
  );
  const inputs = declaredInputs.filter((input) => !promotedModelInputIds.has(input.id));
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
          <div class="generate-row">
            <button id="generate-button" class="button primary" data-action="generate" ${disabled ? "disabled" : ""}>${state.submitting ? (selectedTargetCount > 1 ? `Queueing ${selectedTargetCount}…` : "Queueing…") : "Generate"}</button>
            <div class="generation-quantity" role="group" aria-label="Generation quantity">
              <input id="generation-quantity" class="quantity-value" type="text" inputmode="numeric" autocomplete="off" value="${state.generationQuantity ?? MIN_GENERATION_QUANTITY}" aria-label="Generation quantity" aria-live="polite" ${state.submitting ? "disabled" : ""} />
              <div class="quantity-spinner">
                <button type="button" class="quantity-arrow" data-action="increment-generation-quantity" aria-label="Increase generation quantity" ${state.submitting || (state.generationQuantity ?? MIN_GENERATION_QUANTITY) >= MAX_GENERATION_QUANTITY ? "disabled" : ""}>▲</button>
                <button type="button" class="quantity-arrow" data-action="decrement-generation-quantity" aria-label="Decrease generation quantity" ${state.submitting || (state.generationQuantity ?? MIN_GENERATION_QUANTITY) <= MIN_GENERATION_QUANTITY ? "disabled" : ""}>▼</button>
              </div>
            </div>
          </div>
          <div class="auto-generation-options">
            <label class="switch auto-generation-switch" for="auto-generate">
              <input id="auto-generate" type="checkbox" role="switch" ${(state.pendingAutoEnabled ?? state.autoGenerate) ? "checked" : ""} ${state.automationLoaded === false || state.automationBusy ? "disabled" : ""} />
              <span aria-hidden="true"></span>
              <em>Auto-generate</em>
            </label>
            <label class="auto-generation-checkbox" for="auto-generate-creative-direction">
              <input id="auto-generate-creative-direction" type="checkbox" aria-label="Use Creative Direction" ${state.autoGenerateCreativeDirection ? "checked" : ""} />
              <em>Creative Direction</em>
            </label>
          </div>
          <div id="server-controls">${serverControlsMarkup(state)}</div>
          ${comfyuiInstanceSelectorMarkup(state)}
        </div>
        ${sourcePickerMarkup(state, sources, activeKey, sourceSelectorDisabled)}
        ${presets.length ? presetMarkup(presets, state.selectedPreset) : ""}
        ${sourceStateMarkup(state, profile)}
        ${state.formError ? `<div class="form-error summary" role="alert">${escapeHtml(state.formError)}</div>` : ""}
      </div>
      <div class="panel-scroll" id="panel-scroll">
        ${collapsibleControlsMarkup(basic, values, contract, clientErrors, state.controlSectionOpen, state.recentResolutions)}
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
    state.sharedSettingsStatus === "loading" || generationRequestBlocked(state, profile, contract, clientErrors),
  );
}

export function generationRequestBlocked(state, profile, contract, clientErrors = {}) {
  const services = state.services || [];
  const comfy = services.find((item) => item.service === "comfyui");
  const usesInstanceCatalog = state.comfyuiInstancesStatus !== undefined;
  const selectedInstance = usesInstanceCatalog
    ? (state.comfyuiInstances || []).find(
        (item) => item.id === state.selectedComfyuiInstanceId,
      )
    : null;
  const serviceStateBlocksGeneration = usesInstanceCatalog
    ? state.comfyuiInstancesStatus !== "ready" ||
      !selectedInstance ||
      selectedInstance.available !== true ||
      Boolean(state.comfyuiInstanceError)
    : state.servicesStatus === undefined
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

function comfyuiInstanceSelectorMarkup(state) {
  if (state.comfyuiInstancesStatus === undefined) return "";
  const instances = Array.isArray(state.comfyuiInstances)
    ? state.comfyuiInstances
    : [];
  // With a single configured runtime the selection is automatic, so the
  // control is hidden; it only appears when there is more than one option.
  if (instances.length < 2) return "";
  const selected = instances.find(
    (item) => item.id === state.selectedComfyuiInstanceId,
  );
  const options = instances.length
    ? `${selected ? "" : '<option value="" selected>Choose a runtime</option>'}${instances
        .map((instance) => {
          const availability = instance.available === true ? "" : " · ❌";
          const copy = `${instance.label || instance.id}${availability}`;
          return `<option value="${escapeHtml(instance.id)}" ${instance.id === state.selectedComfyuiInstanceId ? "selected" : ""}>${escapeHtml(copy)}</option>`;
        })
        .join("")}`
    : `<option value="">${state.comfyuiInstancesStatus === "loading" ? "Loading runtimes…" : "No runtimes configured"}</option>`;
  const status = comfyuiInstanceStatus(state, selected);
  return `<div class="field compact comfyui-instance-field">
    <label for="comfyui-instance">Runtime</label>
    <select id="comfyui-instance" aria-describedby="comfyui-instance-status" ${instances.length ? "" : "disabled"}>${options}</select>
    <small id="comfyui-instance-status" class="comfyui-instance-status ${status.kind}" title="${escapeHtml(status.message)}" tabindex="0" ${status.role ? `role="${status.role}"` : ""}><span class="comfyui-instance-status-icon" aria-hidden="true">${status.icon}</span><span class="comfyui-instance-status-message">${escapeHtml(status.message)}</span></small>
  </div>`;
}

function comfyuiInstanceStatus(state, selected) {
  if (state.comfyuiInstancesStatus === "loading") {
    return {
      icon: "⏳",
      kind: "pending",
      role: "status",
      message: "Checking configured runtimes…",
    };
  }
  if (state.comfyuiInstanceError) {
    return {
      icon: "❌",
      kind: "error",
      role: "alert",
      message: state.comfyuiInstanceError,
    };
  }
  if (state.comfyuiInstancesStatus === "error") {
    return {
      icon: "❌",
      kind: "error",
      role: "alert",
      message:
        state.comfyuiInstancesMessage ||
        "ComfyUI runtime status is temporarily unavailable.",
    };
  }
  if (!selected) {
    return {
      icon: "❌",
      kind: "error",
      role: "alert",
      message: (state.comfyuiInstances || []).length
        ? "Choose a configured runtime before generating."
        : "No ComfyUI runtimes are configured.",
    };
  }
  if (selected.available !== true) {
    return {
      icon: "❌",
      kind: "error",
      role: "alert",
      message:
        selected.message ||
        `${selected.label || selected.id} is unavailable. Choose another runtime or try again later.`,
    };
  }
  if (state.comfyuiInstanceWarning) {
    return {
      icon: "⚠️",
      kind: "warning",
      role: "status",
      message: state.comfyuiInstanceWarning,
    };
  }
  if (state.comfyuiInstanceConfigurationMode === "legacy") {
    return {
      icon: "⚠️",
      kind: "warning",
      role: "status",
      message:
        "Only the primary ComfyUI runtime is configured for this deployment.",
    };
  }
  return {
    icon: "✅",
    kind: "available",
    role: "status",
    message: "Available",
  };
}

function sourceKey(source) {
  return source?.source_key || source?.profile_id || "";
}

function sourceDisplayName(source) {
  if (!source) return "";
  const suffix = source.available === false ? " — Unavailable" : source.cached ? " — Cached" : "";
  return `${source.display_name}${suffix}`;
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
  disabled,
) {
  const activeSource = sources.find((item) => sourceKey(item) === activeKey) || null;
  const activeName = activeSource ? sourceDisplayName(activeSource) : sourceSelectorLabel(state, sources);
  const selectedTargetCount = Number(state.selectedGenerationTargetCount) || (activeKey ? 1 : 0);
  const architecture = activeSource
    ? sourceMetadataPresentation(activeSource).architecture
    : "";
  const selectionCopy = [
    architecture && architecture !== "—" ? architecture : "",
    selectedTargetCount > 1
      ? `${selectedTargetCount} checkpoints selected`
      : "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="field compact source-picker-field">
      <span id="generation-source-label">Generation source</span>
      <div class="source-picker">
        <button id="workflow-source" class="source-picker-trigger" type="button" data-action="open-generation-source-dialog" data-source-key="${escapeHtml(activeKey || "")}" aria-haspopup="dialog" aria-controls="source-picker-dialog" aria-labelledby="generation-source-label generation-source-value" ${disabled ? "disabled" : ""}>
          <span class="source-picker-current"><span class="source-picker-name"><strong id="generation-source-value">${escapeHtml(activeName)}</strong></span>${selectionCopy ? `<small>${escapeHtml(selectionCopy)}</small>` : ""}</span>
          <svg class="source-picker-launch-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5.5h12M4 10h12M4 14.5h12" /><circle cx="7" cy="5.5" r="1.5" /><circle cx="13" cy="10" r="1.5" /><circle cx="9" cy="14.5" r="1.5" /></svg>
        </button>
      </div>
    </div>`;
}

export function sourcePickerDialogMarkup(
  sources,
  {
    sourceKey: requestedSourceKey,
    primaryKey,
    modelSelectionsBySource = {},
    checkpointTiers = {},
    searchQuery = "",
  } = {},
) {
  const availableSources = (Array.isArray(sources) ? sources : []).filter(
    (source) => source.available !== false,
  );
  const activeKey = requestedSourceKey || primaryKey || sourceKey(availableSources[0]);
  const activeSource =
    availableSources.find((source) => sourceKey(source) === activeKey) ||
    availableSources[0] ||
    null;
  const activeSourceKey = sourceKey(activeSource);
  const selector = sourceModelSelectors(activeSource)[0] || null;
  const rawSelections = selector
    ? modelSelectionsBySource?.[activeSourceKey]?.[selector.parameter_id]
    : null;
  const selectedValues = new Set(
    Array.isArray(rawSelections)
      ? rawSelections
      : selector
        ? normalizeSourceModelSelections(
            activeSource,
            modelSelectionsBySource?.[activeSourceKey] || {},
          )[selector.parameter_id] || []
        : [],
  );
  const choices = selector?.choices || [];
  const selectedCount = choices.filter((choice) => selectedValues.has(choice.value)).length;
  const totalCount = choices.length;
  const architecture = activeSource
    ? sourceMetadataPresentation(activeSource).architecture
    : "—";
  const layout = normalizeCheckpointTierLayout(
    selector,
    checkpointTiers?.[activeSourceKey]?.[selector?.parameter_id] || {},
  );
  const choiceByValue = new Map(choices.map((choice) => [choice.value, choice]));
  const query = String(searchQuery || "").trim().toLocaleLowerCase();
  const workflowOptions = availableSources
    .map((source) => {
      const optionArchitecture = sourceMetadataPresentation(source).architecture;
      const label = [source.display_name, optionArchitecture !== "—" ? optionArchitecture : ""]
        .filter(Boolean)
        .join(" — ");
      return `<option value="${escapeHtml(sourceKey(source))}" ${sourceKey(source) === activeSourceKey ? "selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
  const tierMarkup = selector
    ? CHECKPOINT_TIER_DEFINITIONS.map((tier) =>
        checkpointTierMarkup({
          tier,
          values: layout[tier.id] || [],
          choiceByValue,
          selectedValues,
          sourceKey: activeSourceKey,
          parameterId: selector.parameter_id,
          query,
        }),
      ).join("")
    : '<div class="checkpoint-picker-empty">This workflow has no checkpoint choices.</div>';
  const applyDisabled = !activeSource || (Boolean(selector) && selectedCount === 0);
  const summary = selector
    ? `${selectedCount} checkpoint${selectedCount === 1 ? "" : "s"} selected · ${selectedCount} generation${selectedCount === 1 ? "" : "s"} will be queued`
    : "This workflow will queue one generation.";
  const architectureCopy = architecture === "—" ? "Architecture not specified" : `${architecture} architecture`;
  return `<form class="dialog-frame source-picker-dialog-frame" method="dialog">
    <header class="dialog-header source-picker-dialog-header">
      <div><h2 id="source-picker-title">Generation source</h2><p>Choose one workflow, then select the checkpoints to run.</p></div>
      <button type="button" class="icon-button source-picker-dialog-close" data-action="cancel-generation-source-dialog" aria-label="Cancel source selection" title="Cancel source selection"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
    </header>
    <div class="source-picker-dialog-content">
      <div class="source-workflow-field">
        <span class="source-workflow-label">Workflow</span>
        <label class="source-workflow-select">
          <svg class="source-workflow-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="5" r="2" /><circle cx="6" cy="18" r="2" /><circle cx="18" cy="18" r="2" /><path d="M12 7v4M6 16v-2.5h12V16" /></svg>
          <span><strong>${escapeHtml(activeSource?.display_name || "No workflow available")}</strong><small>${escapeHtml(`${architectureCopy} · ${totalCount} checkpoint${totalCount === 1 ? "" : "s"}`)}</small></span>
          <svg class="source-workflow-chevron" viewBox="0 0 20 20" aria-hidden="true"><path d="m5 7.5 5 5 5-5" /></svg>
          <select data-source-workflow-choice aria-label="Workflow" ${availableSources.length ? "" : "disabled"}>${workflowOptions}</select>
        </label>
      </div>
      <div class="checkpoint-picker-heading">
        <div><span><h3>Checkpoints</h3><small data-source-selection-count>${escapeHtml(`${selectedCount} of ${totalCount} selected`)}</small></span><p>Drag checkpoints between tiers to rank them.</p></div>
        <div class="checkpoint-picker-tools">
          <label class="checkpoint-search"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" /></svg><input type="search" data-checkpoint-search placeholder="Search checkpoints" value="${escapeHtml(searchQuery)}" aria-label="Search checkpoints" /></label>
          <button type="button" class="button low" data-action="select-all-checkpoints" ${!totalCount || selectedCount === totalCount ? "disabled" : ""}>Select all</button>
          <button type="button" class="button low" data-action="clear-all-checkpoints" ${!selectedCount ? "disabled" : ""}>Clear all</button>
        </div>
      </div>
      <div class="checkpoint-tier-board" data-checkpoint-tier-board>${tierMarkup}</div>
    </div>
    <footer class="dialog-actions">
      <p class="source-picker-summary">${escapeHtml(summary)}</p>
      <button type="button" class="button secondary" data-action="cancel-generation-source-dialog">Cancel</button>
      <button type="button" class="button primary" data-action="apply-generation-source-dialog" ${applyDisabled ? "disabled" : ""}>Apply</button>
    </footer>
  </form>`;
}

function checkpointTierMarkup({
  tier,
  values,
  choiceByValue,
  selectedValues,
  sourceKey: activeSourceKey,
  parameterId,
  query,
}) {
  const choices = values.map((value) => choiceByValue.get(value)).filter(Boolean);
  const visibleChoices = choices.filter((choice) => {
    if (!query) return true;
    return `${choice.label} ${choice.value}`.toLocaleLowerCase().includes(query);
  });
  const selectedCount = choices.filter((choice) => selectedValues.has(choice.value)).length;
  const allSelected = choices.length > 0 && selectedCount === choices.length;
  const indeterminate = selectedCount > 0 && selectedCount < choices.length;
  const selectionAction = allSelected ? "Clear" : "Select";
  const cards = visibleChoices
    .map((choice) =>
      checkpointCardMarkup({
        choice,
        selected: selectedValues.has(choice.value),
        sourceKey: activeSourceKey,
        parameterId,
        tierId: tier.id,
        reorderDisabled: Boolean(query),
      }),
    )
    .join("");
  const emptyCopy = choices.length
    ? "No matching checkpoints"
    : "Drop checkpoints here";
  return `<section class="checkpoint-tier checkpoint-tier-${escapeHtml(tier.id)}" data-checkpoint-tier="${escapeHtml(tier.id)}" data-checkpoint-source-key="${escapeHtml(activeSourceKey)}" data-checkpoint-parameter-id="${escapeHtml(parameterId)}" aria-label="${escapeHtml(tier.label)} tier">
    <div class="checkpoint-tier-rail">
      <strong>${escapeHtml(tier.label)}</strong>
      <label class="checkpoint-tier-toggle" title="${escapeHtml(`${selectionAction} every checkpoint in ${tier.label}`)}">
        <input type="checkbox" data-checkpoint-tier-toggle data-checkpoint-tier-id="${escapeHtml(tier.id)}" aria-label="${escapeHtml(`${selectionAction} every checkpoint in ${tier.label}`)}" ${allSelected ? "checked" : ""} ${indeterminate ? 'data-indeterminate="true"' : ""} ${choices.length ? "" : "disabled"} />
        <span aria-hidden="true"></span><small>${selectedCount}/${choices.length}</small>
      </label>
    </div>
    <div class="checkpoint-tier-grid" role="list">${cards || `<p class="checkpoint-tier-empty">${escapeHtml(emptyCopy)}</p>`}</div>
  </section>`;
}

function checkpointCardMarkup({
  choice,
  selected,
  sourceKey: activeSourceKey,
  parameterId,
  tierId,
  reorderDisabled,
}) {
  const presentation = checkpointChoicePresentation(choice);
  const dragTitle = reorderDisabled
    ? "Clear search to reorder checkpoints"
    : `Drag ${choice.label} to reorder`;
  return `<article class="checkpoint-card${selected ? " is-selected" : ""}" data-checkpoint-card data-checkpoint-value="${escapeHtml(choice.value)}" role="listitem">
    <button type="button" class="checkpoint-drag-handle" data-checkpoint-drag-handle data-checkpoint-source-key="${escapeHtml(activeSourceKey)}" data-checkpoint-parameter-id="${escapeHtml(parameterId)}" data-checkpoint-tier-id="${escapeHtml(tierId)}" data-checkpoint-value="${escapeHtml(choice.value)}" draggable="${reorderDisabled ? "false" : "true"}" aria-label="${escapeHtml(dragTitle)}" title="${escapeHtml(dragTitle)}" ${reorderDisabled ? "disabled" : ""}><span aria-hidden="true"></span></button>
    <label class="checkpoint-choice-body"><input type="checkbox" data-source-model-choice data-source-model-source-key="${escapeHtml(activeSourceKey)}" data-source-model-parameter-id="${escapeHtml(parameterId)}" data-source-model-value="${escapeHtml(choice.value)}" aria-label="${escapeHtml(choice.label)}" ${selected ? "checked" : ""} /><span aria-hidden="true"></span><strong title="${escapeHtml(choice.label)}">${escapeHtml(presentation.label)}</strong>${presentation.badge ? `<small>${escapeHtml(presentation.badge)}</small>` : ""}</label>
  </article>`;
}

function checkpointChoicePresentation(choice) {
  const label = String(choice?.label || choice?.value || "").trim();
  const parenthetical = label.match(/\s*\(([^()]*)\)\s*$/u);
  if (!parenthetical) return { label, badge: "" };
  const precision = parenthetical[1].match(/\b(?:BF16|FP16|FP8|NVFP4|INT8|MXFP8)\b/iu)?.[0];
  if (!precision) return { label, badge: "" };
  return {
    label: label.slice(0, parenthetical.index).trim(),
    badge: precision.toUpperCase(),
  };
}

function sourceMetadataPresentation(source) {
  const baseModel = source?.generation_source?.base_model || {};
  return {
    architecture: metadataLabel(baseModel.architecture_label || baseModel.architecture),
  };
}

function metadataLabel(value, fallback = "—") {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  return text
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/gu, (character) => character.toUpperCase());
}

function warningText(warning) {
  if (typeof warning === "string") return warning;
  if (warning?.message && warning?.code) return `${warning.code}: ${warning.message}`;
  return warning?.message || warning?.code || JSON.stringify(warning);
}

function sourceStateMarkup(state, source) {
  const notices = [];
  const usesInstanceCatalog = state.comfyuiInstancesStatus !== undefined;
  if (!usesInstanceCatalog && state.servicesStatus === "loading") {
    notices.push(
      '<div class="source-notice" role="status">Checking generation service availability…</div>',
    );
  }
  if (!usesInstanceCatalog && state.servicesStatus === "error") {
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

function collapsibleControlsMarkup(inputs, values, contract, errors, openState = {}, recentResolutions = []) {
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
  const promptSectionIndex = sections.reduce(
    (index, section, offset) => (section.kind === "prompt" ? offset : index),
    -1,
  );
  if (promptSectionIndex !== -1) {
    sections.splice(promptSectionIndex + 1, 0, {
      key: "creative-direction",
      kind: "creative-direction",
      title: "Creative Direction",
      controls: [],
    });
  }
  return sections
    .map((section) => {
      const first = section.controls[0];
      const content =
        section.kind === "creative-direction"
          ? promptAssistantMarkup()
          : section.resolutionPair
            ? pairedResolutionMarkup(
                section.resolutionPair.width,
                section.resolutionPair.height,
                values,
                contract,
                errors,
                { hideLegend: true, recentResolutions },
              )
            : section.controls
                .map((input) =>
                  controlMarkup(input, values, contract, errors, {
                    hideLabel:
                      section.kind === "prompt" ||
                      section.kind === "seed" ||
                      input.type === "image" ||
                      input.type === "resolution",
                    recentResolutions,
                  }),
                )
                .join("");
      const sectionHasError = section.controls.some((input) => errors[input.id]);
      return controlSectionMarkup({
        key: section.key,
        title: section.title,
        required: section.controls.some((input) => input.required),
        content,
        status: controlSectionStatus(section, values),
        // A minimal set of sections (prompt, seed, resolution) opens by
        // default; everything else stays collapsed until the user expands
        // it. A section with a validation error opens so the block is visible.
        open:
          sectionHasError ||
          controlSectionIsOpen(
            openState,
            section.key,
            DEFAULT_OPEN_CONTROL_SECTION_KINDS.has(section.kind),
          ),
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

// Section keys whose controls carry validation errors. Sections open while an
// error is visible; callers record these keys in the open-state map so the
// section stays open after the error is resolved instead of collapsing.
export function controlSectionKeysWithErrors(contract, errors = {}) {
  if (!contract || !Object.keys(errors).length) return [];
  const keys = new Set();
  for (const input of interfaceInputs(contract)) {
    if (errors[input.id]) keys.add(controlSectionDescriptor(input).key);
  }
  return [...keys];
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
  return `<div class="prompt-field-actions control-section-actions">${speechButtonMarkup(id, label, disabled)}${pasteClipboardButtonMarkup(control.id, disabled)}<button type="button" class="icon-button prompt-editor-launch" data-action="open-prompt-editor" data-prompt-control-id="${escapeHtml(control.id)}" aria-label="Open focused prompt editor" title="Open focused prompt editor" ${disabled ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5" /></svg></button></div>`;
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
    case "lora_stack":
      input = loraStackMarkup(control, value, disabled, errorId);
      field = `<fieldset class="field semantic-fieldset"><legend>${labelContent}</legend>${input}</fieldset>`;
      break;
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
      input = resolutionMarkup(control, value, disabled, required, error, describedBy, id, {
        recentResolutions: options.recentResolutions,
      });
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
        : `<div class="field prompt-field"><div class="prompt-field-heading"><label for="${id}">${labelContent}</label><div class="prompt-field-actions">${speechButtonMarkup(id, label, disabled)}${pasteClipboardButtonMarkup(control.id, disabled)}<button type="button" class="icon-button prompt-editor-launch" data-action="open-prompt-editor" data-prompt-control-id="${escapeHtml(control.id)}" aria-label="Open focused prompt editor" title="Open focused prompt editor" ${disabled ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5" /></svg></button></div></div>${input}</div>`
      : `<label class="field" for="${id}"><span>${labelContent}</span>${input}</label>`;
  }
  return `<div class="control-block ${disabled ? "is-disabled" : ""}" data-control-block="${escapeHtml(control.id)}" data-control-group="${escapeHtml(control.group || "")}">
    ${field}
    ${error ? `<p class="field-error" id="${errorId}" role="alert">${escapeHtml(error)}</p>` : ""}
  </div>`;
}

function controlConstraint(control, name) {
  return control?.[name] ?? control?.constraints?.[name];
}

function seedMarkup(control, value, common, disabled) {
  const seed = seedFormValue(control, value);
  const random = seed.mode === "random";
  const switchDisabled = disabled || !seedAllowsRandom(control);
  return `<div class="seed-control">
    <label class="switch">
      <input type="checkbox" data-seed-mode="${escapeHtml(control.id)}" aria-label="Random seed" ${random ? "checked" : ""} ${switchDisabled ? "disabled" : ""} />
      <span aria-hidden="true"></span>
      <em>${random ? "Random" : "Fixed"}</em>
    </label>
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

export function recentResolutionsMarkup(recents, currentValue) {
  const entries = (Array.isArray(recents) ? recents : []).filter(
    (entry) =>
      Number.isFinite(Number(entry?.width)) &&
      Number.isFinite(Number(entry?.height)) &&
      Number(entry.width) > 0 &&
      Number(entry.height) > 0,
  );
  if (!entries.length) return "";
  const badges = entries
    .map((entry) => {
      const width = Number(entry.width);
      const height = Number(entry.height);
      const ratio = resolutionSummary(width, height).aspectRatio;
      const isCurrent =
        Number.isFinite(Number(currentValue?.width)) &&
        Number(currentValue.width) === width &&
        Number.isFinite(Number(currentValue?.height)) &&
        Number(currentValue.height) === height;
      return `<span class="resolution-recent-badge${isCurrent ? " is-current" : ""}" data-resolution-recent-value="${width}x${height}">
      <button type="button" class="resolution-recent-apply" data-action="apply-resolution-recent" data-resolution-recent-value="${width}x${height}" aria-label="Use recent resolution ${width} by ${height} pixels">
        ${width} × ${height}<i class="resolution-recent-ratio">${escapeHtml(ratio)}</i>
      </button>
      <button type="button" class="resolution-recent-remove" data-action="remove-resolution-recent" aria-label="Remove ${width} by ${height} from recent resolutions">✕</button>
    </span>`;
    })
    .join("\n    ");
  return `<div class="resolution-recent" data-resolution-recent>\n    ${badges}\n  </div>`;
}

function resolutionMarkup(control, value, disabled, required, error, describedBy, id, options = {}) {
  const base = `data-control-id="${escapeHtml(control.id)}" ${disabled ? "disabled" : ""} ${required ? 'required aria-required="true"' : ""} ${error ? 'aria-invalid="true"' : ""} ${describedBy ? `aria-describedby="${describedBy}"` : ""}`;
  const limits = resolutionConstraints(control);
  const grid = resolutionGridConstraints(control);
  const canvas = resolutionCanvasMarkup({ controlId: control.id, value, grid, disabled });
  return `<div class="resolution-editor">
    ${recentResolutionsMarkup(options.recentResolutions, value)}
    <div class="resolution-preview">${canvas}</div>
    <div class="resolution-control">
      <label for="${id}-width"><span>Width</span><input id="${id}-width" ${base} data-resolution-part="width" type="number" value="${value?.width ?? ""}" min="${limits.minimumWidth ?? ""}" max="${limits.maximumWidth ?? ""}" step="${limits.widthStep}" /></label>
      <label for="${id}-height"><span>Height</span><input id="${id}-height" ${base} data-resolution-part="height" type="number" value="${value?.height ?? ""}" min="${limits.minimumHeight ?? ""}" max="${limits.maximumHeight ?? ""}" step="${limits.heightStep}" /></label>
    </div>
    <div class="resolution-preset">${resolutionPresetChoiceMarkup(value)}</div>
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
        ${recentResolutionsMarkup(options.recentResolutions, value)}
        <div class="resolution-preview">${canvas}</div>
        <div class="resolution-control">
          ${widthInput}
          ${heightInput}
        </div>
        <div class="resolution-preset">${resolutionPresetChoiceMarkup(value)}</div>
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

export const PROMPT_INSTRUCTIONS_HINTS = {
  refine: "Your prompt is added after these instructions, then the Creative Direction.",
  create: "Your Creative Direction is added after these instructions.",
};

function promptInstructionsMarkup(id, assistant = {}) {
  const mode = assistant.mode === "create" ? "create" : "refine";
  const instructions = assistant.instructionOverrides?.[mode] ??
    assistant.defaultInstructions?.[mode] ?? "";
  const ready = typeof assistant.defaultInstructions?.[mode] === "string";
  const thinkingId = id.replace("-instructions", "-thinking-mode");
  return `<details class="prompt-preprocessor">
    <summary id="${id}-summary"><span id="${id}-label">Prompt pre-processor</span><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6" /></svg></summary>
    <div class="prompt-preprocessor-content">
      <p class="prompt-preprocessor-context" data-instructions-mode-label>${mode === "create" ? "Instructions for a new prompt" : "Instructions for refining your prompt"}</p>
      <textarea id="${id}" data-prompt-instructions data-instructions-mode="${mode}" rows="8" maxlength="8000" required aria-labelledby="${id}-label" aria-describedby="${id}-hint" placeholder="Loading default instructions…" ${ready ? "" : "disabled"}>${escapeHtml(instructions)}</textarea>
      <div class="prompt-preprocessor-tools"><button type="button" class="button low" data-action="reset-prompt-instructions" ${ready ? "" : "disabled"}><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M3.5 8a6.5 6.5 0 1 1 .7 6M3.5 3.5V8H8" /></svg>Reset to default</button></div>
      <p id="${id}-hint" class="prompt-preprocessor-hint" data-instructions-mode-hint>${escapeHtml(PROMPT_INSTRUCTIONS_HINTS[mode])}</p>
      <label class="prompt-preprocessor-thinking-option"><input id="${thinkingId}" type="checkbox" ${assistant.think !== false ? "checked" : ""} /> Thinking mode</label>
    </div>
  </details>`;
}

function promptAssistantMarkup() {
  return `<section class="prompt-assistant" id="prompt-assistant" aria-label="Creative Direction">
    <div class="assistant-body">
      ${promptInstructionsMarkup("prompt-assistant-instructions")}
      ${speechTextareaMarkup("creative-direction", "Creative Direction", "", 3)}
      <div class="prompt-assistant-mode-options" role="radiogroup" aria-label="Creative Direction action"><label><input type="radio" name="assistant-mode" value="refine" checked /> Refine Current Prompt</label><label><input type="radio" name="assistant-mode" value="create" /> New Prompt from Creative Direction</label></div>
      <button type="button" class="button secondary" data-action="compose-prompt">Apply Creative Direction</button>
      <p id="prompt-assistant-error" class="prompt-assistant-error" role="alert" hidden></p>
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
          <button type="button" class="button low" data-action="paste-prompt-editor-text" title="Replace with clipboard contents">Paste</button>
          <button type="button" class="button low" data-action="select-prompt-editor-text">Select all</button>
          <button type="button" class="button low" data-action="clear-prompt-editor-text">Clear</button>
        </div>
      </div>
      <textarea id="prompt-editor-textarea" data-prompt-editor-input data-prompt-control-id="${escapeHtml(controlId)}" aria-label="Prompt editor" spellcheck="true" autocapitalize="sentences">${escapeHtml(text)}</textarea>
      <section class="prompt-editor-assistant" aria-label="Creative Direction">
        <div class="prompt-editor-assistant-controls">
          ${promptInstructionsMarkup("prompt-editor-instructions", promptAssistant)}
          ${speechTextareaMarkup("prompt-editor-creative-direction", "Creative Direction", creativeDirection, 3)}
          <div class="prompt-editor-assistant-action-row">
            <div class="prompt-editor-assistant-options"><div class="prompt-editor-assistant-mode-options" role="radiogroup" aria-label="Creative Direction action"><label><input type="radio" name="prompt-editor-assistant-mode" value="refine" ${assistantMode === "refine" ? "checked" : ""} /> Refine Current Prompt</label><label><input type="radio" name="prompt-editor-assistant-mode" value="create" ${assistantMode === "create" ? "checked" : ""} /> New Prompt from Creative Direction</label></div></div>
          </div>
          <div class="prompt-editor-compose-actions"><button type="button" class="button secondary" data-action="compose-prompt-editor" ${assistantAvailable ? "" : "disabled"}>Apply Creative Direction</button></div>
          <p id="prompt-editor-assistant-error" class="prompt-assistant-error" role="alert" hidden></p>
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

function pasteClipboardButtonMarkup(controlId, controlDisabled = false) {
  return `<button type="button" class="icon-button paste-clipboard-button" data-action="paste-prompt-text" data-prompt-control-id="${escapeHtml(controlId)}" aria-label="Replace prompt with clipboard contents" title="Replace prompt with clipboard contents" ${controlDisabled ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 4h6a1 1 0 0 1 1 1v1H8V5a1 1 0 0 1 1-1Z" /><path d="M16 5h1a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h1" /><path d="M12 10.5v5M9.8 13.3 12 15.5l2.2-2.2" /></svg></button>`;
}

export function galleryMarkup(
  generations,
  {
    status = "ready",
    message = null,
    collections = [],
    currentCollectionId = null,
    favoritesFilter = false,
    promptGroups = null,
  } = {},
) {
  const tiles = collections
    .filter((collection) => (collection.parent_id ?? null) === currentCollectionId)
    .map((collection) => collectionTileMarkup(collection))
    .join("");
  const tileGrid = tiles ? `<div class="collection-grid">${tiles}</div>` : "";
  const cards = promptGroups ? promptGroupsMarkup(generations, galleryCardMarkup, promptGroups)
    : sortGenerationsNewestFirst(generations).map((generation) => galleryCardMarkup(generation)).join("");
  if (status === "loading") {
    return `${tileGrid}<section class="gallery-status" role="status"><h2>Loading gallery…</h2><p>Retained history will appear here.</p></section>${cards}`;
  }
  if (status === "error") {
    return `${tileGrid}<section class="gallery-status gallery-error" role="alert"><h2>Gallery temporarily unavailable</h2><p>${escapeHtml(message || "Retained history could not be loaded.")}</p><button type="button" class="button secondary" data-action="retry-gallery">Retry gallery</button></section>${cards}`;
  }
  if (!generations.length && !tiles) {
    if (favoritesFilter) {
      return `<section class="empty-gallery empty-favorites"><h2>No favorites in this view</h2><p>Tap the heart on any card or folder to add it here, or turn off the favorites filter.</p></section>`;
    }
    if (currentCollectionId) {
      return `<section class="empty-gallery empty-collection"><h2>This collection is empty</h2><p>Generate images here, or move cards in.</p></section>`;
    }
    return `<section class="empty-gallery"><h2>No generations yet</h2><p>Choose a source, set a prompt, and queue the first image.</p></section>`;
  }
  return `${tileGrid}${cards}`;
}

const FOLDER_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 6.5h6l2 2H21v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /><path d="M3 9h18" /></svg>`;

export function collectionCountMarkup(collection) {
  const count = Math.max(0, Number(collection?.generation_count) || 0);
  const remaining = Math.max(0, Number(collection?.remaining_count) || 0);
  const label = `${count} ${count === 1 ? "generation" : "generations"}${remaining ? `; ${remaining} remaining including nested folders` : ""}`;
  return `<span class="collection-count${remaining ? " is-generating" : ""}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"><span>${count}</span>${remaining ? `<span class="collection-remaining"><span class="activity-spinner" aria-hidden="true"></span>${remaining} remaining</span>` : ""}</span>`;
}

function generationActivityInfo(state, now) {
  const run = state.generationSubmissionProgress || state.generationActivity?.run;
  const remaining = Math.max(state.generationActivity?.remaining_count || 0, run?.remaining_count || 0);
  let mode = "progress";
  let label;
  let description;
  let percent = 0;
  if (state.autoGenerate || ["paused", "blocked"].includes(state.autoGenerateStatus)) {
    mode = ["paused", "blocked"].includes(state.autoGenerateStatus) ? "paused" : "auto";
    label = mode === "paused" ? "Auto paused"
      : state.autoGenerateStatus === "retrying" ? "Auto retrying"
        : state.submitting || state.promptAssistantComposing ? "Auto preparing"
          : remaining > 0 ? "Auto active" : "Auto waiting";
    description = state.autoGenerateStatusMessage || `${label}. ${remaining} generations remaining. Auto-generation is enabled for your account and continues on the server.`;
    if (state.autoGenerate && state.promptAssistantComposing && remaining > 0) {
      description += " Preparing the next prompt while images generate.";
    } else if (state.autoGenerate && state.autoGeneratePromptReady) {
      description += " Next prompt ready.";
    }
    if (state.autoGenerate && state.automation?.snapshot) {
      const runtimeId = state.automation.snapshot.generation.comfyui_instance_id;
      const runtime = (state.comfyuiInstances || []).find((item) => item.id === runtimeId);
      description += ` Runtime: ${runtime?.label || runtimeId}. Manual jobs take priority after dispatched work finishes.`;
    }
    if (state.autoGenerate && state.autoGeneratePinned) {
      const pinned = (Array.isArray(state.collections) ? state.collections : [])
        .find((collection) => collection.id === state.autoGeneratePinnedCollectionId);
      const target = state.autoGeneratePinnedCollectionId ? pinned?.name : "Home";
      if (target) description += ` Auto-generation is targeting ${target}.`;
    }
  } else if (run?.total_count > 0) {
    const completedAt = Date.parse(run.completed_at || "");
    if (!run.remaining_count && Number.isFinite(completedAt) && now - completedAt > 5000) return null;
    // Prefer the ETA-derived continuous fraction; fall back to per-item counts.
    const fraction = run.completed_fraction;
    percent = typeof fraction === "number" && Number.isFinite(fraction) && fraction >= 0 && fraction <= 1
      ? Math.min(run.remaining_count > 0 ? 99 : 100, Math.round(100 * fraction))
      : Math.min(run.remaining_count > 0 ? 99 : 100, Math.floor(100 * run.resolved_count / run.total_count));
    label = `${percent}%`;
    mode = run.failed_count ? "error" : "progress";
    description = `${run.resolved_count} of ${run.total_count} resolved; ${run.remaining_count} remaining. ${run.succeeded_count || 0} succeeded, ${run.failed_count || 0} failed, ${run.cancelled_count || 0} cancelled.`;
  } else if (state.generationActivityUnavailable) {
    mode = "paused";
    label = "Progress unavailable";
    description = "Generation activity is temporarily unavailable. Reconnecting…";
  } else return null;
  if (state.generationActivityUnavailable) description += " Updates temporarily unavailable; showing the last known progress.";
  return { mode, label, description, percent, determinate: ["progress", "error"].includes(mode) };
}

export function generationActivityTitle(state, now = Date.now(), base = "ImageGen V2") {
  const info = generationActivityInfo(state, now);
  if (!info) return base;
  const marker = info.determinate && info.percent >= 100 ? "🟢" : "🔵";
  return `${marker} ${info.label} · ${base}`;
}

export function generationActivityMarkup(state, now = Date.now()) {
  const info = generationActivityInfo(state, now);
  if (!info) return "";
  const { mode, label, description, percent, determinate } = info;
  const ring = determinate
    ? `<svg class="activity-ring" viewBox="0 0 24 24" aria-hidden="true"><circle class="activity-ring-track" cx="12" cy="12" r="9" /><circle class="activity-ring-value" cx="12" cy="12" r="9" pathLength="100" stroke-dasharray="${percent} 100" /></svg>`
    : `<span class="activity-spinner" aria-hidden="true"></span>`;
  return `<div class="generation-activity activity-${mode}${state.generationActivityUnavailable ? " is-stale" : ""}" tabindex="0" title="${escapeHtml(description)}" ${determinate ? `role="progressbar" aria-label="Generation completion" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}" aria-valuetext="${escapeHtml(description)}"` : `role="status" aria-label="${escapeHtml(description)}"`}>
    ${ring}<span class="activity-label">${label}</span><span class="activity-tooltip" aria-hidden="true">${escapeHtml(description)}</span>
  </div>`;
}

export function collectionTileMarkup(collection) {
  const previews = Array.isArray(collection?.previews)
    ? collection.previews.slice(0, 4)
    : [];
  const count = Math.max(0, Number(collection?.generation_count) || 0);
  const name = String(collection?.name || "");
  const previewsOn = collection?.previews_enabled !== false;
  const showGrid = Boolean(previewsOn && previews.length);
  const preview = showGrid
    ? `<div class="collection-preview-grid">${Array.from({ length: 4 }, (_, index) => {
        const item = previews[index];
        return item
          ? `<span class="collection-preview-cell"><img loading="lazy" src="${escapeHtml(item.thumbnail_url)}" alt="" /></span>`
          : `<span class="collection-preview-cell collection-preview-empty">${index === previews.length ? FOLDER_ICON : ""}</span>`;
      }).join("")}</div>`
    : `<div class="collection-folder-glyph">${FOLDER_ICON}</div>`;
  const id = escapeHtml(collection?.id || "");
  return `<div class="collection-tile${collection.is_favorite ? " is-favorited" : ""}" data-gallery-card="collection" data-collection-id="${id}">
    <button type="button" class="collection-tile-open" data-action="open-collection" data-collection-id="${id}" aria-label="Open collection ${escapeHtml(name)}, ${count} ${count === 1 ? "generation" : "generations"}">
      <span class="collection-tile-preview">${preview}</span>
      <span class="collection-caption"><span class="collection-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>${collectionCountMarkup(collection)}</span>
    </button>
    <div class="collection-tile-overlay">
      <div class="card-hover-scrim" aria-hidden="true"></div>
      <div class="collection-tile-actions card-hover-reveal" role="group" aria-label="Collection actions">
        <button type="button" class="collection-previews-button" data-action="toggle-collection-previews" data-collection-id="${id}" role="switch" aria-label="Previews for ${escapeHtml(name)}" title="Toggle previews" aria-checked="${Boolean(previewsOn)}">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="4.5" y="4.5" width="15" height="15" rx="3" /><path d="M10.5 16V8h1.7a2.3 2.3 0 0 1 0 4.6h-1.7" /></svg>
        </button>
        ${favoriteButtonMarkup(collection, "", "collection")}
        <button type="button" class="rename-collection-button" data-action="rename-collection" data-collection-id="${id}" aria-label="Rename collection ${escapeHtml(name)}" title="Rename collection">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /><path d="m15 5 4 4" /></svg>
        </button>
        ${cardSelectButtonMarkup(collection, "collection")}
        <button type="button" class="delete-collection-button" data-action="delete-collection" data-collection-id="${id}" aria-label="Delete collection ${escapeHtml(name)}" title="Delete collection">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" /></svg>
        </button>
      </div>
    </div>
  </div>`;
}

export function renderCollectionBar(
  collections,
  currentCollectionId,
  { collectionsStatus = "ready" } = {},
) {
  const ancestors = collectionAncestors(collections, currentCollectionId);
  const crumbs = [
    currentCollectionId
      ? '<a href="#/" data-action="open-collection" data-collection-id="">Home</a>'
      : '<span aria-current="location">Home</span>',
    ...ancestors.map((collection, index) =>
      index === ancestors.length - 1
        ? `<span aria-current="location">${escapeHtml(collection.name)}</span>`
        : `<a href="#/c/${encodeURIComponent(collection.id)}" data-action="open-collection" data-collection-id="${escapeHtml(collection.id)}">${escapeHtml(collection.name)}</a>`,
    ),
  ].join('<span class="collection-crumb-separator" aria-hidden="true">/</span>');
  const loading = collectionsStatus === "loading";
  const atDepthCap = currentCollectionId && collectionDepth(collections, currentCollectionId) >= 5;
  return `<nav id="collection-bar" class="collection-bar" aria-label="Collections">
    <div class="collection-crumbs">${crumbs}</div>
    <div class="collection-toolbar-actions">
      <button type="button" class="button low new-collection-launch-button" data-action="new-collection" ${loading || atDepthCap ? "disabled" : ""} title="${atDepthCap ? "Collections cannot be nested more than 5 levels deep." : "Create a collection here"}"><span class="new-collection-plus" aria-hidden="true">+</span><span class="new-collection-label">New collection</span></button>
    </div>
  </nav>`;
}

export function collectionDialogMarkup({ mode = "create", collection = null } = {}) {
  const rename = mode === "rename";
  const value = rename ? String(collection?.name || "") : "";
  return `<form id="collection-form" class="dialog-frame collection-dialog-frame" data-collection-mode="${rename ? "rename" : "create"}" ${collection?.id ? `data-collection-id="${escapeHtml(collection.id)}"` : ""}>
    <header class="dialog-header"><div><h2>${rename ? "Rename collection" : "New collection"}</h2><p>${rename ? "Choose a new name for this collection." : "Create a collection in the current location."}</p></div><button type="button" class="icon-button" data-action="cancel-collection-dialog" aria-label="Cancel">×</button></header>
    <div class="collection-dialog-content"><label class="field"><span>Name</span><input name="name" maxlength="100" value="${escapeHtml(value)}" autocomplete="off" required aria-describedby="collection-name-help collection-name-error" /></label><p id="collection-name-help" class="help-text">1–100 characters. Duplicate names are allowed.</p><p id="collection-name-error" class="field-error" role="alert"></p></div>
    <footer class="dialog-actions"><button type="button" class="button secondary" data-action="cancel-collection-dialog">Cancel</button><button type="submit" class="button primary" ${value.trim() ? "" : "disabled"}>${rename ? "Save" : "Create collection"}</button></footer>
  </form>`;
}

export function collectionDeleteDialogMarkup(collection, subtree) {
  const items = Array.isArray(subtree) ? subtree : [];
  const generations = items.reduce(
    (sum, item) => sum + Math.max(0, Number(item.generation_count) || 0),
    0,
  );
  const descendantCollections = Math.max(0, items.length - 1);
  return `<form id="collection-delete-form" class="dialog-frame collection-delete-frame" data-collection-id="${escapeHtml(collection?.id || "")}">
    <header class="dialog-header"><div><h2>Delete collection?</h2><p>This action permanently removes its contents.</p></div><button type="button" class="icon-button" data-action="cancel-collection-delete" aria-label="Cancel">×</button></header>
    <div class="collection-dialog-content"><p>Delete <strong>‘${escapeHtml(collection?.name || "")}’</strong> and everything inside?</p><p>This permanently deletes <strong>${generations}</strong> ${generations === 1 ? "generation" : "generations"} across <strong>${descendantCollections}</strong> descendant ${descendantCollections === 1 ? "collection" : "collections"}. This cannot be undone.</p></div>
    <footer class="dialog-actions"><button type="button" class="button secondary" data-action="cancel-collection-delete">Cancel</button><button type="submit" class="button destructive">Delete everything</button></footer>
  </form>`;
}

export function moveDialogMarkup(generation, collections) {
  const selectedId = generation?.collection_id ?? null;
  const rows = collectionTreeRows(collections);
  const options = [
    `<label class="move-option"><input type="radio" name="collection_id" value="" ${selectedId === null ? "checked" : ""} /><span>Home <small>Unfiled</small></span></label>`,
    ...rows.map(({ collection, depth }) =>
      `<label class="move-option" style="--collection-depth: ${depth}"><input type="radio" name="collection_id" value="${escapeHtml(collection.id)}" ${collection.id === selectedId ? "checked" : ""} /><span>${escapeHtml(collection.name)}</span></label>`,
    ),
  ].join("");
  return `<form id="move-generation-form" class="dialog-frame move-dialog-frame" data-generation-id="${escapeHtml(generation?.id || "")}">
    <header class="dialog-header"><div><h2>Move to collection</h2><p>Choose where this generation appears.</p></div><button type="button" class="icon-button" data-action="cancel-move-generation" aria-label="Cancel">×</button></header>
    <div class="move-dialog-content" role="radiogroup" aria-label="Collection destination">${options}</div>
    <footer class="dialog-actions"><button type="button" class="button secondary" data-action="cancel-move-generation">Cancel</button><button type="submit" class="button primary">Move</button></footer>
  </form>`;
}

export function galleryCardMarkup(generation) {
  const artifact = generation.display_artifact;
  const hasImage = artifact?.kind === "image";
  const sourceName = generationSourceName(generation);
  const runtimeName = generationComfyuiInstanceName(generation);
  const generationName = runtimeName ? `${sourceName}, ${runtimeName}` : sourceName;
  const stateClass = String(generation.status || "unknown").replaceAll("_", "-");
  const media = hasImage
    ? `<img loading="lazy" src="${escapeHtml(artifact.thumbnail_url || artifact.content_url)}" alt="${escapeHtml(`${generationName}, ${statusLabel(generation.status)}`)}" draggable="true" data-gallery-artifact-id="${escapeHtml(artifact.id)}" />`
    : statusPlaceholderMarkup(generation);
  const progress = generationProgressMarkup(generation);
  const statusOverlay = generation.status === "succeeded" || progress ? "" : `<div class="media-status">${escapeHtml(statusLabel(generation.status))}</div>`;
  const finalCount = Number(generation.final_artifact_count) || 0;
  const imageCount = generation.image_count ?? (finalCount > 0 ? finalCount : generation.artifact_count ?? 0);
  const count = imageCount > 1 ? `<div class="batch-count${generation.status === "succeeded" ? " card-hover-reveal" : ""}" aria-label="${imageCount} images">${imageCount}</div>` : "";
  const checkpointName = generationCheckpointLabel(generation);
  const checkpoint = checkpointName
    ? `<span class="card-checkpoint card-hover-reveal" title="${escapeHtml(checkpointName)}">${escapeHtml(checkpointName)}</span>`
    : "";
  const width = positiveNumber(generation.expected_width) || positiveNumber(artifact?.width);
  const height = positiveNumber(generation.expected_height) || positiveNumber(artifact?.height);
  const aspectStyle = width && height ? ` style="--gallery-media-aspect: ${width} / ${height}"` : "";
  const cancel = generation.cancel_allowed
    ? `<button type="button" class="button card-cancel-button" data-action="cancel-generation" data-generation-id="${escapeHtml(generation.id)}">Cancel</button>`
    : "";
  return `<article class="gallery-card${generation.is_favorite ? " is-favorited" : ""} status-${stateClass}" data-gallery-card="generation" data-generation-id="${escapeHtml(generation.id)}">
    <div class="card-media-frame"${aspectStyle}>
      ${hasImage ? `<button type="button" class="card-media" data-action="open-photo" data-generation-id="${escapeHtml(generation.id)}" aria-label="View ${escapeHtml(generationName)} image">${media}</button>` : `<div class="card-media" aria-label="${escapeHtml(`${generationName}, ${statusLabel(generation.status)}`)}">${media}</div>`}
      <div class="card-hover-scrim" aria-hidden="true"></div>
      ${checkpoint}${count}
      <div class="card-bottom-overlay">
        ${statusOverlay}
        <div class="generation-progress-slot" data-generation-progress-slot>${progress}</div>
        ${cardActionsMarkup(generation)}
      </div>
      ${cancel}
    </div>
  </article>`;
}

export function generationProgressMarkup(generation, { now = Date.now() } = {}) {
  const eta = activeGenerationEta(generation, now);
  const progress = activeGenerationProgress(generation);
  if (!progress) return "";
  const label = String(progress.label || "Processing");
  const runtimeName = generationComfyuiInstanceName(generation);
  const copy = generationProgressCopyMarkup(label, eta, runtimeName);
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
  const valueText = `${valueLabel} of ${maximumLabel} for ${label}${runtimeName ? ` on ${runtimeName}` : ""}`;
  const accessibleValueText = eta ? `${valueText}, ${eta.accessibleText}` : valueText;
  return `<div class="generation-progress generation-progress-determinate">
    ${copy}
    <div class="progress-bar progress-bar-determinate" role="progressbar" aria-label="${escapeHtml(`${label} progress`)}" aria-valuemin="0" aria-valuemax="${escapeHtml(maximum)}" aria-valuenow="${escapeHtml(displayValue)}" aria-valuetext="${escapeHtml(accessibleValueText)}" data-progress-valuetext-base="${escapeHtml(valueText)}" style="--progress-value: ${escapeHtml((fraction * 100).toFixed(2))}%">
      <span class="progress-bar-fill" aria-hidden="true"></span>
    </div>
  </div>`;
}

function generationProgressCopyMarkup(label, eta, runtimeName) {
  const completion = eta && eta.completionTimestamp !== null
    ? ` data-generation-eta-completion="${escapeHtml(eta.completionTimestamp)}"`
    : "";
  const etaMarkup = eta
    ? `<span class="generation-progress-eta" data-generation-eta${completion}>${escapeHtml(eta.text)}</span>`
    : "";
  const runtimeMarkup = runtimeName
    ? `<span class="generation-progress-runtime">${escapeHtml(runtimeName)}</span>`
    : "";
  return `<div class="generation-progress-copy"><strong class="generation-progress-label">${escapeHtml(label)}</strong><span class="generation-progress-context">${runtimeMarkup}${etaMarkup}</span></div>`;
}

export function activeGenerationEta(generation, now) {
  if (!generation || !["dispatching", "running"].includes(generation.status)) {
    if (generation?.id) clearGenerationEtaAnchors(generation.id);
    return null;
  }
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
    completionTimestamp !== null
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

  // Hold the clock mapping fixed for this attempt. Receiving a delayed snapshot
  // can change our knowledge of the deadline, but cannot change clock skew.
  const clockOffset = cached?.clockOffset ?? serverClockOffset() ?? (now - etaUpdatedTimestamp);
  const completionTimestamp = serverCompletionTimestamp + clockOffset;
  generationEtaAnchors.delete(generationId);
  generationEtaAnchors.set(generationId, {
    etaUpdatedAt,
    etaUpdatedTimestamp,
    completionTimestamp,
    clockOffset,
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
  return text.replace(/^About/, "about");
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

function generationComfyuiInstanceName(generation) {
  return (
    String(
      generation?.comfyui_instance_label ||
        generation?.comfyui_instance_id ||
        "",
    ).trim() || null
  );
}

function generationCheckpointLabel(generation) {
  const label = String(generation?.checkpoint_label || "").trim();
  return label || null;
}

function statusPlaceholderMarkup(generation) {
  let label = generation.current_stage_label || statusLabel(generation.status);
  if (generation.status.startsWith("cancelled_")) label = "Cancelled generation";
  else if (generation.status.startsWith("failed_")) label = "Generation failed";
  else if (generation.status === "interrupted") label = "Generation interrupted";
  const queueCopy = generation.status === "queued" ? "<span>Waiting for a fair queue slot</span>" : "";
  const runtimeName = generationComfyuiInstanceName(generation);
  const runtimeCopy = runtimeName
    ? `<span class="status-runtime">${escapeHtml(runtimeName)}</span>`
    : "";
  return `<div class="status-placeholder"><div class="status-symbol" aria-hidden="true"></div><strong>${escapeHtml(label)}</strong>${runtimeCopy}${queueCopy}</div>`;
}

export function cardSelectButtonMarkup(item, kind = "generation") {
  return `<button type="button" class="card-select-button" data-action="select-gallery-card" data-${kind}-id="${escapeHtml(item.id)}" role="checkbox" aria-checked="false" aria-label="Select ${kind === "collection" ? `collection ${escapeHtml(item.name)}` : "image card"}" title="Select card"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3" y="3" width="18" height="18" rx="3" /><path class="selection-checkmark" d="m7 12 3 3 7-7" /></svg></button>`;
}

export function cardActionsMarkup(generation) {
  const artifact = generation.display_artifact;
  const recallTitle = generation.recall_warning
    || generation.recall_unavailable_reason
    || "Load this request into the generation panel";
  return `<div class="card-actions card-hover-reveal" role="group" aria-label="Generation actions"><button type="button" class="card-details-button" data-action="open-detail" data-generation-id="${escapeHtml(generation.id)}" aria-label="Generation details" title="Open generation details"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></svg></button>${downloadButtonMarkup(artifact)}${favoriteButtonMarkup(generation)}<button type="button" class="recall-button" data-action="recall" data-generation-id="${escapeHtml(generation.id)}" ${generation.recall_available ? "" : "disabled"} aria-label="Recall settings" title="${escapeHtml(recallTitle)}">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5m4-1v5l3 2" /></svg>
  </button>${cardSelectButtonMarkup(generation)}${deleteGenerationButtonMarkup(generation)}</div>`;
}

export function deleteGenerationButtonMarkup(generation, extraClasses = "") {
  const deletePending = Boolean(generation.delete_pending);
  return `<button type="button" class="delete-generation-button${extraClasses ? ` ${extraClasses}` : ""}" data-action="delete-generation" data-generation-id="${escapeHtml(generation.id)}"${deletePending ? " disabled" : ""} aria-label="${deletePending ? "Deletion pending" : "Delete generation"}" title="${deletePending ? "Cancellation and deletion are being reconciled" : "Permanently delete this generation"}">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" /></svg>
  </button>`;
}

export function downloadButtonMarkup(artifact, extraClasses = "") {
  const icon = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v12m-5-5 5 5 5-5M5 20h14" /></svg>`;
  return artifact?.kind === "image"
    ? `<a class="download-button${extraClasses ? ` ${extraClasses}` : ""}" href="${escapeHtml(artifact.content_url)}" download aria-label="Download current image" title="Download current image">${icon}</a>`
    : `<button type="button" class="download-button${extraClasses ? ` ${extraClasses}` : ""}" disabled aria-label="Download unavailable" title="No image is available to download">${icon}</button>`;
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
  if (value <= 0) return OVERDUE_ETA_TEXT;
  return `About ${formatGenerationDuration(Math.ceil(value))} left`;
}

export function formatNextInCountdown(remainingSeconds) {
  if (typeof remainingSeconds !== "number" || !Number.isFinite(remainingSeconds)) return "Next in…";
  if (remainingSeconds <= 0) return OVERDUE_ETA_TEXT;
  return `Next in ${formatGenerationDuration(Math.ceil(remainingSeconds))}`;
}

export function photoViewerMarkup(
  generation,
  navigation = {},
  requestedViewMode = "fill",
  requestedPlaybackMode = "hold",
  { activity = "", generateDisabled = false, generateLabel = "Generate" } = {},
) {
  const artifact = generation?.display_artifact;
  const sourceName = generationSourceName(generation);
  const runtimeName = generationComfyuiInstanceName(generation);
  const checkpointName = generationCheckpointLabel(generation);
  const checkpointLabel = checkpointName
    ? `<span class="photo-viewer-checkpoint" title="${escapeHtml(checkpointName)}">${escapeHtml(checkpointName)}</span>`
    : "";
  const hasImage = artifact?.kind === "image";
  const viewMode = ["actual", "fit"].includes(requestedViewMode) ? requestedViewMode : "fill";
  const playbackMode = requestedPlaybackMode === "slideshow" ? "slideshow" : "hold";
  const active = ["queued", "dispatching", "running", "cancel_requested"].includes(generation?.status);
  const status = generation?.progress?.label || generation?.current_stage_label || statusLabel(generation?.status);
  const media = hasImage
    ? `<img src="${escapeHtml(artifact.content_url)}" alt="${escapeHtml(`${sourceName}${runtimeName ? `, ${runtimeName}` : ""}, ${statusLabel(generation.status)}`)}" draggable="false" />`
    : `<div class="photo-viewer-placeholder"><strong>No image is available.</strong></div>`;
  return `<div class="photo-viewer-frame" data-photo-generation-id="${escapeHtml(generation?.id || "")}">
    <div class="photo-viewer-media" data-photo-view-mode="${viewMode}">${media}</div>
    <div class="photo-viewer-generation-dock">
      <button type="button" id="photo-generate-button" class="button primary photo-viewer-generate photo-viewer-control" data-action="generate"${generateDisabled ? " disabled" : ""}>${escapeHtml(generateLabel)}</button>
      <div class="photo-viewer-activity-host" aria-live="polite" aria-atomic="true">${activity}</div>
    </div>
    <div class="photo-viewer-toolbar">
      ${checkpointLabel}
      ${downloadButtonMarkup(artifact, "photo-viewer-download photo-viewer-control")}
      ${favoriteButtonMarkup(generation, "photo-viewer-favorite photo-viewer-control")}
      ${deleteGenerationButtonMarkup(generation, "photo-viewer-delete photo-viewer-control")}
      <div class="photo-viewer-toggle photo-viewer-slideshow photo-viewer-control" data-photo-toggle-state="${playbackMode}" role="group" aria-label="Playback mode">
        <button type="button" class="photo-viewer-toggle-label" data-action="set-photo-playback" data-photo-playback-mode="hold" aria-pressed="${playbackMode === "hold"}">Hold</button>
        <button type="button" class="photo-viewer-toggle-switch app-switch-track" data-action="toggle-photo-slideshow" role="switch" aria-label="Slideshow mode" aria-checked="${playbackMode === "slideshow"}"><span class="photo-viewer-toggle-thumb app-switch-thumb" aria-hidden="true"></span></button>
        <button type="button" class="photo-viewer-toggle-label" data-action="set-photo-playback" data-photo-playback-mode="slideshow" aria-pressed="${playbackMode === "slideshow"}">Slideshow</button>
      </div>
      <div class="photo-viewer-view-controls" role="group" aria-label="Image sizing">
        <button type="button" class="photo-viewer-one-to-one photo-viewer-control" data-action="set-photo-view" data-photo-view-mode="actual" aria-pressed="${viewMode === "actual"}" title="Show one image pixel per screen pixel">1:1</button>
        <div class="photo-viewer-toggle photo-viewer-mode photo-viewer-control" data-photo-toggle-state="${viewMode}">
          <button type="button" class="photo-viewer-toggle-label" data-action="set-photo-view" data-photo-view-mode="fit" aria-pressed="${viewMode === "fit"}">Fit</button>
          <button type="button" class="photo-viewer-toggle-switch app-switch-track" data-action="toggle-photo-view" role="switch" aria-label="Fill image" aria-checked="${viewMode === "fill"}"><span class="photo-viewer-toggle-thumb app-switch-thumb" aria-hidden="true"></span></button>
          <button type="button" class="photo-viewer-toggle-label" data-action="set-photo-view" data-photo-view-mode="fill" aria-pressed="${viewMode === "fill"}">Fill</button>
        </div>
      </div>
    </div>
    <button type="button" class="photo-viewer-fullscreen photo-viewer-control" data-action="toggle-photo-fullscreen" aria-pressed="false">Full screen</button>
    <button type="button" class="photo-viewer-close photo-viewer-control" data-action="close-photo" aria-label="Close image viewer">×</button>
    ${navigation.hasNewer ? '<button type="button" class="photo-viewer-nav photo-viewer-newer photo-viewer-control" data-action="navigate-photo" data-direction="newer" aria-label="View newer generation">‹</button>' : ""}
    ${navigation.hasOlder ? '<button type="button" class="photo-viewer-nav photo-viewer-older photo-viewer-control" data-action="navigate-photo" data-direction="older" aria-label="View older generation">›</button>' : ""}
    ${active ? `<div class="photo-viewer-status" role="status">${escapeHtml(status)}${runtimeName ? ` · ${escapeHtml(runtimeName)}` : ""}</div>` : ""}
    <div class="photo-viewer-next-in" role="timer" hidden></div>
  </div>`;
}

export function favoriteButtonMarkup(generation, extraClasses = "", itemType = "generation") {
  const active = Boolean(generation.is_favorite);
  const label = active ? "Remove from Favorites" : "Add to Favorites";
  return `<button type="button" class="favorite-button${extraClasses ? ` ${extraClasses}` : ""}" data-action="${itemType === "collection" ? "toggle-collection-favorite" : "toggle-favorite"}" data-${itemType}-id="${escapeHtml(generation.id)}" aria-label="${label}" aria-pressed="${active}" title="${label}">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 21s-7.2-4.4-9.5-8.7C.7 8.8 2.2 4.5 6.1 3.4c2.2-.6 4.5.2 5.9 2 1.4-1.8 3.7-2.6 5.9-2 3.9 1.1 5.4 5.4 3.6 8.9C19.2 16.6 12 21 12 21Z" /></svg>
  </button>`;
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
  const runtimeName = generationComfyuiInstanceName(detail);
  const duration = formatGenerationDuration(detail.generation_duration_seconds);
  return `<form method="dialog" class="dialog-frame">
    <header class="dialog-header"><div><h2>${escapeHtml(sourceName)}</h2><p>${escapeHtml(statusLabel(detail.status))}${runtimeName ? ` · ${escapeHtml(runtimeName)}` : ""}${duration ? ` · Generation time: ${escapeHtml(duration)}` : ""}</p></div><button class="icon-button" value="close" aria-label="Close details">×</button></header>
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
        <dt>Execution runtime</dt><dd>${escapeHtml(runtimeName || "—")}</dd>
        <dt>Publication instance</dt><dd>${escapeHtml(source.instance_id || "—")}</dd>
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
  if (input.type === "lora_stack" && Array.isArray(value)) {
    return value.map((entry, index) => `${index + 1}. ${input.items?.find((item) => item.id === entry.id)?.label || entry.id}: ${entry.strength}${entry.strength === 0 ? " (skipped)" : ""}`).join(" → ");
  }
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

export function serviceBannerMarkup(
  services,
  status = "ready",
  message = null,
  comfyuiInstances = null,
) {
  if (comfyuiInstances) {
    const instances = Array.isArray(comfyuiInstances.instances)
      ? comfyuiInstances.instances
      : [];
    if (comfyuiInstances.status === "loading") {
      return '<div class="service-banner" role="status"><strong>Checking ComfyUI runtimes.</strong><span>Gallery history remains available while runtime status loads.</span></div>';
    }
    if (comfyuiInstances.status === "error") {
      return `<div class="service-banner" role="status"><strong>Runtime status unavailable.</strong><span>${escapeHtml(comfyuiInstances.message || "Generation remains paused; history is still available.")}</span></div>`;
    }
    const selected = instances.find(
      (item) => item.id === comfyuiInstances.selectedInstanceId,
    );
    if (!selected) {
      return '<div class="service-banner" role="status"><strong>Choose a ComfyUI runtime.</strong><span>Select an available runtime before generating; history remains available.</span></div>';
    }
    if (selected?.available === false) {
      return `<div class="service-banner" role="status"><strong>${escapeHtml(selected.label || selected.id)} unavailable.</strong><span>${escapeHtml(selected.message || "Choose another configured runtime to generate; history remains available.")}</span></div>`;
    }
    return "";
  }
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


export function serverControlsMarkup(state) {
  const auto = state.automation;
  const snapshot = auto?.snapshot;
  const settingsStatus = state.sharedSettingsStatus;
  const status = state.automationLoaded === false ? "Checking auto generation…" : state.autoGenerateStatusMessage;
  const source = (state.sources || []).find((item) => item.source_key === snapshot?.generation.source_key);
  const runtime = (state.comfyuiInstances || []).find((item) => item.id === snapshot?.generation.comfyui_instance_id);
  const target = state.pendingAutoDestination !== undefined ? state.pendingAutoDestination : snapshot?.generation.collection_id;
  return `
    <div id="auto-generate-status" class="auto-generate-status ${escapeHtml(auto?.status || "idle")}" role="${auto?.status === "blocked" ? "alert" : "status"}" ${status ? "" : "hidden"}>
      ${escapeHtml(status || "")}
      ${auto?.status === "blocked" ? '<button class="button low" data-action="retry-auto-generate">Retry Auto-generate</button>' : ""}
    </div>
    ${auto?.enabled && snapshot ? `<div class="auto-generation-server-settings">
      <div class="auto-limit-row"><label for="auto-generate-limit">Stop after</label>
      <input id="auto-generate-limit" type="number" min="1" max="1000000" step="1" placeholder="Unlimited" aria-label="Auto-generation limit" aria-describedby="auto-limit-help" value="${snapshot.max_generations ?? ""}" ${state.automationBusy ? "disabled" : ""} /></div>
      <span>${auto.remaining === null ? "Unlimited generations" : `${auto.remaining} generations remaining`}</span>
      <small id="auto-limit-help">Change to reset the count; blank for unlimited.</small>
      <label for="auto-generation-destination">Auto-generation folder</label>
      <select id="auto-generation-destination" aria-label="Auto-generation folder"><option value="" ${target ? "" : "selected"}>Home</option>${target && !(state.collections || []).some((item) => item.id === target) ? `<option value="${escapeHtml(target)}" selected disabled>Deleted folder — choose a destination</option>` : ""}${(state.collections || []).map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === target ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select>
      <button type="button" class="button low" data-action="apply-auto-generate" ${state.automationBusy || !state.autoSnapshotDirty ? "disabled" : ""}>Apply to auto generation</button>
      <small>Edits affect future batches only after Apply. Manual jobs take priority.</small>
      <details><summary>Active auto-generation settings</summary>
        <p>${escapeHtml(auto.workflow_name || source?.display_name || snapshot.generation.source_key)} · ${escapeHtml(runtime?.label || snapshot.generation.comfyui_instance_id)} · Quantity ${snapshot.quantity}</p>
        <pre>${escapeHtml(JSON.stringify({ parameters: snapshot.generation.parameters, checkpoints: snapshot.variants }, null, 2))}</pre>
        ${snapshot.assistant ? `<p>Creative Direction: ${escapeHtml(snapshot.assistant.creative_direction)}</p>` : ""}
        ${auto.latest_prompt ? `<p class="auto-latest-prompt">Latest automatic prompt: ${escapeHtml(auto.latest_prompt)}</p>` : ""}
      </details>
    </div>` : ""}
    <div class="shared-settings-status" role="status">
      ${settingsStatus === "loading" ? "Loading shared settings…" : settingsStatus === "saving" ? "Saving settings…" : settingsStatus === "saved" ? "Settings saved across devices" : ""}
      ${settingsStatus === "error" ? `${escapeHtml(state.sharedSettingsMessage || "Settings could not be saved.")} <button class="button low" data-action="settings-retry">Retry settings</button>` : ""}
      ${settingsStatus === "conflict" ? `Settings changed on another device. Your edits are preserved. <button class="button low" data-action="settings-use-saved">Use saved version</button><button class="button low" data-action="settings-keep-local">Keep my edit</button>` : ""}
    </div>`;
}
