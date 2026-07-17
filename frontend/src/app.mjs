import { api, setCsrfToken, upload } from "./api.mjs";
import {
  applyChoiceStrengthDefaults,
  clientValidate,
  choiceStrengthCompanion,
  comparisonInputs,
  comparisonParametersForRequest,
  createLatestRequestGate,
  defaultsForInterface,
  hasActiveGeneration,
  interfaceInputs,
  insertTranscription,
  latestCompletedImageGeneration,
  migrateInterfaceState,
  normalizeInputValue,
  overwriteWithRecall,
  parametersForRequest,
  photoViewerImageLayout,
  positivePromptInput,
  reconcileInterfaceValues,
  resolutionSummary,
  scaleToLayout,
  seedFormValue,
  snapResolutionValue,
  sortGenerationsNewestFirst,
} from "./lib.mjs";
import {
  detailMarkup,
  favoritesMarkup,
  galleryCardMarkup,
  galleryMarkup,
  generationPanelMarkup,
  generationRequestBlocked,
  generationSubmissionDisabled,
  loginMarkup,
  passwordChangeMarkup,
  photoViewerMarkup,
  promptEditorMarkup,
  serviceBannerMarkup,
  shellMarkup,
  sourcePickerDialogMarkup,
} from "./render.mjs";

const root = document.querySelector("#app");

const state = {
  session: null,
  sources: [],
  sourceCatalogStatus: "idle",
  sourceCatalogMessage: null,
  sourceCatalogToken: 0,
  sourceCatalogRefreshPending: false,
  servicePanelRefreshPending: false,
  sourceDetailLoading: false,
  sourceDetailError: null,
  sourceLoadToken: 0,
  activeSourceKey: null,
  activeSource: null,
  comparisonSourceKeys: new Set(),
  sourcePickerDialogOpen: false,
  sourcePickerDraft: null,
  sourcePickerSortKey: "display_name",
  sourcePickerSortDirection: "ascending",
  sourceRatings: {},
  controlSectionOpen: {},
  parameters: {},
  explicitParameterIds: new Set(),
  parameterStateBySource: {},
  pendingSourceMigration: null,
  selectedPreset: null,
  compositionId: null,
  promptAssistant: { mode: "refine", creativeDirection: "", available: false, message: null },
  speechToText: { available: false, message: null },
  generations: [],
  nextCursor: null,
  loadingMore: false,
  favorites: [],
  favoritesNextCursor: null,
  loadingMoreFavorites: false,
  galleryScale: 45,
  services: [],
  servicesStatus: "idle",
  servicesMessage: null,
  galleryStatus: "idle",
  galleryMessage: null,
  submitting: false,
  autoGenerate: false,
  imageUploadsPending: 0,
  serverFieldErrors: {},
  formError: null,
  panelOpen: false,
  eventSource: null,
  liveUpdatesPaused: false,
  pendingLiveUpdates: [],
  lastEventId: 0,
  serviceTimer: null,
  scaleTimer: null,
  observer: null,
  photoViewerGenerationId: null,
  photoViewerTimer: null,
  photoViewerMode: "fill",
  photoViewerPlaybackMode: "hold",
  photoViewerZoom: 1,
  photoViewerPanX: 0,
  photoViewerPanY: 0,
  photoViewerNeedsBaseZoom: false,
  photoViewerFullscreenOwned: false,
  photoViewerFullscreenPending: false,
  photoViewerFullscreenRequestToken: 0,
  changingPasswordFromApp: false,
};

const generationRefreshGate = createLatestRequestGate();
let activeResolutionDrag = null;
let activePhotoViewerDrag = null;
let promptEditorReturnFocus = null;
let sourcePickerReturnFocus = null;
let sourceRatingsRevision = 0;
let sourceRatingsSaveChain = Promise.resolve();
let activeSpeechSession = null;
let speechSessionSequence = 0;
let applicationStartupController = null;
let servicePollingController = null;
let startupGalleryBoundary = null;
let autoGenerateScheduled = false;
let autoGenerateCycleRunning = false;
let promptCompositionRequests = 0;

const SERVICE_POLL_INTERVAL_MS = 10_000;
const TERMINAL_GENERATION_STATUSES = new Set([
  "succeeded",
  "cancelled_with_artifacts",
  "cancelled_without_artifacts",
  "failed_with_artifacts",
  "failed_without_artifacts",
  "interrupted",
]);
const GALLERY_ARTIFACT_DRAG_TYPE = "application/x-comfyui-image-frontend-artifact";

const STARTUP_DEADLINES = {
  session: 10_000,
  preferences: 5_000,
  services: 8_000,
  gallery: 15_000,
  promptAssistant: 8_000,
  speechToText: 8_000,
  sources: 15_000,
  sourceDetail: 15_000,
};

async function initialize() {
  bindDelegatedEvents();
  try {
    const session = await startupGet("/api/auth/session", {
      operation: "Session request",
      deadlineMs: STARTUP_DEADLINES.session,
    });
    state.session = session;
    setCsrfToken(session.csrf_token);
    if (!session.authenticated) {
      renderLogin();
    } else if (session.user.must_change_password) {
      renderPasswordChange(true);
    } else {
      await enterApplication();
    }
  } catch (error) {
    renderFatal(error);
  }
}

async function startupGet(path, { operation, deadlineMs, signal } = {}) {
  const startedAt = performance.now();
  let outcome = "completed";
  try {
    return await api(path, { operation, deadlineMs, signal });
  } catch (error) {
    outcome =
      error?.code === "request_timeout"
        ? "timed_out"
        : error?.name === "AbortError"
          ? "aborted"
          : "failed";
    throw error;
  } finally {
    console.debug("[startup] request timing", {
      operation,
      outcome,
      duration_ms: Math.round(performance.now() - startedAt),
    });
  }
}

function bindDelegatedEvents() {
  root.addEventListener("submit", handleSubmit);
  root.addEventListener("click", handleClick);
  root.addEventListener("change", handleChange);
  root.addEventListener("input", handleInput);
  root.addEventListener("keydown", handleKeyDown);
  document.addEventListener("keydown", handlePhotoViewerKeyDown, true);
  root.addEventListener("keyup", handleKeyUp);
  root.addEventListener("pointerdown", handlePointerDown);
  root.addEventListener("pointermove", handlePointerMove);
  root.addEventListener("pointerup", handlePointerEnd);
  root.addEventListener("pointercancel", handlePointerEnd);
  root.addEventListener("wheel", handlePhotoViewerWheel, { passive: false });
  root.addEventListener("dragstart", handleDragStart);
  root.addEventListener("dragend", handleDragEnd);
  root.addEventListener("dragenter", handleDragEnter);
  root.addEventListener("dragover", handleDragOver);
  root.addEventListener("dragleave", handleDragLeave);
  root.addEventListener("drop", handleDrop);
  document.addEventListener("fullscreenchange", handlePhotoViewerFullscreenChange);
  window.addEventListener("resize", handlePhotoViewerResize);
}

async function handleSubmit(event) {
  if (event.target.id === "login-form") {
    event.preventDefault();
    return submitLogin(event.target);
  }
  if (event.target.id === "password-form") {
    event.preventDefault();
    return submitPassword(event.target);
  }
  if (event.target.id === "create-user-form") {
    event.preventDefault();
    return submitCreateUser(event.target);
  }
}

async function handleClick(event) {
  const clearUpload = event.target.closest("[data-clear-upload]");
  if (clearUpload) {
    const id = clearUpload.dataset.clearUpload;
    state.parameters[id] = null;
    state.explicitParameterIds.add(id);
    persistActiveParameterState();
    renderPanel();
    return;
  }
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  try {
    if (action === "generate") await generate();
    else if (action === "open-generation-source-dialog") openSourcePickerDialog(target);
    else if (action === "cancel-generation-source-dialog") closeSourcePickerDialog("cancel");
    else if (action === "apply-generation-source-dialog") await applySourcePickerDialog();
    else if (action === "select-all-generation-sources") selectAllSourcePickerDraft();
    else if (action === "deselect-all-generation-sources") deselectAllSourcePickerDraft();
    else if (action === "sort-generation-sources") sortSourcePickerDialog(target);
    else if (action === "rate-generation-source") await updateSourceRating(target);
    else if (action === "logout") await logout();
    else if (action === "change-password") {
      state.changingPasswordFromApp = true;
      renderPasswordChange(false);
    } else if (action === "cancel-password") await enterApplication();
    else if (action === "toggle-panel") {
      state.panelOpen = !state.panelOpen;
      document.querySelector(".app-shell")?.classList.toggle("panel-open", state.panelOpen);
      target.setAttribute("aria-expanded", String(state.panelOpen));
    } else if (action === "toggle-control-section") toggleControlSection(target);
    else if (action === "close-panel") closePanel();
    else if (action === "open-prompt-editor") openPromptEditor(target);
    else if (action === "toggle-speech-recording") await toggleSpeechRecording(target);
    else if (action === "cancel-prompt-editor") closePromptEditor("cancel");
    else if (action === "apply-prompt-editor") applyPromptEditor();
    else if (action === "select-prompt-editor-text") selectPromptEditorText();
    else if (action === "clear-prompt-editor-text") clearPromptEditorText();
    else if (action === "compose-prompt-editor") await composePromptEditor(target);
    else if (action === "compose-prompt") await composePrompt(target);
    else if (action === "recall") await recall(target.dataset.generationId);
    else if (action === "recall-favorite") await recallFavorite(target.dataset.generationId);
    else if (action === "toggle-favorite") await toggleFavorite(target.dataset.generationId, target);
    else if (action === "delete-favorite") await deleteFavorite(target.dataset.generationId);
    else if (action === "open-favorites") await openFavorites();
    else if (action === "close-favorites") document.querySelector("#favorites-dialog")?.close();
    else if (action === "load-more-favorites") await loadMoreFavorites();
    else if (action === "open-detail") await openDetail(target.dataset.generationId);
    else if (action === "open-photo") openPhotoViewer(target.dataset.generationId);
    else if (action === "close-photo") closePhotoViewer();
    else if (action === "toggle-photo-fullscreen") togglePhotoViewerFullscreen();
    else if (action === "toggle-photo-view") togglePhotoViewerMode();
    else if (action === "set-photo-view") setPhotoViewerMode(target.dataset.photoViewMode);
    else if (action === "toggle-photo-slideshow") togglePhotoViewerPlaybackMode();
    else if (action === "set-photo-playback") {
      setPhotoViewerPlaybackMode(target.dataset.photoPlaybackMode);
    }
    else if (action === "navigate-photo") await navigatePhotoViewer(target.dataset.direction);
    else if (action === "cancel-generation") await cancelGeneration(target.dataset.generationId, target);
    else if (action === "delete-generation") await deleteGeneration(target.dataset.generationId);
    else if (action === "load-more") await loadMore();
    else if (action === "retry-gallery") await loadStartupGallery();
    else if (action === "retry-generation-sources") await loadSources();
    else if (action === "open-admin") await openAdmin();
    else if (action === "refresh-workflows") await refreshWorkflows();
    else if (action === "reset-user-password") await resetUserPassword(target.dataset.userId);
    else if (action === "delete-user") await deleteUser(target.dataset.userId, target.dataset.username);
    else if (action === "close-admin") document.querySelector("#admin-dialog")?.close();
    else if (action === "reload") window.location.reload();
  } catch (error) {
    toast(error.message || "Action failed.", "error");
  }
}

async function handleChange(event) {
  const element = event.target;
  if (element.id === "auto-generate") {
    state.autoGenerate = element.checked;
    renderPanel();
    scheduleAutoGenerate();
    return;
  }
  if (element.matches("[data-source-draft-key]")) {
    updateSourcePickerDraftSelection(element.dataset.sourceDraftKey, element.checked);
    return;
  }
  if (element.matches("[data-source-primary-key]")) {
    updateSourcePickerDraftPrimary(element.dataset.sourcePrimaryKey);
    return;
  }
  if (element.matches("[data-source-generation-type-filter]")) {
    updateSourcePickerGenerationTypeFilter(
      element.dataset.sourceGenerationTypeFilter,
      element.checked,
    );
    return;
  }
  if (element.id === "preset-select") {
    applyPreset(element.value || null);
    return;
  }
  if (element.id === "gallery-scale") {
    updateGalleryScale(element.value, true);
    return;
  }
  if (element.matches("[data-seed-mode]")) {
    const id = element.dataset.seedMode;
    const input = interfaceInputs(state.activeSource?.interface).find((item) => item.id === id);
    if (!input) return;
    const current = seedFormValue(input, state.parameters[id]);
    state.parameters[id] = { mode: element.value === "random" ? "random" : "fixed", value: current.value };
    state.explicitParameterIds.add(id);
    state.serverFieldErrors[id] = null;
    persistActiveParameterState();
    renderPanel();
    return;
  }
  if (element.matches("input[type=file][data-control-id]")) {
    await handleUpload(element);
    return;
  }
  if (element.matches("[data-control-id]")) {
    const control = updateControlFromElement(element);
    syncNumberControlPair(element);
    syncChoiceStrengthControl(control);
    persistActiveParameterState();
    syncParameterValidation(element.dataset.controlId);
    const companion = choiceStrengthCompanion(sourceInterface(state.activeSource), control);
    if (control?.type === "choice" && companion) syncParameterValidation(companion.id);
  }
}

async function flushDeferredSourcePickerUpdates({ panelAlreadyRendered = false } = {}) {
  const catalogRefreshPending = state.sourceCatalogRefreshPending;
  const panelRefreshPending = state.servicePanelRefreshPending;
  state.sourceCatalogRefreshPending = false;
  state.servicePanelRefreshPending = false;
  if (catalogRefreshPending) {
    await loadSources();
  } else if (panelRefreshPending && !panelAlreadyRendered) {
    renderPanel();
  }
}

function openSourcePickerDialog(button) {
  const dialog = document.querySelector("#source-picker-dialog");
  if (!dialog || dialog.open || button.disabled || !state.activeSourceKey) return;
  sourcePickerReturnFocus = button;
  state.sourcePickerDraft = {
    primaryKey: state.activeSourceKey,
    selectedKeys: new Set([state.activeSourceKey, ...selectedComparisonSourceKeys()]),
    generationTypeFilters: new Set(
      state.sources.map((source) => sourceGenerationTypeKey(source)),
    ),
  };
  state.sourcePickerDialogOpen = true;
  renderSourcePickerDialog();
  dialog.showModal();
  queueMicrotask(() => {
    dialog.querySelector("[data-source-sort-key]")?.focus({ preventScroll: true });
  });
}

function renderSourcePickerDialog() {
  const dialog = document.querySelector("#source-picker-dialog");
  const draft = state.sourcePickerDraft;
  if (!dialog || !draft) return;
  const scrollLeft = dialog.querySelector(".source-picker-table-wrap")?.scrollLeft || 0;
  const scrollTop = dialog.querySelector(".source-picker-table-wrap")?.scrollTop || 0;
  dialog.innerHTML = sourcePickerDialogMarkup(state.sources, {
    primaryKey: draft.primaryKey,
    selectedKeys: draft.selectedKeys,
    sourceRatings: state.sourceRatings,
    sortKey: state.sourcePickerSortKey,
    sortDirection: state.sourcePickerSortDirection,
    generationTypeFilters: draft.generationTypeFilters,
  });
  const scroller = dialog.querySelector(".source-picker-table-wrap");
  if (scroller) {
    scroller.scrollLeft = scrollLeft;
    scroller.scrollTop = scrollTop;
  }
}

function sourceGenerationTypeKey(source) {
  const value = String(source?.generation_source?.generation_type || "").trim().toLowerCase();
  return value || "__unknown__";
}

function updateSourcePickerGenerationTypeFilter(key, checked) {
  const draft = state.sourcePickerDraft;
  if (!draft || !key) return;
  if (checked) draft.generationTypeFilters.add(key);
  else draft.generationTypeFilters.delete(key);
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector(
        `#source-picker-dialog [data-source-generation-type-filter="${CSS.escape(key)}"]`,
      )
      ?.focus({ preventScroll: true });
  });
}

function updateSourcePickerDraftSelection(key, checked) {
  const draft = state.sourcePickerDraft;
  const source = state.sources.find((item) => sourceKey(item) === key);
  if (!draft || !key || key === draft.primaryKey || source?.available === false) return;
  if (checked) draft.selectedKeys.add(key);
  else draft.selectedKeys.delete(key);
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector(`#source-picker-dialog [data-source-draft-key="${CSS.escape(key)}"]`)
      ?.focus({ preventScroll: true });
  });
}

function updateSourcePickerDraftPrimary(key) {
  const draft = state.sourcePickerDraft;
  const source = state.sources.find((item) => sourceKey(item) === key);
  if (!draft || !key || source?.available === false) return;
  const previousPrimaryKey = draft.primaryKey;
  draft.primaryKey = key;
  draft.selectedKeys.add(key);
  if (previousPrimaryKey && previousPrimaryKey !== key) {
    draft.selectedKeys.delete(previousPrimaryKey);
  }
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector(`#source-picker-dialog [data-source-primary-key="${CSS.escape(key)}"]`)
      ?.focus({ preventScroll: true });
  });
}

function selectAllSourcePickerDraft() {
  const draft = state.sourcePickerDraft;
  if (!draft) return;
  for (const source of state.sources) {
    if (
      source.available !== false &&
      draft.generationTypeFilters.has(sourceGenerationTypeKey(source))
    ) {
      draft.selectedKeys.add(sourceKey(source));
    }
  }
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector('#source-picker-dialog [data-action="deselect-all-generation-sources"]')
      ?.focus({ preventScroll: true });
  });
}

function deselectAllSourcePickerDraft() {
  const draft = state.sourcePickerDraft;
  if (!draft) return;
  for (const source of state.sources) {
    const key = sourceKey(source);
    if (
      key !== draft.primaryKey &&
      draft.generationTypeFilters.has(sourceGenerationTypeKey(source))
    ) {
      draft.selectedKeys.delete(key);
    }
  }
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector('#source-picker-dialog [data-action="select-all-generation-sources"]')
      ?.focus({ preventScroll: true });
  });
}

function sortSourcePickerDialog(button) {
  state.sourcePickerSortKey = button.dataset.sourceSortKey || "display_name";
  state.sourcePickerSortDirection =
    button.dataset.sourceSortDirection === "descending" ? "descending" : "ascending";
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector(
        `#source-picker-dialog [data-source-sort-key="${CSS.escape(state.sourcePickerSortKey)}"]`,
      )
      ?.focus({ preventScroll: true });
  });
}

async function updateSourceRating(button) {
  const key = button.dataset.sourceRatingKey;
  const rating = Number(button.dataset.sourceRating);
  if (
    !key ||
    !state.sources.some((source) => sourceKey(source) === key) ||
    !Number.isInteger(rating) ||
    rating < 1 ||
    rating > 5
  ) {
    return;
  }
  state.sourceRatings = { ...state.sourceRatings, [key]: rating };
  sourceRatingsRevision += 1;
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector(
        `#source-picker-dialog [data-source-rating-key="${CSS.escape(key)}"][data-source-rating="${rating}"]`,
      )
      ?.focus({ preventScroll: true });
  });

  const ratings = { ...state.sourceRatings };
  const save = sourceRatingsSaveChain.then(() =>
    api("/api/preferences", {
      method: "PUT",
      body: JSON.stringify({ source_ratings: ratings }),
    }),
  );
  sourceRatingsSaveChain = save.catch(() => {});
  try {
    await save;
  } catch {
    toast("Source rating could not be saved.", "error");
  }
}

function closeSourcePickerDialog(returnValue, { flushDeferredUpdates = true } = {}) {
  const dialog = document.querySelector("#source-picker-dialog");
  const wasOpen = state.sourcePickerDialogOpen;
  state.sourcePickerDialogOpen = false;
  state.sourcePickerDraft = null;
  if (dialog?.open) dialog.close(returnValue);
  if (wasOpen && flushDeferredUpdates) void flushDeferredSourcePickerUpdates();
}

async function applySourcePickerDialog() {
  const draft = state.sourcePickerDraft;
  const primary = state.sources.find(
    (source) => sourceKey(source) === draft?.primaryKey && source.available !== false,
  );
  if (!draft || !primary) return;
  const availableKeys = new Set(
    state.sources
      .filter((source) => source.available !== false)
      .map((source) => sourceKey(source)),
  );
  const comparisonKeys = new Set(
    [...draft.selectedKeys].filter(
      (key) => key !== draft.primaryKey && availableKeys.has(key),
    ),
  );
  const primaryChanged = draft.primaryKey !== state.activeSourceKey;
  closeSourcePickerDialog("apply", { flushDeferredUpdates: false });
  state.comparisonSourceKeys = comparisonKeys;
  state.serverFieldErrors = {};
  state.formError = null;
  if (primaryChanged) await selectSource(draft.primaryKey, { summary: primary });
  else renderPanel();
  await flushDeferredSourcePickerUpdates({ panelAlreadyRendered: true });
}

function handleSourcePickerDialogClose(event) {
  const wasOpen = state.sourcePickerDialogOpen;
  state.sourcePickerDialogOpen = false;
  state.sourcePickerDraft = null;
  const previous = sourcePickerReturnFocus;
  sourcePickerReturnFocus = null;
  queueMicrotask(() => {
    const fallback = document.querySelector("#workflow-source");
    const target = previous?.isConnected ? previous : fallback;
    if (target && !target.disabled) target.focus({ preventScroll: true });
  });
  if (wasOpen) void flushDeferredSourcePickerUpdates();
}

function toggleControlSection(trigger) {
  const section = trigger.closest("[data-control-section]");
  if (!section) return;
  const open = trigger.getAttribute("aria-expanded") !== "true";
  state.controlSectionOpen[section.dataset.controlSection] = open;
  setControlSectionElementOpen(section, open);
}

function setControlSectionElementOpen(section, open) {
  const trigger = section.querySelector(".control-section-trigger");
  const body = section.querySelector(".control-section-body");
  section.classList.toggle("is-expanded", open);
  trigger?.setAttribute("aria-expanded", String(open));
  body?.setAttribute("aria-hidden", String(!open));
  if (open) body?.removeAttribute("inert");
  else body?.setAttribute("inert", "");
}

function handleInput(event) {
  const element = event.target;
  if (element.matches("[data-prompt-editor-input]")) {
    updatePromptEditorStats(element.value);
    return;
  }
  if (element.id === "gallery-scale") {
    updateGalleryScale(element.value, false);
    return;
  }
  if (element.id === "creative-direction") {
    state.promptAssistant.creativeDirection = element.value;
    scheduleAutoGenerate();
    return;
  }
  if (element.name === "assistant-mode") {
    state.promptAssistant.mode = element.value;
    scheduleAutoGenerate();
    return;
  }
  if (element.matches("[data-control-id]") && !element.matches("input[type=file]")) {
    const control = updateControlFromElement(element);
    syncNumberControlPair(element);
    syncChoiceStrengthControl(control);
    if (element.dataset.resolutionPart || element.dataset.resolutionAxis) {
      const container = element.dataset.resolutionAxis
        ? element.closest("[data-resolution-pair-block]")
        : element.closest("[data-control-block]");
      const grid = container?.querySelector("[data-resolution-grid]");
      updateResolutionUi(grid, resolutionValueForGrid(grid));
    }
    if (element.type === "checkbox") {
      const stateLabel = element.closest(".switch")?.querySelector("em");
      if (stateLabel) stateLabel.textContent = element.checked ? "On" : "Off";
    }
    persistActiveParameterState();
    syncParameterValidation(element.dataset.controlId);
    const companion = choiceStrengthCompanion(sourceInterface(state.activeSource), control);
    if (control?.type === "choice" && companion) syncParameterValidation(companion.id);
  }
}

function openPromptEditor(button) {
  const controlId = button.dataset.promptControlId;
  const control = interfaceInputs(sourceInterface(state.activeSource)).find((item) => item.id === controlId);
  const dialog = document.querySelector("#prompt-editor-dialog");
  const source = document.querySelector(`[data-control-id="${CSS.escape(controlId || "")}"]`);
  if (!controlId || !control || !dialog || !source || source.disabled) return;

  const label = controlId === "prompt.text" && !control.semantic_role ? "Prompt" : control.label || controlId;
  const selection = {
    start: source.selectionStart ?? source.value.length,
    end: source.selectionEnd ?? source.value.length,
    direction: source.selectionDirection || "none",
  };
  promptEditorReturnFocus = button;
  dialog.dataset.promptControlId = controlId;
  delete dialog.dataset.promptAssistantCompositionId;
  delete dialog.dataset.promptAssistantModel;
  dialog.returnValue = "";
  dialog.innerHTML = promptEditorMarkup(controlId, label, source.value, state.promptAssistant);
  dialog.showModal();
  syncSpeechControls();
  queueMicrotask(() => {
    const editor = dialog.querySelector("[data-prompt-editor-input]");
    editor?.focus({ preventScroll: true });
    try {
      editor?.setSelectionRange(selection.start, selection.end, selection.direction);
    } catch {
      // The prompt editor remains usable if the browser cannot restore a selection range.
    }
  });
}

function closePromptEditor(returnValue) {
  const dialog = document.querySelector("#prompt-editor-dialog");
  if (dialog?.open) dialog.close(returnValue);
}

function applyPromptEditor() {
  const dialog = document.querySelector("#prompt-editor-dialog");
  const editor = dialog?.querySelector("[data-prompt-editor-input]");
  const creativeDirection = dialog?.querySelector("#prompt-editor-creative-direction");
  const assistantMode = dialog?.querySelector(
    '[name="prompt-editor-assistant-mode"]:checked',
  );
  const controlId = dialog?.dataset.promptControlId;
  const control = interfaceInputs(sourceInterface(state.activeSource)).find((item) => item.id === controlId);
  if (!dialog?.open || !editor || !controlId || !control) return;

  state.parameters[controlId] = normalizeInputValue(control, editor.value);
  state.explicitParameterIds.add(controlId);
  delete state.serverFieldErrors[controlId];
  state.formError = null;
  state.promptAssistant.creativeDirection = creativeDirection?.value || "";
  state.promptAssistant.mode = assistantMode?.value === "create" ? "create" : "refine";
  if (dialog.dataset.promptAssistantCompositionId) {
    state.compositionId = dialog.dataset.promptAssistantCompositionId;
    state.promptAssistant.historicalModel = dialog.dataset.promptAssistantModel || null;
  }
  persistActiveParameterState();
  renderPanel();
  dialog.close("apply");
}

function selectPromptEditorText() {
  const editor = document.querySelector("#prompt-editor-dialog[open] [data-prompt-editor-input]");
  editor?.focus();
  editor?.select();
}

function clearPromptEditorText() {
  const editor = document.querySelector("#prompt-editor-dialog[open] [data-prompt-editor-input]");
  if (!editor) return;
  editor.value = "";
  updatePromptEditorStats("");
  editor.focus();
}

function updatePromptEditorStats(value) {
  const dialog = document.querySelector("#prompt-editor-dialog");
  if (!dialog) return;
  const text = String(value ?? "");
  const words = text.trim() ? text.trim().split(/\s+/u).length : 0;
  const wordCount = dialog.querySelector("[data-prompt-word-count]");
  const characterCount = dialog.querySelector("[data-prompt-character-count]");
  if (wordCount) wordCount.textContent = `${words.toLocaleString()} ${words === 1 ? "word" : "words"}`;
  if (characterCount) {
    characterCount.textContent = `${text.length.toLocaleString()} ${text.length === 1 ? "character" : "characters"}`;
  }
}

function speechCaptureUnavailableMessage() {
  if (!navigator.mediaDevices?.getUserMedia) {
    return "This browser cannot access a microphone from this page.";
  }
  if (!("MediaRecorder" in window)) {
    return "This browser does not support microphone recording.";
  }
  return null;
}

function syncSpeechControls() {
  const browserMessage = speechCaptureUnavailableMessage();
  for (const button of root.querySelectorAll('[data-action="toggle-speech-recording"]')) {
    const targetId = button.dataset.speechTarget;
    const label = button.dataset.speechLabel || "text";
    const session = activeSpeechSession;
    const matchesSession = Boolean(session && session.targetId === targetId);
    const permanentlyDisabled = button.dataset.speechControlDisabled === "true";
    let actionLabel = `Start voice input for ${label}`;
    let title = actionLabel;
    let disabled =
      permanentlyDisabled ||
      !document.getElementById(targetId) ||
      !state.speechToText.available ||
      Boolean(browserMessage) ||
      Boolean(session && !matchesSession);

    button.classList.toggle("is-recording", matchesSession && session.phase === "recording");
    button.classList.toggle("is-transcribing", matchesSession && session.phase === "transcribing");
    button.setAttribute(
      "aria-pressed",
      String(matchesSession && session.phase === "recording"),
    );
    button.removeAttribute("aria-busy");

    if (!state.speechToText.available) {
      title = state.speechToText.message || "Voice input is unavailable.";
    } else if (browserMessage) {
      title = browserMessage;
    } else if (matchesSession && session.phase === "requesting") {
      actionLabel = `Cancel microphone request for ${label}`;
      title = actionLabel;
      disabled = false;
      button.setAttribute("aria-busy", "true");
    } else if (matchesSession && session.phase === "recording") {
      actionLabel = `Stop recording for ${label}`;
      title = actionLabel;
      disabled = false;
    } else if (matchesSession && session.phase === "transcribing") {
      actionLabel = `Transcribing voice input for ${label}`;
      title = actionLabel;
      disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    button.disabled = disabled;
    button.setAttribute("aria-label", actionLabel);
    button.title = title;
  }
}

async function toggleSpeechRecording(button) {
  const targetId = button.dataset.speechTarget;
  const label = button.dataset.speechLabel || "text";
  const target = document.getElementById(targetId);
  if (!targetId || !target) return;

  if (activeSpeechSession) {
    if (activeSpeechSession.targetId !== targetId) return;
    if (activeSpeechSession.phase === "requesting") discardSpeechSession();
    else if (activeSpeechSession.phase === "recording") stopSpeechRecording(activeSpeechSession);
    return;
  }

  if (!state.speechToText.available) {
    throw new Error(state.speechToText.message || "Voice input is unavailable.");
  }
  const browserMessage = speechCaptureUnavailableMessage();
  if (browserMessage) throw new Error(browserMessage);

  const session = {
    id: ++speechSessionSequence,
    targetId,
    targetElement: target,
    label,
    selection: textSelection(target),
    phase: "requesting",
    stream: null,
    recorder: null,
    chunks: [],
    discarded: false,
  };
  activeSpeechSession = session;
  syncSpeechControls();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    session.stream = stream;
    if (session.discarded || activeSpeechSession !== session) {
      stopSpeechTracks(stream);
      return;
    }
    const mimeType = preferredRecordingMimeType();
    const recorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);
    session.recorder = recorder;
    recorder.ondataavailable = (event) => {
      if (event.data?.size) session.chunks.push(event.data);
    };
    recorder.onerror = () => {
      session.discarded = true;
      stopSpeechTracks(session.stream);
      if (activeSpeechSession === session) activeSpeechSession = null;
      syncSpeechControls();
      toast("The browser could not record microphone audio.", "error");
    };
    recorder.onstop = () => {
      transcribeSpeechSession(session).catch((error) => {
        toast(error.message || "Voice input failed.", "error");
      });
    };
    recorder.start();
    session.phase = "recording";
    syncSpeechControls();
  } catch (error) {
    if (session.discarded && activeSpeechSession !== session) return;
    session.discarded = true;
    stopSpeechTracks(session.stream);
    if (activeSpeechSession === session) activeSpeechSession = null;
    syncSpeechControls();
    throw new Error(microphoneErrorMessage(error));
  }
}

function textSelection(element) {
  const fallback = String(element.value ?? "").length;
  return {
    start: element.selectionStart ?? fallback,
    end: element.selectionEnd ?? fallback,
  };
}

function preferredRecordingMimeType() {
  const choices = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  return choices.find((value) => MediaRecorder.isTypeSupported?.(value)) || "";
}

function stopSpeechRecording(session) {
  if (session.recorder?.state !== "recording") return;
  session.phase = "transcribing";
  syncSpeechControls();
  try {
    session.recorder.stop();
  } catch {
    session.discarded = true;
    stopSpeechTracks(session.stream);
    if (activeSpeechSession === session) activeSpeechSession = null;
    syncSpeechControls();
    toast("The browser could not finish the recording.", "error");
  }
}

async function transcribeSpeechSession(session) {
  stopSpeechTracks(session.stream);
  if (session.discarded) return;
  const contentType =
    session.recorder?.mimeType || session.chunks.find((chunk) => chunk.type)?.type || "audio/webm";
  const recording = new Blob(session.chunks, { type: contentType });
  if (!recording.size) {
    finishSpeechSession(session);
    throw new Error("The recording was empty. Try speaking after the recording indicator appears.");
  }
  const file = new File(
    [recording],
    `recording-${session.id}.${recordingExtension(contentType)}`,
    { type: contentType },
  );
  try {
    const result = await upload("/api/speech-to-text/transcriptions", file);
    if (session.discarded) return;
    const target = document.getElementById(session.targetId);
    const dialog = document.querySelector("#prompt-editor-dialog");
    if (!target || (dialog?.contains(target) && !dialog.open)) {
      throw new Error("The voice transcript finished after its editor closed and was not inserted.");
    }
    const inserted = insertTranscription(
      target.value,
      result.text,
      session.selection.start,
      session.selection.end,
    );
    target.value = inserted.value;
    target.dispatchEvent(new Event("input", { bubbles: true }));
    target.focus({ preventScroll: true });
    target.setSelectionRange?.(inserted.cursor, inserted.cursor);
    toast(`Voice transcript inserted into ${session.label}.`, "success");
  } finally {
    finishSpeechSession(session);
  }
}

function finishSpeechSession(session) {
  stopSpeechTracks(session.stream);
  if (activeSpeechSession === session) activeSpeechSession = null;
  syncSpeechControls();
}

function discardSpeechSession() {
  const session = activeSpeechSession;
  if (!session) return;
  session.discarded = true;
  stopSpeechTracks(session.stream);
  if (session.recorder && session.recorder.state !== "inactive") {
    try {
      session.recorder.stop();
    } catch {
      // Tracks are already stopped and the discarded transcript will never be inserted.
    }
  }
  activeSpeechSession = null;
  syncSpeechControls();
}

function stopSpeechTracks(stream) {
  for (const track of stream?.getTracks?.() || []) track.stop();
}

function recordingExtension(contentType) {
  if (contentType.includes("ogg")) return "ogg";
  if (contentType.includes("mp4")) return "m4a";
  if (contentType.includes("mpeg")) return "mp3";
  if (contentType.includes("wav")) return "wav";
  return "webm";
}

function microphoneErrorMessage(error) {
  if (error?.name === "NotAllowedError" || error?.name === "SecurityError") {
    return "Microphone access was denied. Allow access in the browser and try again.";
  }
  if (error?.name === "NotFoundError" || error?.name === "DevicesNotFoundError") {
    return "No microphone was found.";
  }
  return "The browser could not start microphone recording.";
}

function handlePromptEditorClose(event) {
  const target = activeSpeechSession
    ? document.getElementById(activeSpeechSession.targetId)
    : null;
  if (target && event.currentTarget.contains(target)) discardSpeechSession();
  restorePromptEditorFocus(event);
}

function restorePromptEditorFocus(event) {
  const controlId = event.currentTarget.dataset.promptControlId;
  const previous = promptEditorReturnFocus;
  promptEditorReturnFocus = null;
  queueMicrotask(() => {
    const fallback = document.querySelector(
      `[data-action="open-prompt-editor"][data-prompt-control-id="${CSS.escape(controlId || "")}"]`,
    );
    const target = previous?.isConnected ? previous : fallback;
    if (target && !target.disabled) target.focus({ preventScroll: true });
  });
}

function handlePointerDown(event) {
  if (event.button !== 0) return;
  const photo = event.target.closest("#photo-viewer[open] .photo-viewer-media img");
  if (photo) {
    event.preventDefault();
    activePhotoViewerDrag = {
      captureTarget: photo,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startPanX: state.photoViewerPanX,
      startPanY: state.photoViewerPanY,
    };
    document.querySelector("#photo-viewer")?.classList.add("is-panning");
    try {
      photo.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture is an enhancement; delegated pointer events remain the fallback.
    }
    notePhotoViewerActivity();
    return;
  }
  const grid = event.target.closest("[data-resolution-grid]");
  if (!grid || grid.dataset.resolutionDisabled === "true") return;
  const handle = event.target.closest("[data-resolution-handle]");
  const captureTarget = handle || grid;
  event.preventDefault();
  activeResolutionDrag = {
    captureTarget,
    grid,
    mode: handle?.dataset.resolutionHandle || "both",
    pointerId: event.pointerId,
  };
  try {
    captureTarget.setPointerCapture(event.pointerId);
  } catch {
    // Pointer capture is an enhancement; delegated pointer events remain the fallback.
  }
  if (!handle) updateResolutionFromPointer(event, activeResolutionDrag);
}

function handlePointerMove(event) {
  if (document.querySelector("#photo-viewer")?.open) notePhotoViewerActivity();
  if (activePhotoViewerDrag && event.pointerId === activePhotoViewerDrag.pointerId) {
    event.preventDefault();
    state.photoViewerPanX = activePhotoViewerDrag.startPanX + event.clientX - activePhotoViewerDrag.startX;
    state.photoViewerPanY = activePhotoViewerDrag.startPanY + event.clientY - activePhotoViewerDrag.startY;
    applyPhotoViewerTransform();
    return;
  }
  if (!activeResolutionDrag || event.pointerId !== activeResolutionDrag.pointerId) return;
  event.preventDefault();
  updateResolutionFromPointer(event, activeResolutionDrag);
}

function handlePointerEnd(event) {
  if (activePhotoViewerDrag && event.pointerId === activePhotoViewerDrag.pointerId) {
    finishPhotoViewerDrag();
    return;
  }
  if (!activeResolutionDrag || event.pointerId !== activeResolutionDrag.pointerId) return;
  const { captureTarget, grid, mode, pointerId } = activeResolutionDrag;
  activeResolutionDrag = null;
  try {
    if (captureTarget.hasPointerCapture(pointerId)) captureTarget.releasePointerCapture(pointerId);
  } catch {
    // The browser may release capture before pointercancel reaches the delegated handler.
  }
  renderPanelWithResolutionFocus(grid, mode);
}

function handlePhotoViewerWheel(event) {
  const photo = event.target.closest("#photo-viewer[open] .photo-viewer-media img");
  if (!photo || (!event.deltaX && !event.deltaY)) return;
  event.preventDefault();
  notePhotoViewerActivity();

  const deltaX =
    event.deltaX *
    (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerWidth : 1);
  const deltaY =
    event.deltaY *
    (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1);
  if (!event.ctrlKey || !deltaY) {
    state.photoViewerPanX -= deltaX;
    state.photoViewerPanY -= deltaY;
    applyPhotoViewerTransform();
    return;
  }

  const media = photo.closest(".photo-viewer-media");
  const rect = media.getBoundingClientRect();
  const zoomFactor = Math.exp((-deltaY * Math.log(1.12)) / 100);
  const pointerX = event.clientX - (rect.left + rect.width / 2);
  const pointerY = event.clientY - (rect.top + rect.height / 2);

  state.photoViewerPanX = pointerX - (pointerX - state.photoViewerPanX) * zoomFactor;
  state.photoViewerPanY = pointerY - (pointerY - state.photoViewerPanY) * zoomFactor;
  state.photoViewerZoom *= zoomFactor;
  applyPhotoViewerTransform();
}

function handleKeyDown(event) {
  if (
    event.target.matches("[data-prompt-editor-input]") &&
    event.key === "Enter" &&
    (event.ctrlKey || event.metaKey) &&
    !event.isComposing
  ) {
    event.preventDefault();
    applyPromptEditor();
    return;
  }
  const photoViewer = document.querySelector("#photo-viewer");
  if (photoViewer?.open) return;
  const handle = event.target.closest("[data-resolution-handle]");
  if (!handle || handle.disabled) return;
  const grid = handle.closest("[data-resolution-grid]");
  if (!grid) return;
  const mode = handle.dataset.resolutionHandle;
  const current = resolutionValueForGrid(grid);
  const limits = resolutionGridLimits(grid);
  let width = Number(current.width) || 0;
  let height = Number(current.height) || 0;
  let handled = true;

  if (event.key === "ArrowLeft" && mode !== "height") {
    width = snapResolutionValue(width - limits.widthStep, limits.minimumWidth, limits.maximumWidth, limits.widthStep);
  } else if (event.key === "ArrowRight" && mode !== "height") {
    width = snapResolutionValue(width + limits.widthStep, limits.minimumWidth, limits.maximumWidth, limits.widthStep);
  } else if (event.key === "ArrowDown" && mode !== "width") {
    height = snapResolutionValue(height - limits.heightStep, limits.minimumHeight, limits.maximumHeight, limits.heightStep);
  } else if (event.key === "ArrowUp" && mode !== "width") {
    height = snapResolutionValue(height + limits.heightStep, limits.minimumHeight, limits.maximumHeight, limits.heightStep);
  } else if (event.key === "Home") {
    if (mode !== "height") width = limits.minimumWidth;
    if (mode !== "width") height = limits.minimumHeight;
  } else if (event.key === "End") {
    if (mode !== "height") width = limits.maximumWidth;
    if (mode !== "width") height = limits.maximumHeight;
  } else {
    handled = false;
  }

  if (!handled) return;
  event.preventDefault();
  setResolutionValue(grid, width, height);
}

function handlePhotoViewerKeyDown(event) {
  if (!document.querySelector("#photo-viewer")?.open) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else closePhotoViewer();
    return;
  }
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  event.stopPropagation();
  notePhotoViewerActivity();
  navigatePhotoViewer(event.key === "ArrowLeft" ? "newer" : "older").catch((error) => {
    toast(error.message || "Could not navigate the gallery.", "error");
  });
}

function handleKeyUp(event) {
  const handle = event.target.closest("[data-resolution-handle]");
  if (!handle || !["ArrowLeft", "ArrowRight", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const grid = handle.closest("[data-resolution-grid]");
  if (grid) renderPanelWithResolutionFocus(grid, handle.dataset.resolutionHandle);
}

function updateResolutionFromPointer(event, drag) {
  const rect = drag.grid.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const limits = resolutionGridLimits(drag.grid);
  const current = resolutionValueForGrid(drag.grid);
  let width = Number(current.width) || 0;
  let height = Number(current.height) || 0;
  if (drag.mode !== "height") {
    const rawWidth = limits.minimumWidth + ((event.clientX - rect.left) / rect.width) * (limits.maximumWidth - limits.minimumWidth);
    width = snapResolutionValue(rawWidth, limits.minimumWidth, limits.maximumWidth, limits.widthStep);
  }
  if (drag.mode !== "width") {
    const rawHeight = limits.minimumHeight + ((rect.bottom - event.clientY) / rect.height) * (limits.maximumHeight - limits.minimumHeight);
    height = snapResolutionValue(rawHeight, limits.minimumHeight, limits.maximumHeight, limits.heightStep);
  }
  setResolutionValue(drag.grid, width, height);
}

function setResolutionValue(grid, width, height) {
  const widthId = grid.dataset.resolutionWidthId;
  const heightId = grid.dataset.resolutionHeightId;
  if (widthId && heightId) {
    state.parameters[widthId] = width;
    state.parameters[heightId] = height;
    state.explicitParameterIds.add(widthId);
    state.explicitParameterIds.add(heightId);
    delete state.serverFieldErrors[widthId];
    delete state.serverFieldErrors[heightId];
  } else {
    const id = grid.dataset.controlId;
    state.parameters[id] = { width, height };
    state.explicitParameterIds.add(id);
    delete state.serverFieldErrors[id];
  }
  state.formError = null;
  persistActiveParameterState();
  updateResolutionUi(grid, resolutionValueForGrid(grid));
}

function resolutionValueForGrid(grid) {
  if (!grid) return {};
  const widthId = grid.dataset.resolutionWidthId;
  const heightId = grid.dataset.resolutionHeightId;
  if (widthId && heightId) {
    return { width: state.parameters[widthId], height: state.parameters[heightId] };
  }
  return state.parameters[grid.dataset.controlId] || {};
}

function updateResolutionUi(grid, value) {
  if (!grid) return;
  const limits = resolutionGridLimits(grid);
  const width = Number(value?.width) || 0;
  const height = Number(value?.height) || 0;
  const positionX = resolutionPosition(width, limits.minimumWidth, limits.maximumWidth);
  const positionY = resolutionPosition(height, limits.minimumHeight, limits.maximumHeight);
  const summary = resolutionSummary(width, height);
  grid.style.setProperty("--resolution-x", `${positionX}%`);
  grid.style.setProperty("--resolution-y", `${positionY}%`);
  grid.style.setProperty("--resolution-x-mid", `${positionX / 2}%`);
  grid.style.setProperty("--resolution-y-mid", `${positionY / 2}%`);
  const block = grid.closest("[data-resolution-pair-block], [data-control-block]");
  const widthInput = block?.querySelector('[data-resolution-axis="width"], [data-resolution-part="width"]');
  const heightInput = block?.querySelector('[data-resolution-axis="height"], [data-resolution-part="height"]');
  const caption = block?.querySelector("[data-resolution-summary]");
  const sectionStatus = grid
    .closest('[data-control-section="resolution"]')
    ?.querySelector('[data-control-section-status="resolution"]');
  if (widthInput) widthInput.value = value?.width ?? "";
  if (heightInput) heightInput.value = value?.height ?? "";
  if (caption) caption.textContent = summary.text;
  if (sectionStatus) sectionStatus.textContent = `${summary.width} × ${summary.height}`;
  grid
    .querySelector('[data-resolution-handle="both"]')
    ?.setAttribute("aria-label", `Adjust width and height. ${summary.width} by ${summary.height} pixels. Use the arrow keys.`);
  grid
    .querySelector('[data-resolution-handle="width"]')
    ?.setAttribute("aria-label", `Adjust width. ${summary.width} pixels. Use the left and right arrow keys.`);
  grid
    .querySelector('[data-resolution-handle="height"]')
    ?.setAttribute("aria-label", `Adjust height. ${summary.height} pixels. Use the up and down arrow keys.`);
}

function resolutionGridLimits(grid) {
  return {
    minimumWidth: Number(grid.dataset.resolutionMinWidth),
    maximumWidth: Number(grid.dataset.resolutionMaxWidth),
    minimumHeight: Number(grid.dataset.resolutionMinHeight),
    maximumHeight: Number(grid.dataset.resolutionMaxHeight),
    widthStep: Number(grid.dataset.resolutionWidthStep),
    heightStep: Number(grid.dataset.resolutionHeightStep),
  };
}

function resolutionPosition(value, minimum, maximum) {
  if (maximum <= minimum) return 0;
  return Math.max(0, Math.min(100, ((value - minimum) / (maximum - minimum)) * 100));
}

function renderPanelWithResolutionFocus(grid, handle) {
  const selector = grid.dataset.resolutionWidthId
    ? `[data-resolution-grid][data-resolution-width-id="${CSS.escape(grid.dataset.resolutionWidthId)}"]`
    : `[data-resolution-grid][data-control-id="${CSS.escape(grid.dataset.controlId)}"]`;
  renderPanel();
  queueMicrotask(() => {
    document.querySelector(`${selector} [data-resolution-handle="${handle}"]`)?.focus();
  });
}

function updateControlFromElement(element) {
  const id = element.dataset.controlId;
  const control = interfaceInputs(state.activeSource?.interface).find((item) => item.id === id);
  if (!id || !control) return null;
  if (element.dataset.resolutionPart) {
    const current = state.parameters[id] || {};
    state.parameters[id] = {
      ...current,
      [element.dataset.resolutionPart]: element.value === "" ? null : Number(element.value),
    };
  } else if (element.dataset.jsonControl) {
    try {
      state.parameters[id] = JSON.parse(element.value);
    } catch {
      state.serverFieldErrors[id] = "Enter valid JSON.";
      return null;
    }
  } else if (control.type === "boolean") {
    state.parameters[id] = element.checked;
  } else if (control.type === "seed") {
    state.parameters[id] = { mode: "fixed", value: element.value.trim() };
  } else {
    state.parameters[id] = normalizeInputValue(control, element.value);
  }
  if (control.type === "number" && state.parameters[id] === null) {
    state.explicitParameterIds.delete(id);
  } else {
    state.explicitParameterIds.add(id);
  }
  if (control.type === "choice") {
    const contract = sourceInterface(state.activeSource);
    const companion = choiceStrengthCompanion(contract, control);
    state.parameters = applyChoiceStrengthDefaults(
      contract,
      state.parameters,
      state.explicitParameterIds,
      control.id,
    );
    if (companion && !state.explicitParameterIds.has(companion.id)) {
      delete state.serverFieldErrors[companion.id];
    }
  }
  delete state.serverFieldErrors[id];
  state.formError = null;
  return control;
}

function syncChoiceStrengthControl(control) {
  if (control?.type !== "choice") return;
  const companion = choiceStrengthCompanion(sourceInterface(state.activeSource), control);
  if (!companion || state.explicitParameterIds.has(companion.id)) return;
  const block = document.querySelector(
    `[data-control-block="${CSS.escape(companion.id)}"]`,
  );
  if (!block) return;
  const value = state.parameters[companion.id] ?? "";
  const entry = block.querySelector("[data-number-entry]");
  const slider = block.querySelector("[data-number-slider]");
  if (entry) entry.value = value;
  if (slider) slider.value = value;
}

function syncNumberControlPair(element) {
  if (!element.matches("[data-number-entry], [data-number-slider]")) return;
  const block = element.closest("[data-control-block]");
  if (!block) return;
  if (element.matches("[data-number-slider]")) {
    const exact = block.querySelector("[data-number-entry]");
    if (exact) exact.value = element.value;
    return;
  }
  const slider = block.querySelector("[data-number-slider]");
  const numeric = Number(element.value);
  if (
    !slider ||
    element.value === "" ||
    !Number.isFinite(numeric) ||
    numeric < Number(slider.min) ||
    numeric > Number(slider.max)
  )
    return;
  slider.value = element.value;
}

function syncParameterValidation(controlId) {
  const contract = sourceInterface(state.activeSource);
  const errors = {
    ...clientValidate(contract, state.parameters),
    ...withoutNulls(state.serverFieldErrors),
  };
  state.fieldErrors = errors;

  const block = document.querySelector(
    `[data-control-block="${CSS.escape(controlId || "")}"]`,
  );
  if (block) syncFieldError(block, controlId, errors[controlId]);
  document.querySelector(".form-error.summary")?.remove();

  const generateButton = document.querySelector("#generate-button");
  if (generateButton) {
    const selected =
      state.activeSource ||
      state.sources.find((item) => sourceKey(item) === state.activeSourceKey);
    generateButton.disabled = generationSubmissionDisabled(
      state,
      selected,
      contract,
      errors,
    );
  }
  scheduleAutoGenerate();
}

function syncFieldError(block, controlId, message) {
  const errorId = `control-${String(controlId || "").replaceAll(/[^A-Za-z0-9_-]/g, "-")}-error`;
  let error = block.querySelector(".field-error");
  if (message) {
    if (!error) {
      error = document.createElement("p");
      error.className = "field-error";
      error.id = errorId;
      error.setAttribute("role", "alert");
      block.append(error);
    }
    error.textContent = message;
  } else {
    error?.remove();
  }

  for (const element of block.querySelectorAll("[data-control-id]:not([data-resolution-grid])")) {
    const describedBy = new Set((element.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
    describedBy.delete(errorId);
    if (message) {
      element.setAttribute("aria-invalid", "true");
      describedBy.add(errorId);
    } else {
      element.removeAttribute("aria-invalid");
    }
    if (describedBy.size) element.setAttribute("aria-describedby", [...describedBy].join(" "));
    else element.removeAttribute("aria-describedby");
  }
}

async function submitLogin(form) {
  setBusy(form, true);
  clearAuthError();
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: form.elements.username.value,
        password: form.elements.password.value,
      }),
    });
    state.session = result;
    setCsrfToken(result.csrf_token);
    if (result.user.must_change_password) renderPasswordChange(true);
    else await enterApplication();
  } catch (error) {
    showAuthError(error.message);
  } finally {
    setBusy(form, false);
  }
}

async function submitPassword(form) {
  clearAuthError();
  const password = form.elements.new_password.value;
  if (password !== form.elements.confirm_password.value) {
    showAuthError("New password confirmation does not match.");
    return;
  }
  setBusy(form, true);
  try {
    await api("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: form.elements.current_password?.value || null,
        new_password: password,
      }),
    });
    const session = await api("/api/auth/session", {
      operation: "Session request",
      deadlineMs: STARTUP_DEADLINES.session,
    });
    state.session = session;
    setCsrfToken(session.csrf_token);
    state.changingPasswordFromApp = false;
    await enterApplication();
    toast("Password updated.", "success");
  } catch (error) {
    showAuthError(error.message);
  } finally {
    setBusy(form, false);
  }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  stopLiveUpdates();
  stopApplicationStartup();
  state.sources = [];
  state.activeSourceKey = null;
  state.activeSource = null;
  state.comparisonSourceKeys = new Set();
  state.sourcePickerDialogOpen = false;
  state.sourcePickerDraft = null;
  state.sourceRatings = {};
  sourceRatingsRevision += 1;
  state.parameters = {};
  state.explicitParameterIds = new Set();
  state.parameterStateBySource = {};
  state.pendingSourceMigration = null;
  state.sourceCatalogStatus = "idle";
  state.sourceCatalogRefreshPending = false;
  state.servicePanelRefreshPending = false;
  state.sourceCatalogToken += 1;
  state.sourceLoadToken += 1;
  state.services = [];
  state.servicesStatus = "idle";
  state.servicesMessage = null;
  state.generations = [];
  state.nextCursor = null;
  startupGalleryBoundary = null;
  state.galleryStatus = "idle";
  state.galleryMessage = null;
  state.autoGenerate = false;
  const session = await api("/api/auth/session", {
    operation: "Session request",
    deadlineMs: STARTUP_DEADLINES.session,
  });
  state.session = session;
  setCsrfToken(session.csrf_token);
  renderLogin();
}

function renderLogin() {
  stopLiveUpdates();
  stopApplicationStartup();
  root.innerHTML = loginMarkup(state.session?.app_title || "ImageGen V2");
  queueMicrotask(() => root.querySelector("input")?.focus());
}

function renderPasswordChange(forced) {
  stopLiveUpdates();
  stopApplicationStartup();
  root.innerHTML = passwordChangeMarkup(state.session?.app_title || "ImageGen V2", forced);
  queueMicrotask(() => root.querySelector("input")?.focus());
}

async function enterApplication() {
  stopLiveUpdates();
  stopApplicationStartup();
  const controller = new AbortController();
  applicationStartupController = controller;
  state.sourceCatalogStatus = "loading";
  state.sourceCatalogMessage = null;
  state.services = [];
  state.servicesStatus = "loading";
  state.servicesMessage = null;
  state.generations = [];
  state.nextCursor = null;
  state.galleryStatus = "loading";
  state.galleryMessage = null;
  state.autoGenerate = false;
  state.favorites = [];
  state.favoritesNextCursor = null;
  state.sourceRatings = {};
  sourceRatingsRevision += 1;
  state.promptAssistant = {
    ...state.promptAssistant,
    available: false,
    message: "Checking Prompt Assistant availability…",
  };
  state.speechToText = {
    available: false,
    message: "Checking voice input availability…",
  };
  root.innerHTML = shellMarkup(state);
  document.querySelector("#photo-viewer")?.addEventListener("close", resetPhotoViewerState);
  document.querySelector("#prompt-editor-dialog")?.addEventListener("close", handlePromptEditorClose);
  document
    .querySelector("#source-picker-dialog")
    ?.addEventListener("close", handleSourcePickerDialogClose);
  renderPanel();
  renderGallery();
  renderServiceBanner();
  applyGalleryScale();
  setupPaginationObserver();
  startLiveUpdates({ paused: true });
  const servicesRequest = loadStartupServices(controller.signal).finally(() => {
    if (!controller.signal.aborted && applicationStartupController === controller) {
      startServicePolling();
    }
  });
  const galleryRequest = loadStartupGallery(controller.signal).finally(() => {
    if (!controller.signal.aborted && applicationStartupController === controller) {
      resumeLiveUpdates();
    }
  });
  const requests = [
    loadStartupPreferences(controller.signal),
    servicesRequest,
    galleryRequest,
    loadStartupPromptAssistant(controller.signal),
    loadStartupSpeechToText(controller.signal),
    loadSources({ signal: controller.signal, diagnostic: true }),
  ];
  void Promise.allSettled(requests);
}

function stopApplicationStartup() {
  applicationStartupController?.abort();
  applicationStartupController = null;
}

function requestWasAborted(error, signal) {
  return Boolean(signal?.aborted || error?.name === "AbortError");
}

async function loadStartupPreferences(signal = applicationStartupController?.signal) {
  const ratingsRevision = sourceRatingsRevision;
  try {
    const preferences = await startupGet("/api/preferences", {
      operation: "Display preferences",
      deadlineMs: STARTUP_DEADLINES.preferences,
      signal,
    });
    if (signal?.aborted) return;
    state.galleryScale = preferences.gallery_scale;
    if (ratingsRevision === sourceRatingsRevision) {
      state.sourceRatings = normalizedSourceRatings(preferences.source_ratings);
      if (state.sourcePickerDialogOpen) renderSourcePickerDialog();
    }
    applyGalleryScale();
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    toast(`Display preferences unavailable: ${error.message}`, "error");
  }
}

function normalizedSourceRatings(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, rating]) => [key, Number(rating)])
      .filter(([, rating]) => Number.isInteger(rating) && rating >= 1 && rating <= 5),
  );
}

async function loadStartupServices(signal = applicationStartupController?.signal) {
  try {
    const services = await startupGet("/api/services", {
      operation: "Service status",
      deadlineMs: STARTUP_DEADLINES.services,
      signal,
    });
    if (signal?.aborted) return;
    state.services = Array.isArray(services) ? services : [];
    state.servicesStatus = "ready";
    state.servicesMessage = null;
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    state.services = [];
    state.servicesStatus = "error";
    state.servicesMessage = error.message || "Service status is temporarily unavailable.";
  }
  renderServiceBanner();
  renderPanel();
}

async function loadStartupGallery(signal = applicationStartupController?.signal) {
  state.galleryStatus = "loading";
  state.galleryMessage = null;
  renderGallery();
  try {
    const page = await startupGet("/api/generations?limit=24", {
      operation: "Gallery history",
      deadlineMs: STARTUP_DEADLINES.gallery,
      signal,
    });
    if (signal?.aborted) return;
    const currentById = new Map(state.generations.map((item) => [item.id, item]));
    const incoming = sortGenerationsNewestFirst(Array.isArray(page.items) ? page.items : []);
    startupGalleryBoundary = {
      oldest: incoming.length ? incoming[incoming.length - 1] : null,
    };
    state.generations = sortGenerationsNewestFirst([
      ...state.generations,
      ...incoming.filter((item) => !currentById.has(item.id)),
    ]);
    state.nextCursor = page.next_cursor;
    state.galleryStatus = "ready";
    state.galleryMessage = null;
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    startupGalleryBoundary = null;
    state.galleryStatus = "error";
    state.galleryMessage = error.message || "Gallery history is temporarily unavailable.";
  }
  renderGallery();
  setupPaginationObserver();
  scheduleAutoGenerate();
}

async function loadStartupPromptAssistant(signal = applicationStartupController?.signal) {
  try {
    const assistant = await startupGet("/api/prompt-assistant/status", {
      operation: "Prompt Assistant status",
      deadlineMs: STARTUP_DEADLINES.promptAssistant,
      signal,
    });
    if (signal?.aborted) return;
    state.promptAssistant = {
      ...state.promptAssistant,
      available: Boolean(assistant.available),
      message: assistant.message,
    };
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    state.promptAssistant = {
      ...state.promptAssistant,
      available: false,
      message: error.message || "Prompt Assistant is temporarily unavailable.",
    };
  }
  renderPanel();
}

async function loadStartupSpeechToText(signal = applicationStartupController?.signal) {
  try {
    const speechToText = await startupGet("/api/speech-to-text/status", {
      operation: "Voice input status",
      deadlineMs: STARTUP_DEADLINES.speechToText,
      signal,
    });
    if (signal?.aborted) return;
    state.speechToText = speechToText;
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    state.speechToText = {
      available: false,
      message: error.message || "Voice input is temporarily unavailable.",
    };
  }
  renderPanel();
}

function sourceKey(source) {
  return source?.source_key || source?.profile_id || null;
}

function selectedComparisonSourceKeys() {
  return new Set(
    [...state.comparisonSourceKeys].filter((key) => key && key !== state.activeSourceKey),
  );
}

function hasComparisonSources() {
  return selectedComparisonSourceKeys().size > 0;
}

function sourceInterface(source) {
  return source?.interface || source?.contract || null;
}

function sourceRevision(source) {
  if (source?.revision) return source.revision;
  if (!source) return null;
  return {
    publication_id: source.workflow_version,
    workflow_sha256: source.ui_graph_sha256,
    api_sha256: source.api_graph_sha256,
    manifest_sha256: source.contract_sha256,
  };
}

function revisionsMatch(first, second) {
  const firstRevision = sourceRevision(first) || {};
  const secondRevision = sourceRevision(second) || {};
  return ["publication_id", "workflow_sha256", "api_sha256", "manifest_sha256"].every(
    (key) => firstRevision[key] === secondRevision[key],
  );
}

function sourceContextIsCurrent(key, revision) {
  return Boolean(
    key &&
      state.activeSourceKey === key &&
      state.activeSource &&
      revisionsMatch({ revision }, state.activeSource),
  );
}

function persistActiveParameterState() {
  if (!state.activeSourceKey || !sourceInterface(state.activeSource)) return;
  state.parameterStateBySource[state.activeSourceKey] = {
    interface: structuredClone(sourceInterface(state.activeSource)),
    revision: structuredClone(sourceRevision(state.activeSource)),
    values: structuredClone(state.parameters),
    explicitInputIds: [...state.explicitParameterIds],
  };
}

async function loadSources({ signal, diagnostic = false } = {}) {
  const catalogToken = ++state.sourceCatalogToken;
  state.sourceCatalogStatus = "loading";
  state.sourceCatalogMessage = null;
  renderPanel();
  try {
    const sources = diagnostic
      ? await startupGet("/api/workflows", {
          operation: "Generation source catalog",
          deadlineMs: STARTUP_DEADLINES.sources,
          signal,
        })
      : await api("/api/workflows", {
          operation: "Generation source catalog",
          deadlineMs: STARTUP_DEADLINES.sources,
          signal,
        });
    if (signal?.aborted || catalogToken !== state.sourceCatalogToken) return;
    state.sources = Array.isArray(sources) ? sources : [];
    const availableKeys = new Set(
      state.sources
        .filter((item) => item.available !== false)
        .map((item) => sourceKey(item)),
    );
    state.comparisonSourceKeys = new Set(
      [...state.comparisonSourceKeys].filter(
        (key) => key !== state.activeSourceKey && availableKeys.has(key),
      ),
    );
    state.sourceCatalogStatus = "ready";
    const selected = state.sources.find((item) => sourceKey(item) === state.activeSourceKey);
    const next = selected || state.sources.find((item) => item.available !== false) || state.sources[0] || null;
    if (!next) {
      persistActiveParameterState();
      state.sourceLoadToken += 1;
      state.activeSourceKey = null;
      state.activeSource = null;
      state.parameters = {};
      state.explicitParameterIds = new Set();
      state.sourceDetailLoading = false;
      state.sourceDetailError = null;
      renderPanel();
      return;
    }
    if (
      state.activeSource &&
      sourceKey(next) === state.activeSourceKey &&
      sourceInterface(state.activeSource) &&
      revisionsMatch(next, state.activeSource)
    ) {
      state.activeSource = { ...state.activeSource, ...next };
      renderPanel();
      return;
    }
    await selectSource(sourceKey(next), { summary: next, signal, diagnostic });
  } catch (error) {
    if (requestWasAborted(error, signal) || catalogToken !== state.sourceCatalogToken) return;
    state.sourceCatalogStatus = "error";
    state.sourceCatalogMessage = error.message || "Published sources could not be loaded.";
    renderPanel();
  }
}

async function selectSource(key, { summary = null, signal, diagnostic = false } = {}) {
  const activeMigration = sourceInterface(state.activeSource)
    ? {
        sourceKey: state.activeSourceKey,
        interface: structuredClone(sourceInterface(state.activeSource)),
        values: structuredClone(state.parameters),
        explicitInputIds: [...state.explicitParameterIds],
      }
    : state.pendingSourceMigration;
  const migration = activeMigration?.sourceKey !== key ? activeMigration : null;
  persistActiveParameterState();
  const token = ++state.sourceLoadToken;
  const resolvedSummary = summary || state.sources.find((item) => sourceKey(item) === key) || null;
  state.comparisonSourceKeys.delete(key);
  state.activeSourceKey = key || null;
  state.activeSource = resolvedSummary;
  state.pendingSourceMigration = migration;
  const saved = key ? state.parameterStateBySource[key] : null;
  state.parameters = structuredClone(saved?.values || {});
  state.explicitParameterIds = new Set(saved?.explicitInputIds || []);
  state.sourceDetailLoading = Boolean(key);
  state.sourceDetailError = null;
  state.selectedPreset = null;
  state.compositionId = null;
  state.serverFieldErrors = {};
  state.formError = null;
  renderPanel();
  if (!key) return;
  try {
    const path = `/api/workflows/${encodeURIComponent(key)}`;
    const detail = diagnostic
      ? await startupGet(path, {
          operation: "Generation source details",
          deadlineMs: STARTUP_DEADLINES.sourceDetail,
          signal,
        })
      : await api(path, {
          operation: "Generation source details",
          deadlineMs: STARTUP_DEADLINES.sourceDetail,
          signal,
        });
    if (signal?.aborted || token !== state.sourceLoadToken) return;
    const contract = sourceInterface(detail);
    if (!contract) throw new Error("The selected source has no public interface.");
    state.activeSource = { ...(resolvedSummary || {}), ...detail, interface: contract };
    const baseValues = reconcileInterfaceValues(
      contract,
      saved?.values || {},
      saved?.interface || null,
      saved?.explicitInputIds || [],
    );
    const migrated = migrateInterfaceState(
      contract,
      migration?.interface || null,
      migration?.values || {},
      migration?.explicitInputIds || [],
      baseValues,
      saved?.explicitInputIds || [],
    );
    state.parameters = migrated.values;
    state.explicitParameterIds = new Set(migrated.explicitInputIds);
    state.pendingSourceMigration = null;
    state.sourceDetailError = null;
    persistActiveParameterState();
  } catch (error) {
    if (requestWasAborted(error, signal) || token !== state.sourceLoadToken) return;
    state.sourceDetailError = error.message || "The selected source could not be described.";
  } finally {
    if (token === state.sourceLoadToken) {
      state.sourceDetailLoading = false;
      renderPanel();
    }
  }
}

function applyPreset(presetId) {
  state.selectedPreset = presetId;
  const contract = sourceInterface(state.activeSource);
  state.parameters = defaultsForInterface(contract);
  const preset = contract?.presets?.find((item) => item.id === presetId);
  const presetValues = structuredClone(preset?.values || {});
  state.explicitParameterIds = new Set(
    Object.entries(presetValues)
      .filter(([, value]) => value !== null && value !== undefined)
      .map(([id]) => id),
  );
  Object.assign(state.parameters, presetValues);
  state.parameters = applyChoiceStrengthDefaults(
    contract,
    state.parameters,
    state.explicitParameterIds,
  );
  state.serverFieldErrors = {};
  state.formError = null;
  persistActiveParameterState();
  renderPanel();
}

function renderPanel() {
  const panel = document.querySelector("#generation-panel");
  if (!panel) return;
  const panelView = capturePanelView(panel);
  const contract = sourceInterface(state.activeSource);
  const clientErrors = clientValidate(contract, state.parameters);
  state.fieldErrors = { ...clientErrors, ...withoutNulls(state.serverFieldErrors) };
  const selected = state.activeSource || state.sources.find((item) => sourceKey(item) === state.activeSourceKey);
  panel.innerHTML = generationPanelMarkup(state, selected, contract);
  const assistant = panel.querySelector("#prompt-assistant");
  if (assistant) {
    const direction = assistant.querySelector("#creative-direction");
    direction.value = state.promptAssistant.creativeDirection || "";
    const mode = assistant.querySelector(`[name=assistant-mode][value=${state.promptAssistant.mode}]`);
    if (mode) mode.checked = true;
    const button = assistant.querySelector("[data-action=compose-prompt]");
    if (!state.promptAssistant.available) {
      button.disabled = true;
    }
  }
  restorePanelView(panel, panelView);
  syncSpeechControls();
  scheduleAutoGenerate();
}

function capturePanelView(panel) {
  const view = {
    scrollTop: panel.querySelector("#panel-scroll")?.scrollTop || 0,
    selector: null,
    selection: null,
    sectionKey: null,
    sectionOpen: false,
  };
  const element = document.activeElement;
  if (!element || !panel.contains(element)) return view;
  let selector = null;
  if (element.id) {
    selector = `#${CSS.escape(element.id)}`;
  } else if (element.dataset.seedMode) {
    selector = `[data-seed-mode="${CSS.escape(element.dataset.seedMode)}"]`;
  } else if (element.name === "assistant-mode") {
    selector = `[name="assistant-mode"][value="${CSS.escape(element.value)}"]`;
  }
  if (!selector) return view;
  let selection = null;
  try {
    if (element.selectionStart !== null) {
      selection = {
        start: element.selectionStart,
        end: element.selectionEnd,
        direction: element.selectionDirection,
      };
    }
  } catch {
    // Selection ranges are only available on text-editing controls.
  }
  return {
    ...view,
    selector,
    selection,
    sectionKey: element.closest("[data-control-section]")?.dataset.controlSection || null,
    sectionOpen: Boolean(element.closest(".control-section.is-expanded")),
  };
}

function restorePanelView(panel, view) {
  const scroller = panel.querySelector("#panel-scroll");
  if (scroller) scroller.scrollTop = view.scrollTop;
  if (!view.selector) return;
  if (view.sectionKey && view.sectionOpen) {
    const section = panel.querySelector(
      `[data-control-section="${CSS.escape(view.sectionKey)}"]`,
    );
    if (section) setControlSectionElementOpen(section, true);
  }
  const element = panel.querySelector(view.selector);
  if (!element || element.disabled) return;
  element.focus({ preventScroll: true });
  if (!view.selection) return;
  try {
    element.setSelectionRange(
      view.selection.start,
      view.selection.end,
      view.selection.direction,
    );
  } catch {
    // The replacement control may no longer support a text selection.
  }
}

function scheduleAutoGenerate() {
  if (!state.autoGenerate || autoGenerateScheduled || autoGenerateCycleRunning) return;
  autoGenerateScheduled = true;
  queueMicrotask(() => {
    autoGenerateScheduled = false;
    void runAutoGenerateCycle().catch((error) => {
      toast(error.message || "Auto-generation failed.", "error");
    });
  });
}

function autoGenerationReady() {
  const contract = sourceInterface(state.activeSource);
  const selected =
    state.activeSource ||
    state.sources.find((item) => sourceKey(item) === state.activeSourceKey);
  const errors = {
    ...clientValidate(contract, state.parameters),
    ...withoutNulls(state.serverFieldErrors),
  };
  const galleryPending =
    state.galleryStatus !== undefined && state.galleryStatus !== "ready";
  const assistantRequired = Boolean(
    String(state.promptAssistant.creativeDirection || "").trim(),
  );
  return Boolean(
    state.autoGenerate &&
      !autoGenerateCycleRunning &&
      !promptCompositionRequests &&
      !galleryPending &&
      !hasActiveGeneration(state.generations) &&
      !generationRequestBlocked(state, selected, contract, errors) &&
      (!assistantRequired || state.promptAssistant.available),
  );
}

async function runAutoGenerateCycle() {
  if (!autoGenerationReady()) return;
  const requestSourceKey = state.activeSourceKey;
  let queued = false;
  autoGenerateCycleRunning = true;
  try {
    if (String(state.promptAssistant.creativeDirection || "").trim()) {
      const composed = await composePrompt(null, { automatic: true });
      if (!composed || !state.autoGenerate || hasActiveGeneration(state.generations)) return;
    }
    queued = await generate({ automatic: true });
  } finally {
    autoGenerateCycleRunning = false;
    if (
      state.autoGenerate &&
      (state.activeSourceKey !== requestSourceKey ||
        (queued && !hasActiveGeneration(state.generations)))
    ) {
      scheduleAutoGenerate();
    }
  }
}

async function generate({ automatic = false } = {}) {
  if (state.autoGenerate && !automatic) return;
  if (hasComparisonSources()) return generateSelectedSources();
  return generateSingleSource();
}

async function generateSingleSource() {
  const contract = sourceInterface(state.activeSource);
  const requestSourceKey = state.activeSourceKey;
  const requestRevision = structuredClone(sourceRevision(state.activeSource));
  const requestCompositionId = state.compositionId;
  if (
    !requestSourceKey ||
    !state.activeSource ||
    !contract ||
    state.activeSource.available === false
  )
    return false;
  const errors = clientValidate(contract, state.parameters);
  if (Object.keys(errors).length) {
    state.serverFieldErrors = errors;
    state.formError = "Review the highlighted controls.";
    renderPanel();
    focusFirstInvalid();
    return false;
  }
  state.submitting = true;
  state.formError = null;
  state.serverFieldErrors = {};
  renderPanel();
  let focusErrors = false;
  try {
    const payload = {
      source_key: requestSourceKey,
      revision: requestRevision,
      parameters: parametersForRequest(contract, state.parameters),
    };
    if (requestCompositionId) payload.prompt_assistant_run_id = requestCompositionId;
    const generation = await api("/api/generations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const current = state.generations.find((item) => item.id === generation.id);
    state.generations = sortGenerationsNewestFirst([
      current || generation,
      ...state.generations.filter((item) => item.id !== generation.id),
    ]);
    if (
      sourceContextIsCurrent(requestSourceKey, requestRevision) &&
      state.compositionId === requestCompositionId
    ) {
      state.compositionId = null;
    }
    upsertGalleryCard(current || generation);
    toast("Generation queued.", "success");
    return true;
  } catch (error) {
    if (!sourceContextIsCurrent(requestSourceKey, requestRevision)) {
      toast(`Generation request for the previous source failed: ${error.message}`, "error");
      return false;
    }
    state.formError = error.message;
    state.serverFieldErrors = normalizeParameterErrors(error.fields);
    focusErrors = Object.keys(state.serverFieldErrors).length > 0;
    if (["source_republished", "source_unavailable"].includes(error.code)) {
      const message = error.message;
      await loadSources();
      if (state.activeSourceKey === requestSourceKey) state.formError = message;
    }
    return false;
  } finally {
    state.submitting = false;
    renderPanel();
    if (focusErrors) focusFirstInvalid();
  }
}

async function generateSelectedSources() {
  const contract = sourceInterface(state.activeSource);
  const requestSourceKey = state.activeSourceKey;
  const requestRevision = structuredClone(sourceRevision(state.activeSource));
  const requestCompositionId = state.compositionId;
  const requestParameters = structuredClone(state.parameters);
  const selectedKeys = selectedComparisonSourceKeys();
  selectedKeys.add(requestSourceKey);
  const sources = state.sources.filter(
    (source) => source.available !== false && selectedKeys.has(sourceKey(source)),
  );
  if (
    !requestSourceKey ||
    !state.activeSource ||
    !contract ||
    state.activeSource.available === false ||
    !sources.length
  )
    return false;

  const errors = clientValidate(contract, requestParameters);
  if (Object.keys(errors).length) {
    state.serverFieldErrors = errors;
    state.formError = "Review the highlighted comparison controls.";
    renderPanel();
    focusFirstInvalid();
    return false;
  }

  state.submitting = true;
  state.formError = null;
  state.serverFieldErrors = {};
  renderPanel();
  let focusErrors = false;
  try {
    const primaryParameters = parametersForRequest(contract, requestParameters);
    const validation = await api("/api/generations/validate", {
      method: "POST",
      body: JSON.stringify({
        source_key: requestSourceKey,
        revision: requestRevision,
        parameters: primaryParameters,
      }),
    });
    const seedInput = comparisonInputs(contract).find(
      (input) => input.semantic_role === "seed",
    );
    const resolvedSeed = seedInput ? validation.resolved_seeds?.[seedInput.id] : undefined;

    const detailResults = await Promise.allSettled(
      sources.map(async (summary) => {
        const key = sourceKey(summary);
        if (
          key === requestSourceKey &&
          sourceInterface(state.activeSource) &&
          revisionsMatch(summary, state.activeSource)
        ) {
          return { ...summary, ...state.activeSource };
        }
        return api(`/api/workflows/${encodeURIComponent(key)}`);
      }),
    );
    const queueTargets = [];
    const failures = [];
    for (let index = 0; index < detailResults.length; index += 1) {
      const result = detailResults[index];
      const summary = sources[index];
      if (result.status === "rejected") {
        failures.push({ source: summary, error: result.reason });
        continue;
      }
      const detail = result.value;
      const targetContract = sourceInterface(detail);
      if (!targetContract) {
        failures.push({ source: summary, error: new Error("No public interface is available.") });
        continue;
      }
      const targetKey = sourceKey(detail);
      const payload = {
        source_key: targetKey,
        revision: structuredClone(sourceRevision(detail)),
        parameters:
          targetKey === requestSourceKey
            ? primaryParameters
            : comparisonParametersForRequest(
                contract,
                requestParameters,
                targetContract,
                resolvedSeed,
              ),
      };
      if (payload.source_key === requestSourceKey && requestCompositionId) {
        payload.prompt_assistant_run_id = requestCompositionId;
      }
      queueTargets.push({ source: summary, payload });
    }

    const queueResults = await Promise.allSettled(
      queueTargets.map(({ payload }) =>
        api("/api/generations", { method: "POST", body: JSON.stringify(payload) }),
      ),
    );
    const queued = [];
    for (let index = 0; index < queueResults.length; index += 1) {
      const result = queueResults[index];
      if (result.status === "fulfilled") queued.push(result.value);
      else failures.push({ source: queueTargets[index].source, error: result.reason });
    }

    if (queued.length) {
      const queuedIds = new Set(queued.map((generation) => generation.id));
      const currentById = new Map(state.generations.map((generation) => [generation.id, generation]));
      state.generations = sortGenerationsNewestFirst([
        ...queued.map((generation) => currentById.get(generation.id) || generation),
        ...state.generations.filter((generation) => !queuedIds.has(generation.id)),
      ]);
      renderGallery();
    }
    if (
      queued.some((generation) => generation.source_key === requestSourceKey) &&
      sourceContextIsCurrent(requestSourceKey, requestRevision) &&
      state.compositionId === requestCompositionId
    ) {
      state.compositionId = null;
    }

    if (failures.length) {
      const failureSummary = failures
        .slice(0, 3)
        .map(({ source, error }) => `${source.display_name}: ${error.message}`)
        .join(" ");
      const omitted = failures.length > 3 ? ` ${failures.length - 3} more failed.` : "";
      state.formError = `Queued ${queued.length} of ${sources.length} selected generation sources. ${failureSummary}${omitted}`;
      const activeFailure = failures.find(({ source }) => sourceKey(source) === requestSourceKey);
      if (activeFailure) {
        state.serverFieldErrors = normalizeParameterErrors(activeFailure.error.fields);
        focusErrors = Object.keys(state.serverFieldErrors).length > 0;
      }
      toast(state.formError, "error");
    } else {
      toast(`${queued.length} generations queued across ${sources.length} selected sources.`, "success");
    }
    return queued.length > 0 && failures.length === 0;
  } catch (error) {
    if (!sourceContextIsCurrent(requestSourceKey, requestRevision)) {
      toast(`Comparison request for the previous source failed: ${error.message}`, "error");
      return false;
    }
    state.formError = error.message;
    state.serverFieldErrors = normalizeParameterErrors(error.fields);
    focusErrors = Object.keys(state.serverFieldErrors).length > 0;
    if (["source_republished", "source_unavailable"].includes(error.code)) {
      const message = error.message;
      await loadSources();
      if (state.activeSourceKey === requestSourceKey) state.formError = message;
    }
    return false;
  } finally {
    state.submitting = false;
    renderPanel();
    if (focusErrors) focusFirstInvalid();
  }
}

async function composePrompt(button, { automatic = false } = {}) {
  if (!state.promptAssistant.available) return false;
  const requestSourceKey = state.activeSourceKey;
  const requestRevision = structuredClone(sourceRevision(state.activeSource));
  const contract = sourceInterface(state.activeSource);
  const promptInput =
    positivePromptInput(contract) || interfaceInputs(contract).find((input) => input.id === "prompt.text");
  if (!requestSourceKey || !promptInput) return false;
  const requestMode = state.promptAssistant.mode;
  const requestPrompt = state.parameters[promptInput.id] || "";
  const requestDirection = state.promptAssistant.creativeDirection || "";
  promptCompositionRequests += 1;
  if (button) {
    button.disabled = true;
    button.textContent = "Applying…";
  }
  try {
    const result = await api("/api/prompt-assistant/compose", {
      method: "POST",
      body: JSON.stringify({
        mode: requestMode,
        prompt: requestPrompt,
        creative_direction: requestDirection,
      }),
    });
    if (
      !sourceContextIsCurrent(requestSourceKey, requestRevision) ||
      (automatic && !state.autoGenerate)
    ) {
      if (!automatic) {
        toast("Prompt composition finished after the source changed and was not applied.");
      }
      return false;
    }
    state.parameters[promptInput.id] = result.prompt;
    state.explicitParameterIds.add(promptInput.id);
    persistActiveParameterState();
    state.compositionId = result.composition_id;
    state.promptAssistant.historicalModel = result.model;
    renderPanel();
    if (!automatic) {
      const prompt = document.querySelector(`[data-control-id="${CSS.escape(promptInput.id)}"]`);
      prompt?.focus();
      toast("Creative direction applied to the editable Prompt field.", "success");
    }
    return true;
  } catch (error) {
    if (sourceContextIsCurrent(requestSourceKey, requestRevision)) {
      toast(
        automatic
          ? `Auto-generate could not apply Creative Direction: ${error.message}`
          : error.message || "Creative direction could not be applied.",
        "error",
      );
    } else {
      toast(`Prompt composition for the previous source failed: ${error.message}`, "error");
    }
    return false;
  } finally {
    promptCompositionRequests = Math.max(0, promptCompositionRequests - 1);
    if (button?.isConnected) {
      button.disabled = false;
      button.textContent = "Apply Creative Direction";
    }
    if (!automatic) scheduleAutoGenerate();
  }
}

async function composePromptEditor(button) {
  if (!state.promptAssistant.available) return;
  const dialog = button.closest("#prompt-editor-dialog[open]");
  const editor = dialog?.querySelector("[data-prompt-editor-input]");
  const direction = dialog?.querySelector("#prompt-editor-creative-direction");
  const checkedMode = dialog?.querySelector('[name="prompt-editor-assistant-mode"]:checked');
  const controlId = dialog?.dataset.promptControlId;
  const requestSourceKey = state.activeSourceKey;
  const requestRevision = structuredClone(sourceRevision(state.activeSource));
  if (!dialog || !editor || !direction || !checkedMode || !controlId || !requestSourceKey) return;

  const requestMode = checkedMode.value === "create" ? "create" : "refine";
  const requestPrompt = editor.value;
  const requestDirection = direction.value;
  button.disabled = true;
  button.textContent = "Applying…";
  try {
    const result = await api("/api/prompt-assistant/compose", {
      method: "POST",
      body: JSON.stringify({
        mode: requestMode,
        prompt: requestPrompt,
        creative_direction: requestDirection,
      }),
    });
    if (!sourceContextIsCurrent(requestSourceKey, requestRevision)) {
      toast("Prompt composition finished after the source changed and was not applied.");
      return;
    }
    if (!dialog.open || !button.isConnected || dialog.dataset.promptControlId !== controlId) {
      toast("Prompt composition finished after the focused editor closed and was not applied.");
      return;
    }
    editor.value = result.prompt;
    updatePromptEditorStats(result.prompt);
    dialog.dataset.promptAssistantCompositionId = result.composition_id;
    dialog.dataset.promptAssistantModel = result.model;
    editor.focus();
    toast("Creative direction applied in the focused editor. Apply to keep it.", "success");
  } catch (error) {
    if (dialog.open && button.isConnected && sourceContextIsCurrent(requestSourceKey, requestRevision)) {
      toast(error.message || "Creative direction could not be applied.", "error");
    } else {
      toast(`Focused prompt composition failed: ${error.message}`, "error");
    }
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.textContent = "Apply Creative Direction";
    }
  }
}

async function handleUpload(input) {
  const file = input.files?.[0];
  if (!file) return;
  const id = input.dataset.controlId;
  const control = interfaceInputs(sourceInterface(state.activeSource)).find((item) => item.id === id);
  if (control?.type === "image") return selectComputerImage(id, file, control);
  input.disabled = true;
  try {
    const result = await upload(`/api/uploads/${input.dataset.uploadKind}`, file);
    state.parameters[id] = result.id;
    state.explicitParameterIds.add(id);
    delete state.serverFieldErrors[id];
    persistActiveParameterState();
    renderPanel();
  } catch (error) {
    state.serverFieldErrors[id] = error.message;
    renderPanel();
  }
}

function handleDragStart(event) {
  const image = event.target.closest("[data-gallery-artifact-id]");
  if (!image || !event.dataTransfer) return;
  event.dataTransfer.effectAllowed = "copy";
  event.dataTransfer.setData(GALLERY_ARTIFACT_DRAG_TYPE, image.dataset.galleryArtifactId);
  image.classList.add("is-dragging");
}

function handleDragEnd(event) {
  event.target.closest("[data-gallery-artifact-id]")?.classList.remove("is-dragging");
  document
    .querySelectorAll(".image-input-dropzone.is-drag-over")
    .forEach((element) => element.classList.remove("is-drag-over"));
}

function imageDropzoneForEvent(event) {
  const zone = event.target.closest("[data-image-drop-control]");
  if (!zone || zone.getAttribute("aria-disabled") === "true") return null;
  return zone;
}

function transferHasImageCandidate(dataTransfer) {
  const types = Array.from(dataTransfer?.types || []);
  return types.includes("Files") || types.includes(GALLERY_ARTIFACT_DRAG_TYPE);
}

function handleDragEnter(event) {
  const zone = imageDropzoneForEvent(event);
  if (!zone || !transferHasImageCandidate(event.dataTransfer)) return;
  event.preventDefault();
  zone.classList.add("is-drag-over");
}

function handleDragOver(event) {
  const zone = imageDropzoneForEvent(event);
  if (!zone || !transferHasImageCandidate(event.dataTransfer)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
  zone.classList.add("is-drag-over");
}

function handleDragLeave(event) {
  const zone = imageDropzoneForEvent(event);
  if (!zone || zone.contains(event.relatedTarget)) return;
  zone.classList.remove("is-drag-over");
}

async function handleDrop(event) {
  const zone = imageDropzoneForEvent(event);
  if (!zone || !transferHasImageCandidate(event.dataTransfer)) return;
  event.preventDefault();
  zone.classList.remove("is-drag-over");
  const controlId = zone.dataset.imageDropControl;
  const control = interfaceInputs(sourceInterface(state.activeSource)).find(
    (item) => item.id === controlId && item.type === "image",
  );
  if (!control) return;
  const file = event.dataTransfer.files?.[0];
  if (file) {
    await selectComputerImage(controlId, file, control);
    return;
  }
  const artifactId = event.dataTransfer.getData(GALLERY_ARTIFACT_DRAG_TYPE);
  if (artifactId) await selectGalleryImage(controlId, artifactId, control);
}

async function selectComputerImage(controlId, file, control) {
  state.imageUploadsPending += 1;
  delete state.serverFieldErrors[controlId];
  renderPanel();
  try {
    await validateBrowserImage(file, control.media || {});
    const result = await upload("/api/uploads/reference-images", file);
    setImageSelection(controlId, result, file.name || "Uploaded image");
  } catch (error) {
    state.serverFieldErrors[controlId] = error.message || "Image upload failed.";
  } finally {
    state.imageUploadsPending = Math.max(0, state.imageUploadsPending - 1);
    renderPanel();
  }
}

async function selectGalleryImage(controlId, artifactId, control) {
  state.imageUploadsPending += 1;
  delete state.serverFieldErrors[controlId];
  renderPanel();
  try {
    const result = await api(
      `/api/uploads/reference-images/from-artifact/${encodeURIComponent(artifactId)}`,
      { method: "POST" },
    );
    validateImageMetadata(result, control.media || {});
    setImageSelection(controlId, result, "Gallery image");
  } catch (error) {
    state.serverFieldErrors[controlId] = error.message || "Gallery image could not be selected.";
  } finally {
    state.imageUploadsPending = Math.max(0, state.imageUploadsPending - 1);
    renderPanel();
  }
}

function setImageSelection(controlId, result, name) {
  state.parameters[controlId] = {
    asset_id: result.id,
    preview_url: result.preview_url,
    mime_type: result.mime_type,
    bytes: result.byte_size,
    width: result.width,
    height: result.height,
    sha256: result.sha256,
    name,
  };
  state.explicitParameterIds.add(controlId);
  delete state.serverFieldErrors[controlId];
  persistActiveParameterState();
}

async function validateBrowserImage(file, media) {
  const accepted = Array.isArray(media.accepted_mime_types) ? media.accepted_mime_types : [];
  if (file.type && accepted.length && !accepted.includes(file.type)) {
    throw new Error("Choose a PNG, JPEG, or WebP image accepted by this source.");
  }
  if (Number.isFinite(Number(media.max_bytes)) && file.size > Number(media.max_bytes)) {
    throw new Error("Image exceeds this source's byte limit.");
  }
  if (typeof createImageBitmap !== "function") return;
  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error("The selected file is not a decodable image.");
  }
  try {
    validateImageMetadata({ width: bitmap.width, height: bitmap.height }, media);
  } finally {
    bitmap.close();
  }
}

function validateImageMetadata(image, media) {
  if (Number.isFinite(Number(media.max_width)) && image.width > Number(media.max_width)) {
    throw new Error("Image exceeds this source's maximum width.");
  }
  if (Number.isFinite(Number(media.max_height)) && image.height > Number(media.max_height)) {
    throw new Error("Image exceeds this source's maximum height.");
  }
  if (
    image.mime_type &&
    Array.isArray(media.accepted_mime_types) &&
    !media.accepted_mime_types.includes(image.mime_type)
  ) {
    throw new Error("Choose a PNG, JPEG, or WebP image accepted by this source.");
  }
  if (image.byte_size && Number.isFinite(Number(media.max_bytes)) && image.byte_size > media.max_bytes) {
    throw new Error("Image exceeds this source's byte limit.");
  }
}

function renderGallery() {
  const gallery = document.querySelector("#gallery");
  if (!gallery) return;
  state.generations = sortGenerationsNewestFirst(state.generations);
  gallery.innerHTML = galleryMarkup(state.generations, {
    status: state.galleryStatus,
    message: state.galleryMessage,
  });
  const sentinel = document.querySelector("#gallery-sentinel");
  if (sentinel) sentinel.hidden = !state.nextCursor;
}

function upsertGalleryCard(generation) {
  const gallery = document.querySelector("#gallery");
  if (!gallery) return;
  const empty = gallery.querySelector(".empty-gallery");
  empty?.remove();
  const existing = gallery.querySelector(`[data-generation-id="${CSS.escape(generation.id)}"]`);
  if (existing) {
    existing.outerHTML = galleryCardMarkup(generation);
  } else {
    const index = state.generations.findIndex((item) => item.id === generation.id);
    const nextGeneration = index >= 0 ? state.generations[index + 1] : null;
    const nextCard = nextGeneration
      ? gallery.querySelector(`[data-generation-id="${CSS.escape(nextGeneration.id)}"]`)
      : null;
    if (nextCard) nextCard.insertAdjacentHTML("beforebegin", galleryCardMarkup(generation));
    else gallery.insertAdjacentHTML("beforeend", galleryCardMarkup(generation));
  }
}

async function loadMore() {
  if (!state.nextCursor || state.loadingMore) return;
  state.loadingMore = true;
  try {
    const page = await api(`/api/generations?limit=24&cursor=${encodeURIComponent(state.nextCursor)}`);
    const known = new Set(state.generations.map((item) => item.id));
    for (const item of page.items) {
      if (!known.has(item.id)) state.generations.push(item);
    }
    state.generations = sortGenerationsNewestFirst(state.generations);
    state.nextCursor = page.next_cursor;
    renderGallery();
    setupPaginationObserver();
  } finally {
    state.loadingMore = false;
  }
}

function setupPaginationObserver() {
  state.observer?.disconnect();
  const sentinel = document.querySelector("#gallery-sentinel");
  if (!sentinel || !state.nextCursor || !("IntersectionObserver" in window)) return;
  state.observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore().catch(() => {});
    },
    { root: document.querySelector("#gallery-viewport"), rootMargin: "600px" },
  );
  state.observer.observe(sentinel);
}

async function refreshGeneration(id, { insertIf = () => true } = {}) {
  const refreshToken = generationRefreshGate.issue(id);
  try {
    const detail = await api(`/api/generations/${id}`);
    if (!generationRefreshGate.isCurrent(id, refreshToken)) return;
    const index = state.generations.findIndex((item) => item.id === id);
    const previous = index >= 0 ? state.generations[index] : null;
    const inserted = index < 0;
    const becameViewable =
      index >= 0 &&
      state.generations[index].display_artifact?.kind !== "image" &&
      detail.display_artifact?.kind === "image";
    if (index >= 0) state.generations[index] = detail;
    else if (insertIf(detail)) state.generations.unshift(detail);
    else return;
    state.generations = sortGenerationsNewestFirst(state.generations);
    upsertGalleryCard(detail);
    scheduleAutoGenerate();
    const dialog = document.querySelector("#detail-dialog");
    if (dialog?.open && dialog.dataset.generationId === id) dialog.innerHTML = detailMarkup(detail);
    const completedForSlideshow =
      detail.status === "succeeded" && previous?.status !== "succeeded";
    if (
      completedForSlideshow &&
      showLatestCompletedSlideshowGeneration({ completedGenerationId: id })
    ) {
      return;
    }
    if (
      state.photoViewerGenerationId &&
      state.photoViewerPlaybackMode === "hold" &&
      (state.photoViewerGenerationId === id || inserted || becameViewable)
    ) {
      renderPhotoViewer();
    }
  } catch (error) {
    if (!generationRefreshGate.isCurrent(id, refreshToken)) return;
    if (error.status === 404) removeGeneration(id);
  }
}

async function recall(id) {
  const recalled = await api(`/api/generations/${id}/recall`);
  if (!recalled.available) {
    toast(recalled.reason || "Exact recall is unavailable.", "error");
    return;
  }
  const recalledState = overwriteWithRecall(state, recalled, sourceInterface(state.activeSource));
  if (recalled.source_available === false) {
    state.parameters = recalledState.parameters;
    state.explicitParameterIds = recalledState.explicitParameterIds;
    state.promptAssistant = recalledState.promptAssistant;
    state.compositionId = null;
    state.serverFieldErrors = {};
    state.formError = null;
    state.selectedPreset = null;
    persistActiveParameterState();
    renderPanel();
    closePanel(false);
    document.querySelector("#generation-panel")?.scrollIntoView({ block: "start" });
    toast(recalled.reason || "Historical settings loaded into the current source.", "warning");
    return;
  }
  const key = recalled.source_key || recalled.profile_id;
  const source = await api(`/api/workflows/${encodeURIComponent(key)}`);
  const contract = sourceInterface(source);
  state.activeSourceKey = key;
  state.activeSource = { ...source, interface: contract };
  state.comparisonSourceKeys = new Set();
  state.sourcePickerDialogOpen = false;
  state.sourcePickerDraft = null;
  state.pendingSourceMigration = null;
  state.explicitParameterIds = new Set(
    Object.entries(recalledState.parameters || {})
      .filter(([, value]) => value !== null && value !== undefined)
      .map(([id]) => id),
  );
  state.parameters = reconcileInterfaceValues(
    contract,
    recalledState.parameters,
    null,
    state.explicitParameterIds,
  );
  state.promptAssistant = recalledState.promptAssistant;
  state.compositionId = null;
  state.serverFieldErrors = {};
  state.formError = null;
  state.selectedPreset = null;
  persistActiveParameterState();
  renderPanel();
  closePanel(false);
  document.querySelector("#generation-panel")?.scrollIntoView({ block: "start" });
  toast("Exact historical settings loaded. Press Generate when ready.", "success");
}

async function recallFavorite(id) {
  document.querySelector("#favorites-dialog")?.close();
  await recall(id);
}

async function toggleFavorite(id, button) {
  const generation = state.generations.find((item) => item.id === id);
  if (!generation) return;
  const wasFavorite = Boolean(generation.is_favorite);
  button.disabled = true;
  try {
    if (wasFavorite) {
      await api(`/api/generations/${encodeURIComponent(id)}/favorite`, { method: "DELETE" });
      setGenerationFavorite(id, false);
      state.favorites = state.favorites.filter((item) => item.generation.id !== id);
      if (document.querySelector("#favorites-dialog")?.open) renderFavoritesDialog();
      toast("Removed from Favorites.", "success");
    } else {
      const favorite = await api(`/api/generations/${encodeURIComponent(id)}/favorite`, {
        method: "PUT",
      });
      setGenerationFavorite(id, true, favorite.generation);
      state.favorites = [
        favorite,
        ...state.favorites.filter((item) => item.generation.id !== id),
      ];
      if (document.querySelector("#favorites-dialog")?.open) renderFavoritesDialog();
      toast("Added to Favorites.", "success");
    }
  } finally {
    if (button.isConnected) button.disabled = false;
  }
}

function setGenerationFavorite(id, isFavorite, summary = null) {
  const index = state.generations.findIndex((item) => item.id === id);
  if (index < 0) return;
  state.generations[index] = {
    ...state.generations[index],
    ...(summary || {}),
    is_favorite: isFavorite,
  };
  upsertGalleryCard(state.generations[index]);
}

async function openFavorites() {
  const page = await api("/api/favorites?limit=40");
  state.favorites = page.items;
  state.favoritesNextCursor = page.next_cursor;
  renderFavoritesDialog();
  const dialog = document.querySelector("#favorites-dialog");
  if (!dialog.open) dialog.showModal();
}

function renderFavoritesDialog() {
  const dialog = document.querySelector("#favorites-dialog");
  if (!dialog) return;
  dialog.innerHTML = favoritesMarkup(state.favorites, state.favoritesNextCursor);
}

async function loadMoreFavorites() {
  if (!state.favoritesNextCursor || state.loadingMoreFavorites) return;
  state.loadingMoreFavorites = true;
  try {
    const page = await api(
      `/api/favorites?limit=40&cursor=${encodeURIComponent(state.favoritesNextCursor)}`,
    );
    const known = new Set(state.favorites.map((item) => item.id));
    state.favorites.push(...page.items.filter((item) => !known.has(item.id)));
    state.favoritesNextCursor = page.next_cursor;
    renderFavoritesDialog();
  } finally {
    state.loadingMoreFavorites = false;
  }
}

async function deleteFavorite(id) {
  if (!window.confirm("Remove this generation from Favorites? It will remain in your generation history.")) return;
  await api(`/api/generations/${encodeURIComponent(id)}/favorite`, { method: "DELETE" });
  state.favorites = state.favorites.filter((item) => item.generation.id !== id);
  setGenerationFavorite(id, false);
  renderFavoritesDialog();
  toast("Favorite deleted. Generation history was preserved.", "success");
}

async function openDetail(id) {
  const detail = await api(`/api/generations/${id}`);
  const dialog = document.querySelector("#detail-dialog");
  dialog.dataset.generationId = id;
  dialog.innerHTML = detailMarkup(detail);
  dialog.showModal();
}

function photoViewerGenerations() {
  return state.generations.filter((generation) => generation.display_artifact?.kind === "image");
}

function photoViewerNavigation(id) {
  const generations = photoViewerGenerations();
  const index = generations.findIndex((generation) => generation.id === id);
  return {
    hasOlder: index >= 0 && (index < generations.length - 1 || Boolean(state.nextCursor)),
    hasNewer: index > 0,
  };
}

function renderPhotoViewer() {
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog || !state.photoViewerGenerationId) return;
  if (activePhotoViewerDrag) {
    activePhotoViewerDrag.renderPending = true;
    return;
  }
  const generation = state.generations.find((item) => item.id === state.photoViewerGenerationId);
  if (!generation?.display_artifact || generation.display_artifact.kind !== "image") {
    closePhotoViewer();
    return;
  }
  const host = dialog.querySelector(".photo-viewer-host");
  if (!host) return;
  host.innerHTML = photoViewerMarkup(
    generation,
    photoViewerNavigation(generation.id),
    state.photoViewerMode,
    state.photoViewerPlaybackMode,
  );
  preparePhotoViewerImage();
  updatePhotoViewerFullscreenControl();
}

function openPhotoViewer(id) {
  const generation = state.generations.find((item) => item.id === id);
  if (!generation?.display_artifact || generation.display_artifact.kind !== "image") return;
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog) return;
  state.photoViewerPlaybackMode = "hold";
  resetPhotoViewerView();
  state.photoViewerGenerationId = id;
  if (!dialog.open) dialog.showModal();
  renderPhotoViewer();
  notePhotoViewerActivity();
  dialog.querySelector("[data-action=close-photo]")?.focus({ preventScroll: true });
}

async function navigatePhotoViewer(direction) {
  if (!state.photoViewerGenerationId || !["older", "newer"].includes(direction)) return;
  if (state.photoViewerPlaybackMode === "slideshow") {
    state.photoViewerPlaybackMode = "hold";
  }
  let generations = photoViewerGenerations();
  let index = generations.findIndex((generation) => generation.id === state.photoViewerGenerationId);
  let target = generations[index + (direction === "older" ? 1 : -1)];
  while (!target && direction === "older" && state.nextCursor) {
    await loadMore();
    generations = photoViewerGenerations();
    index = generations.findIndex((generation) => generation.id === state.photoViewerGenerationId);
    target = generations[index + 1];
  }
  if (!target) {
    renderPhotoViewer();
    return;
  }
  state.photoViewerGenerationId = target.id;
  resetPhotoViewerView(state.photoViewerMode);
  renderPhotoViewer();
  notePhotoViewerActivity();
}

function togglePhotoViewerMode() {
  setPhotoViewerMode(state.photoViewerMode === "fill" ? "fit" : "fill");
}

function setPhotoViewerMode(mode) {
  if (!["actual", "fit", "fill"].includes(mode)) return;
  resetPhotoViewerView(mode);
  const dialog = document.querySelector("#photo-viewer");
  const media = dialog?.querySelector(".photo-viewer-media");
  if (media) media.dataset.photoViewMode = mode;
  updatePhotoViewerModeControl();
  layoutPhotoViewerImage();
}

function togglePhotoViewerPlaybackMode() {
  setPhotoViewerPlaybackMode(
    state.photoViewerPlaybackMode === "slideshow" ? "hold" : "slideshow",
  );
}

function setPhotoViewerPlaybackMode(mode) {
  if (!["hold", "slideshow"].includes(mode)) return;
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog?.open) return;
  state.photoViewerPlaybackMode = mode;
  if (mode === "hold") {
    updatePhotoViewerPlaybackControl();
    return;
  }

  if (showLatestCompletedSlideshowGeneration({ force: true })) return;
  updatePhotoViewerPlaybackControl();
}

function showLatestCompletedSlideshowGeneration({
  force = false,
  completedGenerationId = null,
} = {}) {
  if (
    state.photoViewerPlaybackMode !== "slideshow" ||
    !state.photoViewerGenerationId
  ) {
    return false;
  }
  const latest = latestCompletedImageGeneration(state.generations);
  if (!latest) return false;
  const currentIsNewlyComplete =
    latest.id === state.photoViewerGenerationId && latest.id === completedGenerationId;
  if (!force && latest.id === state.photoViewerGenerationId && !currentIsNewlyComplete) {
    return false;
  }
  state.photoViewerGenerationId = latest.id;
  resetPhotoViewerView(state.photoViewerMode);
  renderPhotoViewer();
  return true;
}

function preparePhotoViewerImage() {
  const photo = document.querySelector("#photo-viewer .photo-viewer-media img");
  if (!photo) return;
  photo.addEventListener("load", layoutPhotoViewerImage, { once: true });
  if (photo.complete) layoutPhotoViewerImage();
}

function layoutPhotoViewerImage() {
  const photo = document.querySelector("#photo-viewer .photo-viewer-media img");
  const media = photo?.closest(".photo-viewer-media");
  if (!photo || !media || !photo.naturalWidth || !photo.naturalHeight) return;
  const layout = photoViewerImageLayout(
    photo.naturalWidth,
    photo.naturalHeight,
    media.clientWidth,
    media.clientHeight,
  );
  if (!layout) return;
  photo.style.width = `${layout.width}px`;
  photo.style.height = `${layout.height}px`;
  if (state.photoViewerNeedsBaseZoom) {
    state.photoViewerZoom =
      state.photoViewerMode === "fill"
        ? layout.fillZoom
        : state.photoViewerMode === "actual"
          ? layout.oneToOneZoom
          : 1;
    state.photoViewerPanX = 0;
    state.photoViewerPanY = state.photoViewerMode === "fill" ? layout.fillPanY : 0;
    state.photoViewerNeedsBaseZoom = false;
  }
  applyPhotoViewerTransform();
}

function applyPhotoViewerTransform() {
  const photo = document.querySelector("#photo-viewer .photo-viewer-media img");
  if (!photo) return;
  photo.style.transform = `translate3d(${state.photoViewerPanX}px, ${state.photoViewerPanY}px, 0) scale(${state.photoViewerZoom})`;
  photo.dataset.photoZoom = String(state.photoViewerZoom);
  photo.dataset.photoPanX = String(state.photoViewerPanX);
  photo.dataset.photoPanY = String(state.photoViewerPanY);
}

function resetPhotoViewerView(mode = "fill") {
  state.photoViewerMode = ["actual", "fit"].includes(mode) ? mode : "fill";
  state.photoViewerZoom = 1;
  state.photoViewerPanX = 0;
  state.photoViewerPanY = 0;
  state.photoViewerNeedsBaseZoom = true;
  finishPhotoViewerDrag(false);
  applyPhotoViewerTransform();
}

function finishPhotoViewerDrag(renderPending = true) {
  if (!activePhotoViewerDrag) return;
  const { captureTarget, pointerId, renderPending: shouldRender } = activePhotoViewerDrag;
  activePhotoViewerDrag = null;
  document.querySelector("#photo-viewer")?.classList.remove("is-panning");
  try {
    if (captureTarget.hasPointerCapture(pointerId)) captureTarget.releasePointerCapture(pointerId);
  } catch {
    // The browser may release capture before pointercancel reaches the delegated handler.
  }
  if (renderPending && shouldRender) renderPhotoViewer();
}

function requestPhotoViewerFullscreen(dialog) {
  const target = dialog.querySelector(".photo-viewer-host");
  if (!target) return;
  if (document.fullscreenElement || typeof target.requestFullscreen !== "function") return;
  const requestToken = ++state.photoViewerFullscreenRequestToken;
  state.photoViewerFullscreenPending = true;
  target.requestFullscreen({ navigationUI: "hide" }).then(
    () => {
      if (state.photoViewerFullscreenRequestToken !== requestToken || !dialog.open) {
        if (document.fullscreenElement === target) document.exitFullscreen().catch(() => {});
        return;
      }
      state.photoViewerFullscreenPending = false;
      state.photoViewerFullscreenOwned = document.fullscreenElement === target;
    },
    () => {
      if (state.photoViewerFullscreenRequestToken === requestToken) {
        state.photoViewerFullscreenPending = false;
      }
    },
  );
}

function togglePhotoViewerFullscreen() {
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog?.open) return;
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
    return;
  }
  requestPhotoViewerFullscreen(dialog);
}

function handlePhotoViewerFullscreenChange() {
  const dialog = document.querySelector("#photo-viewer");
  if (document.fullscreenElement) {
    if (state.photoViewerFullscreenPending && dialog?.open) state.photoViewerFullscreenOwned = true;
  } else {
    state.photoViewerFullscreenOwned = false;
    state.photoViewerFullscreenPending = false;
  }
  updatePhotoViewerFullscreenControl();
  state.photoViewerNeedsBaseZoom = true;
  window.requestAnimationFrame(layoutPhotoViewerImage);
}

function updatePhotoViewerFullscreenControl() {
  const button = document.querySelector("#photo-viewer [data-action=toggle-photo-fullscreen]");
  if (!button) return;
  const active = Boolean(document.fullscreenElement);
  button.textContent = active ? "Exit full screen" : "Full screen";
  button.setAttribute("aria-pressed", String(active));
}

function updatePhotoViewerModeControl() {
  const control = document.querySelector("#photo-viewer .photo-viewer-view-controls");
  const modeToggle = control?.querySelector(".photo-viewer-mode");
  const toggle = modeToggle?.querySelector("[data-action=toggle-photo-view]");
  if (!control || !modeToggle || !toggle) return;
  modeToggle.dataset.photoToggleState = state.photoViewerMode;
  toggle.setAttribute("aria-checked", String(state.photoViewerMode === "fill"));
  for (const label of control.querySelectorAll("[data-action=set-photo-view]")) {
    label.setAttribute("aria-pressed", String(label.dataset.photoViewMode === state.photoViewerMode));
  }
}

function updatePhotoViewerPlaybackControl() {
  const control = document.querySelector("#photo-viewer .photo-viewer-slideshow");
  const toggle = control?.querySelector("[data-action=toggle-photo-slideshow]");
  if (!control || !toggle) return;
  control.dataset.photoToggleState = state.photoViewerPlaybackMode;
  toggle.setAttribute(
    "aria-checked",
    String(state.photoViewerPlaybackMode === "slideshow"),
  );
  for (const label of control.querySelectorAll("[data-action=set-photo-playback]")) {
    label.setAttribute(
      "aria-pressed",
      String(label.dataset.photoPlaybackMode === state.photoViewerPlaybackMode),
    );
  }
}

function handlePhotoViewerResize() {
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog?.open) return;
  state.photoViewerNeedsBaseZoom = true;
  window.requestAnimationFrame(layoutPhotoViewerImage);
}

function notePhotoViewerActivity() {
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog?.open) return;
  dialog.classList.add("controls-visible");
  if (state.photoViewerTimer) window.clearTimeout(state.photoViewerTimer);
  state.photoViewerTimer = window.setTimeout(() => {
    dialog.classList.remove("controls-visible");
    state.photoViewerTimer = null;
  }, 2000);
}

function closePhotoViewer() {
  const dialog = document.querySelector("#photo-viewer");
  const shouldExitFullscreen = state.photoViewerFullscreenOwned && Boolean(document.fullscreenElement);
  if (dialog?.open) dialog.close();
  else resetPhotoViewerState();
  if (shouldExitFullscreen) document.exitFullscreen().catch(() => {});
}

function resetPhotoViewerState() {
  if (state.photoViewerTimer) window.clearTimeout(state.photoViewerTimer);
  state.photoViewerTimer = null;
  state.photoViewerGenerationId = null;
  state.photoViewerPlaybackMode = "hold";
  state.photoViewerFullscreenOwned = false;
  state.photoViewerFullscreenPending = false;
  state.photoViewerFullscreenRequestToken += 1;
  resetPhotoViewerView();
  document.querySelector("#photo-viewer")?.classList.remove("controls-visible");
}

async function cancelGeneration(id, button) {
  const buttonLabel = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "Stopping…";
  }
  try {
    const result = await api(`/api/generations/${id}/cancel`, { method: "POST" });
    if (result === null) {
      removeGeneration(id);
      const detailDialog = document.querySelector("#detail-dialog");
      if (detailDialog?.dataset.generationId === id) detailDialog.close();
      toast("Queued generation cancelled and removed.", "success");
      return;
    }
    await refreshGeneration(id);
    toast(result.status === "cancel_requested" ? "Cancellation requested." : "Generation cancelled.", "success");
  } catch (error) {
    if (button?.isConnected) {
      button.disabled = false;
      button.textContent = buttonLabel || "Cancel";
    }
    throw error;
  }
}

async function deleteGeneration(id) {
  if (
    !window.confirm(
      "Permanently delete this generation record and all of its application-owned artifacts? It will disappear from your history and cannot be undone.",
    )
  ) {
    return;
  }
  const response = await fetch(`/api/generations/${encodeURIComponent(id)}`, {
    method: "DELETE",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": state.session.csrf_token },
  });
  if (![202, 204].includes(response.status)) {
    const payload = await response.json();
    throw new Error(payload.error?.message || "Deletion failed.");
  }
  if (response.status === 204) {
    removeGeneration(id);
    document.querySelector("#detail-dialog")?.close();
    toast("Generation deleted.", "success");
  } else {
    await refreshGeneration(id);
    toast("Cancellation and deletion are being reconciled.", "success");
  }
}

function removeGeneration(id) {
  generationRefreshGate.invalidate(id);
  const closesPhotoViewer = state.photoViewerGenerationId === id;
  if (closesPhotoViewer) closePhotoViewer();
  state.generations = state.generations.filter((item) => item.id !== id);
  state.favorites = state.favorites.filter((item) => item.generation.id !== id);
  document.querySelector(`[data-generation-id="${CSS.escape(id)}"]`)?.remove();
  if (document.querySelector("#favorites-dialog")?.open) renderFavoritesDialog();
  if (!state.generations.length) renderGallery();
  else if (state.photoViewerGenerationId && !closesPhotoViewer) renderPhotoViewer();
  scheduleAutoGenerate();
}

function startLiveUpdates({ paused = false } = {}) {
  state.eventSource?.close();
  state.liveUpdatesPaused = paused;
  state.pendingLiveUpdates = [];
  const source = new EventSource(`/api/events?last_event_id=${state.lastEventId}`);
  const eventTypes = [
    "generation.queued",
    "generation.dispatching",
    "generation.running",
    "generation.stage",
    "generation.progress",
    "artifact.available",
    "artifact.persistence_failed",
    "generation.cancel_requested",
    "generation.cancelled",
    "generation.error",
    "generation.terminal",
    "generation.requeued",
    "generation.deleted",
  ];
  for (const type of eventTypes) {
    source.addEventListener(type, (event) => {
      if (state.eventSource !== source) return;
      const payload = JSON.parse(event.data);
      if (event.lastEventId) state.lastEventId = Math.max(state.lastEventId, Number(event.lastEventId));
      const update = { type, payload };
      if (state.liveUpdatesPaused) state.pendingLiveUpdates.push(update);
      else applyLiveUpdate(update);
    });
  }
  source.onerror = () => {};
  state.eventSource = source;
}

function applyLiveUpdate({ type, payload }) {
  if (type === "generation.deleted") removeGeneration(payload.generation_id);
  else if (payload.generation_id) refreshGeneration(payload.generation_id).catch(() => {});
}

function resumeLiveUpdates() {
  if (!state.liveUpdatesPaused) return;
  const pending = state.pendingLiveUpdates;
  state.pendingLiveUpdates = [];
  state.liveUpdatesPaused = false;
  const boundary = startupGalleryBoundary;
  startupGalleryBoundary = null;
  const snapshotFailed = state.galleryStatus === "error";
  if (!boundary && !snapshotFailed) return;
  const visibleGenerations = new Map(state.generations.map((item) => [item.id, item]));
  const latestByGeneration = new Map();
  for (const update of pending) {
    const generationId = update.payload.generation_id;
    if (!generationId) continue;
    latestByGeneration.delete(generationId);
    latestByGeneration.set(generationId, update);
  }
  for (const update of latestByGeneration.values()) {
    const generationId = update.payload.generation_id;
    if (update.type === "generation.deleted") {
      if (visibleGenerations.has(generationId)) applyLiveUpdate(update);
      continue;
    }
    const visible = visibleGenerations.get(generationId);
    if (visible) {
      const alreadyTerminal = TERMINAL_GENERATION_STATUSES.has(visible.status);
      if (!alreadyTerminal || update.type !== "generation.terminal") applyLiveUpdate(update);
      continue;
    }
    refreshGeneration(generationId, {
      insertIf: boundary
        ? (detail) => generationPrecedesBoundary(detail, boundary.oldest)
        : () => true,
    }).catch(() => {});
  }
}

function generationPrecedesBoundary(generation, boundary) {
  if (!boundary) return true;
  return sortGenerationsNewestFirst([generation, boundary])[0]?.id === generation.id;
}

function startServicePolling() {
  stopServicePolling();
  const controller = new AbortController();
  servicePollingController = controller;
  scheduleServicePoll(controller);
}

function scheduleServicePoll(controller) {
  if (servicePollingController !== controller || controller.signal.aborted) return;
  state.serviceTimer = window.setTimeout(async () => {
    state.serviceTimer = null;
    try {
      await refreshServices(controller.signal);
    } finally {
      scheduleServicePoll(controller);
    }
  }, SERVICE_POLL_INTERVAL_MS);
}

function stopServicePolling() {
  servicePollingController?.abort();
  servicePollingController = null;
  if (state.serviceTimer !== null) window.clearTimeout(state.serviceTimer);
  state.serviceTimer = null;
}

function stopLiveUpdates() {
  discardSpeechSession();
  state.eventSource?.close();
  state.eventSource = null;
  state.liveUpdatesPaused = false;
  state.pendingLiveUpdates = [];
  startupGalleryBoundary = null;
  generationRefreshGate.clear();
  stopServicePolling();
  state.observer?.disconnect();
  closePhotoViewer();
}

async function refreshServices(signal) {
  const previousPanelState = servicePanelState();
  const previousComfy = state.services.find((item) => item.service === "comfyui")?.available;
  try {
    state.services = await api("/api/services", {
      operation: "Service status",
      deadlineMs: STARTUP_DEADLINES.services,
      signal,
    });
    state.servicesStatus = "ready";
    state.servicesMessage = null;
    const currentComfy = state.services.find((item) => item.service === "comfyui")?.available;
    renderServiceBanner();
    if (previousPanelState !== servicePanelState()) {
      // Keep the source catalog stable while the user reviews a transactional draft.
      if (state.sourcePickerDialogOpen) state.servicePanelRefreshPending = true;
      else renderPanel();
    }
    if (previousComfy !== currentComfy) {
      if (state.sourcePickerDialogOpen) state.sourceCatalogRefreshPending = true;
      else await loadSources({ signal });
    }
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    state.servicesStatus = "error";
    state.servicesMessage = error.message || "Service status is temporarily unavailable.";
    renderServiceBanner();
    if (previousPanelState !== servicePanelState()) {
      if (state.sourcePickerDialogOpen) state.servicePanelRefreshPending = true;
      else renderPanel();
    }
    // Session expiry is handled by normal API interaction; avoid disruptive polling errors.
  }
}

function servicePanelState() {
  const services = [...(state.services || [])]
    .map((item) => ({
      service: item.service,
      available: Boolean(item.available),
      message: item.message || null,
    }))
    .sort((first, second) => String(first.service).localeCompare(String(second.service)));
  return JSON.stringify({
    status: state.servicesStatus,
    message: state.servicesStatus === "error" ? state.servicesMessage : null,
    services,
  });
}

function renderServiceBanner() {
  const banner = document.querySelector("#service-banner");
  if (banner) {
    banner.innerHTML = serviceBannerMarkup(
      state.services,
      state.servicesStatus,
      state.servicesMessage,
    );
  }
}

function updateGalleryScale(value, persist) {
  state.galleryScale = Number(value);
  applyGalleryScale();
  if (persist) {
    window.clearTimeout(state.scaleTimer);
    state.scaleTimer = window.setTimeout(async () => {
      try {
        await api("/api/preferences", {
          method: "PUT",
          body: JSON.stringify({ gallery_scale: state.galleryScale }),
        });
      } catch {
        toast("Gallery scale could not be saved.", "error");
      }
    }, 250);
  }
}

function applyGalleryScale() {
  const gallery = document.querySelector("#gallery");
  if (!gallery) return;
  const layout = scaleToLayout(state.galleryScale);
  gallery.style.setProperty("--gallery-card-min", `${layout.cardWidth}px`);
  gallery.classList.toggle("gallery-full", layout.full);
  const input = document.querySelector("#gallery-scale");
  if (input) {
    input.value = String(state.galleryScale);
    input.setAttribute("aria-valuetext", `${state.galleryScale}%`);
  }
}

async function openAdmin() {
  const [users, diagnostics] = await Promise.all([
    api("/api/admin/users"),
    api("/api/admin/workflows/diagnostics"),
  ]);
  const dialog = document.querySelector("#admin-dialog");
  dialog.innerHTML = adminMarkup(users, diagnostics);
  if (!dialog.open) dialog.showModal();
}

function adminMarkup(users, diagnostics) {
  const ordinary = users.filter((item) => item.role === "user");
  return `<div class="dialog-frame admin-frame">
    <header class="dialog-header"><div><h2>Administration</h2><p>Manage accounts and published-source discovery.</p></div><button type="button" class="icon-button" data-action="close-admin" aria-label="Close administration">×</button></header>
    <div class="admin-content">
      <section><h3>Users</h3><form id="create-user-form" class="inline-form"><label class="field"><span>Username</span><input name="username" required /></label><label class="field"><span>Temporary password</span><input name="temporary_password" type="password" minlength="8" required /></label><button class="button primary" type="submit">Create user</button></form>
      <div class="table-wrap"><table><thead><tr><th>Username</th><th>State</th><th>Created</th><th>Account actions</th></tr></thead><tbody>${ordinary.map((user) => `<tr><td>${escapeForAdmin(user.username)}</td><td>${user.must_change_password ? "Temporary password" : "Active"}</td><td>${new Date(user.created_at).toLocaleDateString()}</td><td><div class="button-row"><button type="button" class="button low" data-action="reset-user-password" data-user-id="${user.id}">Reset password</button><button type="button" class="button destructive low" data-action="delete-user" data-user-id="${user.id}" data-username="${escapeForAdmin(user.username)}">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="4">No ordinary users.</td></tr>'}</tbody></table></div></section>
      <section><div class="section-heading"><h3>Published-source diagnostics</h3><button type="button" class="button secondary" data-action="refresh-workflows">Refresh discovery</button></div><div class="diagnostic-list">${diagnostics.map((item) => `<article class="diagnostic ${item.accepted ? "accepted" : "rejected"}"><strong>${escapeForAdmin(item.display_name || item.basename || item.source_key || "Published source")}</strong><span>${item.accepted ? "Accepted" : "Rejected"}</span><p>${escapeForAdmin(item.message)}</p><code>${escapeForAdmin(item.code)}</code></article>`).join("") || '<p class="muted">No discovery diagnostics yet.</p>'}</div></section>
    </div>
    <footer class="dialog-actions"><button type="button" class="button primary" data-action="close-admin">Close</button></footer>
  </div>`;
}

async function submitCreateUser(form) {
  const values = new FormData(form);
  await api("/api/admin/users", {
    method: "POST",
    body: JSON.stringify({
      username: values.get("username"),
      temporary_password: values.get("temporary_password"),
    }),
  });
  await openAdmin();
  toast("User created with a forced password change.", "success");
}

async function resetUserPassword(userId) {
  const password = window.prompt("Enter a new temporary password (at least 8 characters):");
  if (!password) return;
  await api(`/api/admin/users/${userId}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ temporary_password: password }),
  });
  await openAdmin();
  toast("Password reset and existing sessions revoked.", "success");
}

async function deleteUser(userId, username) {
  if (!window.confirm(`Delete ${username} and all application-owned history and files?`)) return;
  await api(`/api/admin/users/${userId}`, { method: "DELETE" });
  await openAdmin();
  toast("User and application-owned content deleted.", "success");
}

async function refreshWorkflows() {
  await api("/api/admin/workflows/refresh", { method: "POST" });
  await loadSources();
  await openAdmin();
  toast("Published source discovery refreshed.", "success");
}

function closePanel(updateState = true) {
  if (updateState) state.panelOpen = false;
  document.querySelector(".app-shell")?.classList.remove("panel-open");
}

function toast(message, kind = "info") {
  const region = document.querySelector("#toast-region");
  if (!region) return;
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  region.append(node);
  window.setTimeout(() => node.remove(), 4500);
}

function focusFirstInvalid() {
  queueMicrotask(() => {
    const first = document.querySelector('[aria-invalid="true"]');
    const target = first?.matches("[data-number-slider]")
      ? first.closest("[data-control-block]")?.querySelector("[data-number-entry]")
      : first;
    target?.focus();
  });
}

function showAuthError(message) {
  const node = document.querySelector("#auth-error");
  if (node) node.textContent = message;
}

function clearAuthError() {
  showAuthError("");
}

function setBusy(form, busy) {
  for (const element of form.elements) element.disabled = busy;
}

function withoutNulls(value) {
  return Object.fromEntries(Object.entries(value || {}).filter(([, item]) => item));
}

function normalizeParameterErrors(value) {
  return Object.fromEntries(
    Object.entries(value || {}).map(([key, message]) => [key.replace(/^parameters\./, ""), message]),
  );
}

function escapeForAdmin(value) {
  const span = document.createElement("span");
  span.textContent = String(value ?? "");
  return span.innerHTML;
}

function renderFatal(error) {
  root.innerHTML = `<main class="auth-page"><section class="auth-card"><h1>Application unavailable</h1><p class="form-error">${escapeForAdmin(error.message || "Startup failed.")}</p><button class="button primary" data-action="reload">Reload</button></section></main>`;
}

initialize();
