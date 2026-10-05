import { defaultSourceKey, reconcileSourceKey, promptRuntimeId, promptRuntimeError } from "./prompt-routing.mjs";
import { photoViewerPreloadArtifact, createPhotoViewerPreloader } from "./photo-viewer-preload.mjs";
import { createPhotoViewerImages, photoKey } from "./photo-viewer-images.mjs";
import { submitGeneration, setSubmissionOwner, pendingSubmission, createSubmissionRecovery, pendingPromptJobs, finishPromptJob } from "./generation-submissions.mjs";
import { installThumbnails } from "./thumbnails.mjs";
import { reconcileGallery, reconcileGalleryCard, reconcilePhotoViewer } from "./gallery-dom.mjs";
import { createSettingsSync, settingsEqual } from "./user-settings.mjs";
import { createAutoGenerationSync } from "./auto-generation-sync.mjs";
import { automaticPromptUpdate, olderAutomaticProgress } from "./auto-generation-progress.mjs";
import { bindGalleryGroups } from "./gallery-groups.mjs";
import { installLoraStackControls, strongestLoraTrigger } from "./lora-stack.mjs";
import { installLoraManager, loraImagePath } from "./lora-manager.mjs";
import { createAdminLoraController, reconcileLoraStrengthMemory } from "./admin-loras.mjs";
import { api, getCsrfToken, isTransientError, setCsrfToken, upload } from "./api.mjs";
import { refreshGenerationEtaElements, updatePhotoViewerNextIn as refreshPhotoViewerNextIn } from "./generation-countdown.mjs";
import { bindGalleryCardHover } from "./gallery-hover.mjs";
import { bindGallerySelection } from "./gallery-selection.mjs";
import { createPromptRerun } from "./prompt-rerun.mjs";
import { createPromptRerunProgress } from "./prompt-rerun-progress.mjs";
import {
  EXPECTATION_INPUT_IDS,
  EXPECTATIONS_SECTION_KEY,
  checkIsActive,
  createExpectationCheck,
  expectationCheckBusy,
  expectationPanelPresentation,
  expectationSettingsErrors,
  expectationSettingsPayload,
  expectationSnapshotPayload,
  expectationStatusPresentation,
  expectationsActive,
  parseExpectations,
  scoredAttempt,
  shouldAutoApply,
} from "./expectation-check.mjs";
import {
  favoritesFilterActive,
  favoritesFilterPresentation,
  favoritesMode,
  excludedCheckpointRanks,
  excludedLoraRanks,
  loraRankFilterSummary,
  galleryFilterActive,
  galleryGenerationMatches,
  galleryViewParameters,
  nextFavoritesMode,
} from "./gallery-view.mjs";
import {
  CHECKPOINT_TIER_DEFINITIONS,
  EXPECTATION_LIMITS,
  MAX_BATCH_GENERATION_ITEMS,
  MAX_GENERATION_QUANTITY,
  MIN_GENERATION_QUANTITY,
  activeSourceStorageKey,
  applyChoiceStrengthDefaults,
  applyCollectionOrder,
  applyRecallSeedMode,
  clampGenerationQuantity,
  clientValidate,
  choiceOptions,
  choiceStrengthCompanion,
  collectionDepth,
  collectionSiblingIds,
  collectionSubtree,
  controlSectionStorageKey,
  createLatestRequestGate,
  createCoalescedTaskQueue,
  creativeDirectionStorageKey,
  creativeDirectionWarningText,
  defaultsForInterface,
  directionSignalNextStatus,
  hasActiveGeneration,
  interfaceInputs,
  insertTranscription,
  latestCompletedImageGeneration,
  loadRecentResolutions,
  mapModelChoiceValue,
  mapModelSelections,
  migrateInterfaceState,
  migrateLoraStrengthMemory,
  recentResolutionsForInterface,
  normalizeCheckpointTierLayout,
  normalizeCheckpointRanks,
  normalizeExpectationSettings,
  normalizeLoraRanks,
  stepLoraRank,
  checkpointRank,
  moveCheckpointRank,
  normalizeSourceModelSelections,
  normalizeInputValue,
  normalizeStoredActiveSource,
  normalizeStoredControlSections,
  normalizeStoredCreativeDirectionDraft,
  normalizeStoredParameterState,
  overwriteWithRecall,
  parametersForRequest,
  parameterStateStorageKey,
  photoViewerImageLayout,
  positivePromptInput,
  promptInstructionsForMode,
  recalledComfyuiInstanceState,
  recentResolutionKey,
  recordRecentResolution,
  reconcileInterfaceValues,
  removeRecentResolution,
  reorderedCollectionIds,
  resolutionPresetForValue,
  resolutionSummary,
  scaleToLayout,
  seedFormValue,
  snapResolutionValue,
  sortGenerationsNewestFirst,
  sourceModelParameterVariants,
  sourceModelSelectors,
} from "./lib.mjs";
import {
  clearGenerationEtaAnchors,
  collectionDeleteDialogMarkup,
  controlSectionKeysWithErrors,
  collectionDialogMarkup,
  collectionTileMarkup,
  collectionCountMarkup,
  generationActivityMarkup,
  generationActivityInfo,
  generationActivityTitle,
  detailMarkup,
  galleryCardMarkup,
  collectionThumbnailsHidden,
  galleryMarkup,
  generationProgressMarkup,
  generationPanelMarkup,
  generationRequestBlocked,
  generationSubmissionDisabled,
  generationButtonPresentation,
  promptGenerationButtonPresentation,
  generationButtonContentMarkup,
  imagePoolWorkers,
  imageWorkerPoolLabel,
  imageWorkerPoolTooltip,
  serverControlsMarkup,
  automationStatusMarkup,
  sharedSettingsStatusMarkup,
  promptPipelineMarkup,
  loginMarkup,
  moveDialogMarkup,
  passwordChangeMarkup,
  photoViewerMarkup,
  PROMPT_INSTRUCTIONS_HINTS,
  promptEditorMarkup,
  recentResolutionsMarkup,
  renderCollectionBar,
  serviceBannerMarkup,
  shellMarkup,
  sourcePickerDialogMarkup,
} from "./render.mjs";

const root = document.querySelector("#app");
const galleryHover = bindGalleryCardHover(root);
let disposeThumbnails = null;
let loraManagerController = null;
const adminLoraController = createAdminLoraController({
  api, getCsrfToken, notify: toast,
  actorId: () => state.session?.user?.id,
  storage: () => { try { return window.sessionStorage; } catch { return null; } },
  refreshSources: async (sourceKey) => {
    loraManagerController?.invalidateSource(sourceKey);
    await api("/api/admin/workflows/refresh", { method: "POST" });
    await loadSources();
    return state.sources;
  },
});

let autoSettingsSync = null;

const state = {
  session: null,
  comfyuiInstances: [],
  defaultComfyuiInstanceId: null,
  textComfyuiInstanceId: null,
  comfyuiInstancesStatus: "idle",
  comfyuiInstancesMessage: null,
  comfyuiImagePool: null,
  comfyuiImagePoolInstanceIds: [],
  comfyuiInstanceConfigurationMode: null,
  comfyuiInstanceError: null,
  comfyuiInstanceWarning: null,
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
  sourcePickerDialogOpen: false,
  sourcePickerDraft: null,
  checkpointTiers: {},
  loraTiers: {},
  modelSelectionsBySourceRevision: new Map(),
  selectedGenerationTargetCount: 0,
  generationQuantity: MIN_GENERATION_QUANTITY,
  controlSectionOpen: {},
  recentResolutions: [],
  parameters: {},
  loraImages: {},
  loraStrengthMemory: {},
  explicitParameterIds: new Set(),
  parameterStateBySource: {},
  pendingSourceMigration: null,
  selectedPreset: null,
  compositionId: null,
  promptDirectionSignal: {
    sourceKey: null,
    controlId: null,
    status: "idle",
    appliedValue: null,
  },
  promptEditorDirectionStatus: "idle",
  promptEditorDirectionAppliedValue: null,
  promptAssistant: {
    mode: "refine",
    creativeDirection: "",
    think: true,
    available: false,
    message: null,
    error: null,
    defaultInstructions: {},
    instructionOverrides: {},
    visionAvailable: undefined,
  },
  // Creative Direction expectations (panel settings) and the latest server check.
  expectations: normalizeExpectationSettings(null),
  expectationCheck: null,
  expectationCheckStarting: false,
  expectationStatusDismissedId: null,
  speechToText: { available: false, message: null },
  collections: [],
  collectionsStatus: "idle",
  collectionsMessage: null,
  currentCollectionId: null,
  generations: [],
  nextCursor: null,
  loadingMore: false,
  favoritesMode: "all",
  excludedCheckpointRanks: [],
  excludedLoraRanks: [],
  photoViewerDetachedGeneration: null,
  galleryScale: 45,
  galleryLayout: "grouped",
  gallerySkippedCursor: null,
  services: [],
  servicesStatus: "idle",
  servicesMessage: null,
  galleryStatus: "idle",
  galleryMessage: null,
  submitting: false,
  generationActivity: null,
  generationActivityUnavailable: false,
  generationSubmissionProgress: null,
  autoGenerate: false,
  automation: null,
  automationLoaded: false,
  automationBusy: false,
  autoSettingsSaving: false,
  autoSettingsStatus: "saved",
  autoSettingsMessage: null,
  maxAutoGenerations: 200,
  sharedSettingsStatus: "loading",
  sharedSettingsMessage: null,
  recentResolutionsBySource: {},
  promptGeneration: { enabled: false, active_source: null, sources: {}, previous_assistant_mode: null },
  promptGeneratorSources: [],
  promptGeneratorsLoaded: false,
  promptGeneratorSource: null,
  promptGenerationBusy: false,
  promptGenerationRequest: null,
  promptPreparationBusy: false,
  promptGenerationPhase: null,
  promptJobsUnavailable: false,
  submissionRecoveryPending: false,
  promptGenerationError: null,
  promptGenerationReadError: null,
  latestGeneratedPrompt: null,
  promptEditorDirty: false,
  autoGenerateCreativeDirection: false,
  autoGenerateStatus: "idle",
  autoGenerateStatusMessage: null,
  imageUploadsPending: 0,
  serverFieldErrors: {},
  formError: null,
  panelOpen: false,
  eventSource: null,
  liveUpdatesPaused: false,
  pendingLiveUpdates: [],
  lastEventId: 0,
  serviceTimer: null,
  generationEtaTimer: null,
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
const liveGenerationRefreshQueue = createCoalescedTaskQueue((id, options) => refreshGeneration(id, options));
let activeResolutionDrag = null;
let recentResolutionsRecordTimer = null;
let activePhotoViewerDrag = null;
let photoViewerImages = null;
const photoViewerPreloader = createPhotoViewerPreloader((artifact) => photoViewerImages?.preload(artifact));
let photoViewerImage = null;
let photoViewerDisplayed = null;
let photoViewerLoadKey = null;
let photoViewerLoadRevision = 0;
let photoViewerNavigationRevision = 0;
let photoViewerLoading = false;
let photoViewerPaging = false;
let photoViewerLoadError = null;
let photoViewerRetryDirection = null;
let photoViewerFrame = null;
let photoViewerLayoutPending = false;
let photoViewerDirection = "older";
let promptEditorReturnFocus = null;
let promptEditorInstructionOverrides = {};
let sourcePickerReturnFocus = null;
let sourcePickerContext = null;
let collectionDialogReturnFocus = null;
let collectionDeleteReturnFocus = null;
let moveDialogReturnFocus = null;
let checkpointTiersRevision = 0;
let checkpointRankChange = null;
let loraRankChange = null;
let activeSpeechSession = null;
let speechSessionSequence = 0;
let applicationStartupController = null;
let servicePollingController = null;
let startupGalleryBoundary = null;
let activityRefreshTimer = null;
let activityRequestToken = 0;
let activityRefreshRequest = null;
let settingsSync = null;
const settingsInterfaces = new Map();
let userStateTimer = null;
let promptJobTimer = null;
let promptGeneratorLoadToken = 0;
let promptJobsRefreshing = false;
let promptJobsReady = false;
const promptJobSeen = new Map();
const promptJobPhases = new Map();
let submissionRecovery = null;
let automationReadToken = 0;
let autoGeneratePinned = false;
let autoGeneratePinnedCollectionId = null;

function clearAutoGeneratePin() {
  autoGeneratePinned = false;
  autoGeneratePinnedCollectionId = null;
}
let promptCompositionRequests = 0;
let collectionNavigationToken = 0;
let collectionsRequestToken = 0;
let galleryHistoryController = null;
let galleryPageController = null;
let galleryPageRequest = null;
let topbarResizeObserver = null;
const pendingGenerationIds = new Set();

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
const CHECKPOINT_DRAG_TYPE = "application/x-comfyui-image-frontend-checkpoint";
const COLLECTION_DRAG_TYPE = "application/x-comfyui-image-frontend-collection";

const STARTUP_DEADLINES = {
  session: 10_000,
  preferences: 5_000,
  services: 8_000,
  comfyuiInstances: 8_000,
  gallery: 15_000,
  collections: 8_000,
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

let galleryGroups = null;
let gallerySelection = null;
let promptRerun = null;
let expectationChecks = null;
// Checks started in this page: the Prompt field value at start (auto-apply rule).
const expectationStarts = new Map();

function commitPanelLoras(id, values, memory, sourceKey, { updateSubject = false, refreshPanel = true } = {}) {
  if (sourceKey !== state.activeSourceKey) throw new Error("The workflow changed. Reopen the LoRA manager.");
  const control = interfaceInputs(sourceInterface(state.activeSource)).find((item) => item.id === id && item.type === "lora_stack");
  if (!control) throw new Error("The published LoRA catalog changed. Reopen the manager.");
  state.parameters[id] = structuredClone(values);
  state.loraStrengthMemory[id] = structuredClone(memory);
  state.explicitParameterIds.add(id);
  delete state.serverFieldErrors[id];
  state.formError = null;
  const { triggerWord } = strongestLoraTrigger(control, values);
  if (updateSubject && triggerWord) {
    const source = state.promptGeneratorSource;
    const input = interfaceInputs(source?.interface).find((item) => item.id === "subject_name" && item.type === "string");
    const saved = state.promptGeneration.sources[source?.source_key];
    if (input && saved && source.available !== false) {
      saved.values.subject_name = triggerWord;
      if (!saved.explicitInputIds.includes("subject_name")) saved.explicitInputIds.push("subject_name");
      state.promptGenerationError = null;
    }
  }
  persistActiveParameterState();
  syncParameterValidation(id);
  autoSettingsSync?.stage();
  settingsSync?.schedule();
  if (refreshPanel) renderPanel();
  else syncServerControls();
}

function bindDelegatedEvents() {
  installLoraStackControls(root, {
    context: (id) => {
      const control = interfaceInputs(sourceInterface(state.activeSource)).find((item) => item.id === id && item.type === "lora_stack");
      return control && state.activeSourceKey ? { control, sourceKey: state.activeSourceKey,
        values: state.parameters[id], memory: state.loraStrengthMemory[id] || {} } : null;
    },
    apply: (id, values, memory, sourceKey) => commitPanelLoras(id, values, memory, sourceKey, { refreshPanel: false }),
  });
  galleryGroups = bindGalleryGroups(root, {
    getState: () => state, render: renderGallery, notify: toast,
    visibleGenerations: () => visibleGenerations(),
    appendMembers: (items) => {
      const known = new Set(state.generations.map((item) => item.id));
      state.generations.push(...items.filter((item) => !known.has(item.id)));
      renderGallery();
    },
  });
  gallerySelection = bindGallerySelection(root, {
    getState: () => state,
    refresh: refreshAfterGalleryOperation,
    notify: toast,
    openRerun: (selection) => openPromptRerun(selection),
    rerunBlocked: promptRerunBlockedReason,
  });
  loraManagerController = installLoraManager(root, {
    api,
    loraTiers: () => state.loraTiers,
    loraRankChange: () => loraRankChange,
    context: (id, owner) => {
      if (owner === "rerun") return promptRerun?.controller.loraContext(id);
      const control = interfaceInputs(sourceInterface(state.activeSource)).find((item) => item.id === id && item.type === "lora_stack");
      if (!control || !state.activeSourceKey) return null;
      const subjectSource = state.promptGeneratorSource;
      const subjectAvailable = Boolean(subjectSource?.available !== false
        && interfaceInputs(subjectSource?.interface).some((item) => item.id === "subject_name" && item.type === "string")
        && state.promptGeneration.sources[subjectSource?.source_key]);
      return { control, sourceKey: state.activeSourceKey, sourceName: state.activeSource?.display_name || "Workflow",
        publicationRevision: sourceRevision(state.activeSource),
        values: state.parameters[id], memory: state.loraStrengthMemory[id] || {}, images: state.loraImages[id] || {}, subjectAvailable };
    },
    onImages: (sourceKey, id, images, owner) => {
      if (owner === "rerun") return promptRerun?.controller.onLoraImages(sourceKey, id, images);
      if (sourceKey !== state.activeSourceKey) return;
      state.loraImages[id] = images;
      renderPanel();
    },
    notify: toast,
    apply: (id, values, memory, sourceKey, owner) => {
      if (owner === "rerun") return promptRerun?.controller.applyLoras(id, values, memory, sourceKey);
      commitPanelLoras(id, values, memory, sourceKey, { updateSubject: true });
    },
  });
  root.addEventListener("submit", handleSubmit);
  root.addEventListener("click", handleClick);
  // Expectations only shape manual checks, so editing them never restages automation.
  root.addEventListener("change", async (event) => {
    const edit = event.target.closest("#generation-panel") && event.target.id !== "auto-generate" && !EXPECTATION_INPUT_IDS.includes(event.target.id);
    await handleChange(event);
    if (edit) autoSettingsSync?.stage();
  });
  root.addEventListener("input", (event) => {
    handleInput(event);
    if (event.target.closest("#generation-panel") && !["auto-generate", "auto-generate-limit", ...EXPECTATION_INPUT_IDS].includes(event.target.id)) autoSettingsSync?.stage();
  });
  root.addEventListener("toggle", (event) => {
    if (!event.target.matches?.("#generation-panel details[data-expectations]")) return;
    if (state.controlSectionOpen[EXPECTATIONS_SECTION_KEY] === event.target.open) return;
    state.controlSectionOpen[EXPECTATIONS_SECTION_KEY] = event.target.open;
    persistControlSections();
  }, true);
  for (const eventType of ["input", "change"]) root.addEventListener(eventType, (event) => {
    if (eventType === "input" && event.target.matches('input[type="checkbox"], input[type="radio"], select')) return;
    if (event.target.closest("#generation-panel")) queueMicrotask(() => { settingsSync?.schedule(); persistBrowserDraft(); });
  });
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
  document.addEventListener("visibilitychange", syncPhotoViewerPreload);
  window.addEventListener("resize", handlePhotoViewerResize);
  window.addEventListener("focus", () => void refreshUserState());
  root.addEventListener("focusout", () => setTimeout(() => void refreshUserState(), 0));
  window.addEventListener("hashchange", handleCollectionHashChange);
}

function promptRerunBlockedReason() {
  if (!state.automationLoaded || state.pendingAutoEnabled !== undefined || state.automationBusy) return "Checking auto generation…";
  if (state.autoGenerate) return "Turn off auto generation to re-run prompts";
  if (state.submitting || state.submissionRecoveryPending) return "Wait for the current submission to finish";
  if (!state.sources.some((source) => source.available !== false && (source.output_kind || "image") === "image")) return "No generation source is available";
  return null;
}

let promptRerunProgress = null;

function promptRerunController() {
  const dialog = root.querySelector("#gallery-rerun-dialog");
  if (!dialog) return null;
  if (promptRerun?.dialog === dialog) return promptRerun.controller;
  const controller = createPromptRerun(dialog, {
    api,
    sources: () => state.sources,
    collectionName: (id) => (id ? state.collections.find((item) => item.id === id)?.name || "this folder" : "Home"),
    initial: () => ({
      source: selectedGenerationSource(),
      parameters: state.parameters,
      selections: state.activeSource ? modelSelectionsForSource(state.activeSource) : {},
      quantity: state.generationQuantity,
      collectionId: state.currentCollectionId,
      checkpointTiers: state.checkpointTiers,
      loraMemory: state.loraStrengthMemory,
      loraImages: state.loraImages,
      recentResolutions: state.recentResolutions,
      promptAssistant: state.promptAssistant,
    }),
    sourceSettings: (key) => ({
      loraMemory: key === state.activeSourceKey ? state.loraStrengthMemory : {},
      loraImages: key === state.activeSourceKey ? state.loraImages : {},
      recentResolutions: key === state.activeSourceKey ? state.recentResolutions : state.recentResolutionsBySource[key] || [],
    }),
    closePickers: () => {
      if (sourcePickerContext?.owner === "rerun") closeSourcePickerDialog("cancel");
      loraManagerController?.closeForOwner("rerun");
    },
    loadSource: async (key) => {
      const summary = state.sources.find((source) => sourceKey(source) === key) || {};
      const detail = await api(`/api/workflows/${encodeURIComponent(key)}`, { operation: "Generation source details" });
      const contract = sourceInterface(detail);
      if (!contract) throw new Error("The selected source has no public interface.");
      return { ...summary, ...detail, interface: contract };
    },
    savedParameters: (key, source) => {
      if (key === state.activeSourceKey && state.activeSource && revisionsMatch(source, state.activeSource)) return state.parameters;
      return state.parameterStateBySource[key]?.values || {};
    },
    savedSelections: (source) => modelSelectionsForSource(source),
    submit: submitPromptRerun,
    onClose: () => gallerySelection?.restoreFocus(),
  });
  promptRerun = { dialog, controller };
  return controller;
}

function openPromptRerun({ body }) {
  const reason = promptRerunBlockedReason();
  if (reason) { toast(reason, "error"); return; }
  if (!selectedGenerationSource()) { toast("Choose a generation source in the control panel first.", "error"); return; }
  void promptRerunController()?.open({ body });
}

async function submitPromptRerun(body, plannedTotal) {
  const reason = promptRerunBlockedReason();
  if (reason) throw new Error(reason);
  const requestOwnerId = state.session.user.id;
  beginGenerationActivitySubmission(plannedTotal);
  state.submitting = true;
  syncGenerationSubmissionState();
  try {
    const result = await submitGeneration("/api/gallery/prompt-rerun", body, null, { signal: applicationStartupController.signal });
    if (state.session?.user?.id !== requestOwnerId) return null;
    await applyPromptRerunResult(result);
    return result;
  } catch (error) {
    state.generationSubmissionProgress = null;
    if (error.code === "submission_status_unknown") {
      submissionRecovery?.start();
      throw Object.assign(new Error("Reconnecting to your Prompt Re-run request… It will finish once the connection returns."), { code: error.code });
    }
    if (error.code === "source_republished" || error.code === "source_unavailable") await loadSources();
    throw error;
  } finally {
    state.submitting = false;
    syncGenerationSubmissionState();
    gallerySelection?.sync();
  }
}

async function applyPromptRerunResult(result) {
  const queued = result.items.filter((item) => item.generation).map((item) => item.generation);
  const failures = result.items.filter((item) => item.error).map((item) => item.error);
  for (const generation of queued) {
    if (!TERMINAL_GENERATION_STATUSES.has(generation.status)) pendingGenerationIds.add(generation.id);
  }
  const name = result.collection?.name || "the new folder";
  const fallback = result.resolution_fallback_count
    ? ` ${result.resolution_fallback_count} prompt${result.resolution_fallback_count === 1 ? "" : "s"} used the chosen resolution because the original size was unavailable.`
    : "";
  toast(
    result.run
      ? `Refining ${result.prompt_count} prompts for up to ${result.planned_count} images in “${name}”.${fallback}`
      : failures.length
      ? `Queued ${queued.length} of ${result.planned_count} generations into “${name}”. ${failures[0].message}${fallback}`
      : `Queued ${queued.length} generation${queued.length === 1 ? "" : "s"} into “${name}”.${fallback}`,
    failures.length ? "error" : "success",
  );
  gallerySelection?.clear();
  galleryGroups?.invalidate();
  await refreshGenerationActivity();
  if (result.collection?.id) openCollectionRoute(result.collection.id);
  else await loadCollections();
}

async function refreshAfterGalleryOperation({ operation, plan, result, destination }) {
  galleryGroups?.invalidate();
  if (operation === "favorite") {
    const generations = new Set(result.generation_ids);
    const collections = new Set(result.collection_ids);
    for (const id of generations) generationRefreshGate.invalidate(id);
    const updateGeneration = (item) => item && generations.has(item.id) ? { ...item, is_favorite: true } : item;
    const updateCollection = (item) => item && collections.has(item.id) ? { ...item, is_favorite: true } : item;
    state.generations = state.generations.map(updateGeneration);
    state.collections = state.collections.map(updateCollection);
    state.photoViewerDetachedGeneration = updateGeneration(state.photoViewerDetachedGeneration);
    renderGallery();
    updatePhotoViewerFavoriteControl();
    return;
  }
  if (operation === "move") {
    const ids = new Set(result.generation_ids);
    for (const id of ids) generationRefreshGate.invalidate(id);
    state.generations = state.generations.map((item) => ids.has(item.id) ? { ...item, collection_id: destination } : item);
    state.generations = state.generations.filter(generationBelongsToView);
  } else if (operation === "confirm-delete") {
    const removed = result.items.filter((item) => item.status !== "failed");
    const generationIds = new Set(removed.filter((item) => item.kind === "generation").map((item) => item.id));
    const collectionIds = new Set(removed.filter((item) => item.kind === "collection").flatMap((item) => collectionSubtree(state.collections, item.id).map((child) => child.id)));
    state.generations = state.generations.filter((item) => !generationIds.has(item.id) && !collectionIds.has(item.collection_id));
    for (const id of plan.generation_ids) if (generationIds.has(id)) pendingGenerationIds.delete(id);
  }
  await loadCollections();
  await loadStartupGallery();
}

async function handleSubmit(event) {
  let submission = null;
  if (event.target.id === "login-form") {
    event.preventDefault();
    submission = submitLogin(event.target);
  }
  else if (event.target.id === "password-form") {
    event.preventDefault();
    submission = submitPassword(event.target);
  }
  else if (event.target.id === "create-user-form") {
    event.preventDefault();
    submission = submitCreateUser(event.target);
  }
  else if (event.target.id === "collection-form") {
    event.preventDefault();
    submission = submitCollectionForm(event.target);
  }
  else if (event.target.id === "collection-delete-form") {
    event.preventDefault();
    submission = submitCollectionDelete(event.target);
  }
  else if (event.target.id === "move-generation-form") {
    event.preventDefault();
    submission = submitGenerationMove(event.target);
  }
  if (!submission) return;
  try {
    await submission;
  } catch (error) {
    toast(error.message || "Action failed.", "error");
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
    autoSettingsSync?.stage();
    return;
  }
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  const rerunEdit = Boolean(target.closest("#gallery-rerun-dialog")) || sourcePickerContext?.owner === "rerun";
  try {
    if (action === "generate") await generate();
    else if (action === "open-collection") {
      event.preventDefault();
      openCollectionRoute(target.dataset.collectionId || null);
    }
    else if (action === "new-collection") openCollectionDialog("create", target);
    else if (action === "rename-collection") openCollectionDialog("rename", target, target.dataset.collectionId);
    else if (action === "cancel-collection-dialog") closeCollectionDialog("cancel");
    else if (action === "delete-collection") openCollectionDeleteDialog(target, target.dataset.collectionId);
    else if (action === "cancel-collection-delete") closeCollectionDeleteDialog("cancel");
    else if (action === "toggle-collection-previews") toggleCollectionPreviews(target.dataset.collectionId);
    else if (action === "move-generation") openMoveDialog(target.dataset.generationId, target);
    else if (action === "cancel-move-generation") closeMoveDialog("cancel");
    else if (action === "open-generation-source-dialog") openSourcePickerDialog(target);
    else if (action === "cancel-generation-source-dialog") closeSourcePickerDialog("cancel");
    else if (action === "apply-generation-source-dialog") await applySourcePickerDialog();
    else if (action === "select-all-checkpoints") updateAllSourcePickerCheckpoints(true);
    else if (action === "clear-all-checkpoints") updateAllSourcePickerCheckpoints(false);
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
    else if (action === "apply-resolution-recent") applyRecentResolution(target);
    else if (action === "remove-resolution-recent") removeRecentResolutionEntry(target);
    else if (action === "close-panel") closePanel();
    else if (action === "open-prompt-editor") openPromptEditor(target);
    else if (action === "toggle-speech-recording") await toggleSpeechRecording(target);
    else if (action === "cancel-prompt-editor") closePromptEditor("cancel");
    else if (action === "apply-prompt-editor") applyPromptEditor();
    else if (action === "select-prompt-editor-text") selectPromptEditorText();
    else if (action === "clear-prompt-editor-text") clearPromptEditorText();
    else if (action === "paste-prompt-text") await pastePromptTextFromClipboard(target);
    else if (action === "paste-prompt-editor-text") await pastePromptEditorTextFromClipboard();
    else if (action === "compose-prompt-editor") await composePromptEditor(target);
    else if (action === "compose-prompt") await (expectationsActive(state) ? startExpectationCheck("apply") : composePrompt(target));
    else if (action === "view-expectation-check") openExpectationCheckDialog();
    else if (action === "dismiss-expectation-status") dismissExpectationStatus();
    else if (action === "reset-prompt-instructions") resetPromptInstructions(target);
    else if (action === "retry-auto-generate") void autoGenerationCommand("/retry");
    else if (action === "retry-auto-settings") await autoSettingsSync?.retry();
    else if (action === "generate-prompt") await runPromptGeneration(false);
    else if (action === "use-latest-prompt") applyLatestGeneratedPrompt();
    else if (action === "reload-prompt-generators") await loadPromptGenerators();
    else if (action === "settings-use-saved") await settingsSync?.resolve(false);
    else if (action === "settings-keep-local") await settingsSync?.resolve(true);
    else if (action === "settings-retry") void retrySharedSettings();
    else if (action === "increment-generation-quantity") applyGenerationQuantity(state.generationQuantity + 1);
    else if (action === "decrement-generation-quantity") applyGenerationQuantity(state.generationQuantity - 1);
    else if (action === "recall") await recall(target.dataset.generationId);
    else if (action === "toggle-favorite") await toggleFavorite(target.dataset.generationId, target);
    else if (action === "toggle-collection-favorite") await toggleCollectionFavorite(target.dataset.collectionId, target);
    else if (action === "toggle-favorites-filter") toggleFavoritesFilter();
    else if (action === "toggle-checkpoint-rank-filter") toggleCheckpointRankFilter(target.dataset.checkpointRank);
    else if (action === "toggle-lora-rank-filter") toggleLoraRankFilter(target.dataset.loraRank);
    else if (action === "show-all-checkpoint-ranks") {
      state.excludedCheckpointRanks = [];
      syncRankFilterControls();
      void reloadGalleryFilters();
      document.querySelector('[data-action="toggle-checkpoint-rank-filter"]')?.focus();
    }
    else if (action === "open-detail") await openDetail(target.dataset.generationId);
    else if (action === "open-photo") openPhotoViewer(target.dataset.generationId);
    else if (action === "close-photo") closePhotoViewer();
    else if (action === "retry-photo") retryPhotoViewer();
    else if (action === "toggle-photo-fullscreen") togglePhotoViewerFullscreen();
    else if (action === "toggle-photo-view") togglePhotoViewerMode();
    else if (action === "set-photo-view") setPhotoViewerMode(target.dataset.photoViewMode);
    else if (action === "toggle-photo-slideshow") togglePhotoViewerPlaybackMode();
    else if (action === "set-photo-playback") {
      setPhotoViewerPlaybackMode(target.dataset.photoPlaybackMode);
    }
    else if (action === "rank-checkpoint" || action === "retry-checkpoint-rank") await changePhotoCheckpointRank(target);
    else if (action === "rank-lora" || action === "retry-lora-rank") await changeLoraRank(target.dataset.loraIdentity, action === "retry-lora-rank" ? null : Number(target.dataset.rankStep), { focus: target });
    else if (action === "navigate-photo") await navigatePhotoViewer(target.dataset.direction);
    else if (action === "cancel-generation") await cancelGeneration(target.dataset.generationId, target);
    else if (action === "delete-generation") await deleteGeneration(target.dataset.generationId);
    else if (action === "load-more") await loadMore();
    else if (action === "retry-gallery") await loadStartupGallery();
    else if (action === "set-gallery-layout") updateGalleryLayout(target.dataset.galleryLayout, true);
    else if (action === "retry-generation-sources") await loadSources();
    else if (action === "open-admin") await openAdmin();
    else if (action === "refresh-workflows") await refreshWorkflows();
    else if (action === "reset-user-password") await resetUserPassword(target.dataset.userId);
    else if (action === "delete-user") await deleteUser(target.dataset.userId, target.dataset.username);
    else if (action === "close-admin") document.querySelector("#admin-dialog")?.close();
    else if (action === "reload") window.location.reload();
    if (!rerunEdit && ["apply-generation-source-dialog", "apply-resolution-recent", "apply-prompt-editor", "paste-prompt-text", "compose-prompt", "reset-prompt-instructions", "use-latest-prompt", "settings-use-saved", "settings-keep-local", "increment-generation-quantity", "decrement-generation-quantity", "recall"].includes(action)) autoSettingsSync?.stage();
  } catch (error) {
    toast(error.message || "Action failed.", "error");
  }
}

 async function handleChange(event) {
   const element = event.target;
   if (handleRerunResolutionInput(element)) return;
   if (element.matches("[data-resolution-preset]")) {
    if (element.value !== "custom") applyResolutionPreset(element);
    return;
  }
  if (element.id === "prompt-generation-enabled") {
    state.promptGeneration.enabled = element.checked;
    if (element.checked) {
      state.promptGeneration.previous_assistant_mode = state.promptAssistant.mode;
      state.promptAssistant.mode = "refine";
      state.controlSectionOpen["prompt-generation"] = true;
    } else {
      state.promptAssistant.mode = state.promptGeneration.previous_assistant_mode || "refine";
      state.controlSectionOpen["prompt-generation"] = false;
    }
    settingsSync?.schedule();
    renderPanel();
    return;
  }
  if (element.id === "prompt-generation-source") {
    await selectPromptGenerator(element.value || null);
    return;
  }
  if (element.matches("[data-prompt-generator-id], [data-prompt-generator-seed-mode]")) {
    updatePromptGeneratorControl(element);
    return;
  }
  if (element.id === "auto-generate") {
    const enabled = element.checked;
    state.pendingAutoEnabled = enabled;
    void changeAutoGeneration(enabled);
    return;
  }
  if (element.id === "auto-generate-limit") {
    const value = element.value.trim();
    const limit = value === "" ? null : Number(value);
    if (limit !== null && (!Number.isInteger(limit) || limit < 1 || limit > 1_000_000)) {
      element.setCustomValidity("Enter a whole number from 1 to 1,000,000, or leave blank for unlimited.");
      element.reportValidity();
      return;
    }
    element.setCustomValidity("");
    state.maxAutoGenerations = limit;
    settingsSync?.schedule();
    syncServerControls();
    return;
  }
  if (element.id === "generation-quantity") {
    applyGenerationQuantity(element.value);
    return;
  }
  if (element.id === "auto-generate-creative-direction") {
    state.autoGenerateCreativeDirection = element.checked;
    state.controlSectionOpen["creative-direction"] = element.checked;
    settingsSync?.schedule();
    renderPanel();
    syncServerControls();
    return;
  }
  if (element.id === "prompt-assistant-thinking-mode") {
    state.promptAssistant.think = element.checked;
    setPromptAssistantError(null);
    persistCreativeDirectionDraft();
    syncServerControls();
    return;
  }
  if (element.id === "expectations-enabled") {
    state.expectations = { ...state.expectations, enabled: element.checked };
    setPromptAssistantError(null);
    settingsSync?.schedule();
    syncServerControls();
    return;
  }
  if (element.id === "expectations-threshold" || element.id === "expectations-attempts") {
    updateExpectationLimit(element, true);
    return;
  }
  if (element.matches("[data-source-workflow-choice]")) {
    updateSourcePickerDraftWorkflow(element.value);
    return;
  }
  if (element.matches("[data-source-model-choice]")) {
    updateSourcePickerDraftModelSelection(
      element.dataset.sourceModelSourceKey,
      element.dataset.sourceModelParameterId,
      element.dataset.sourceModelValue,
      element.checked,
    );
    return;
  }
  if (element.matches("[data-checkpoint-tier-toggle]")) {
    updateSourcePickerTierSelection(
      element.dataset.checkpointTierId,
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
  if (element.matches("#collection-form [name=name]")) {
    syncCollectionNameValidation(element);
    return;
  }
  if (element.matches("[data-seed-mode]")) {
    const id = element.dataset.seedMode;
    const input = interfaceInputs(state.activeSource?.interface).find((item) => item.id === id);
    if (!input) return;
    const current = seedFormValue(input, state.parameters[id]);
    state.parameters[id] = { mode: element.checked ? "random" : "fixed", value: current.value };
    state.controlSectionOpen.seed = !element.checked;
    persistControlSections();
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
    if (
      control?.type === "choice" &&
      sourceModelSelectors(state.activeSource).some(
        (selector) => selector.parameter_id === control.id,
      )
    ) {
      renderPanel();
    }
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

function sourcesForPicker() {
  if (sourcePickerContext) return sourcePickerContext.sources();
  return state.sources.map((source) =>
    sourceKey(source) === state.activeSourceKey && state.activeSource
      ? { ...source, ...state.activeSource }
      : source,
  );
}

function openSourcePickerDialog(button) {
  const dialog = document.querySelector("#source-picker-dialog");
  if (!dialog || dialog.open || button.disabled || !state.activeSourceKey) return;
  sourcePickerContext = button.closest("#gallery-rerun-dialog") ? promptRerun?.controller.sourcePickerContext() : null;
  if (button.closest("#gallery-rerun-dialog") && !sourcePickerContext) return;
  const sources = sourcesForPicker();
  const activeKey = sourcePickerContext?.sourceKey || state.activeSourceKey;
  sourcePickerReturnFocus = button;
  state.sourcePickerDraft = {
    sourceKey: activeKey,
    modelSelectionsBySource: Object.fromEntries(
      sources.map((source) => [
        sourceKey(source),
        structuredClone(sourcePickerContext && sourceKey(source) === activeKey ? sourcePickerContext.selections : modelSelectionsForSource(source)),
      ]),
    ),
    checkpointTiers: structuredClone(state.checkpointTiers),
    checkpointTierBaseline: structuredClone(state.checkpointTiers),
    ranksEdited: false,
    searchQuery: "",
    // The panel's source: other workflows preview its checkpoints until edited here.
    originSourceKey: activeKey,
    editedSourceKeys: new Set(),
    carriedSourceKeys: new Set(),
  };
  ensureSourcePickerDraftPreferences(
    sources.find((source) => sourceKey(source) === activeKey),
  );
  state.sourcePickerDialogOpen = true;
  renderSourcePickerDialog();
  dialog.showModal();
  queueMicrotask(() => {
    dialog.querySelector("[data-source-workflow-choice]")?.focus({ preventScroll: true });
  });
}

function renderSourcePickerDialog() {
  const dialog = document.querySelector("#source-picker-dialog");
  const draft = state.sourcePickerDraft;
  if (!dialog || !draft) return;
  const scrollTop = dialog.querySelector("[data-checkpoint-tier-board]")?.scrollTop || 0;
  dialog.innerHTML = sourcePickerDialogMarkup(sourcesForPicker(), {
    sourceKey: draft.sourceKey,
    modelSelectionsBySource: draft.modelSelectionsBySource,
    checkpointTiers: draft.checkpointTiers,
    searchQuery: draft.searchQuery,
  });
  if (draft.busy) dialog.querySelector(".source-picker-dialog-content")?.setAttribute("inert", "");
  if (draft.busy) dialog.querySelector('[data-action="apply-generation-source-dialog"]')?.setAttribute("disabled", "");
  if (draft.savingRanks) for (const button of dialog.querySelectorAll('[data-action="cancel-generation-source-dialog"]')) button.disabled = true;
  if (draft.error) {
    const message = dialog.querySelector(".source-picker-summary");
    message.classList.add("field-error");
    message.setAttribute("role", "alert");
    message.textContent = draft.error;
  }
  const scroller = dialog.querySelector("[data-checkpoint-tier-board]");
  if (scroller) scroller.scrollTop = scrollTop;
  for (const input of dialog.querySelectorAll('[data-indeterminate="true"]')) {
    input.indeterminate = true;
  }
}

function sourcePickerDraftSource() {
  const key = state.sourcePickerDraft?.sourceKey;
  return sourcesForPicker().find(
    (source) => sourceKey(source) === key && source.available !== false,
  ) || null;
}

function ensureSourcePickerDraftPreferences(source) {
  const draft = state.sourcePickerDraft;
  const key = sourceKey(source);
  const selector = sourceModelSelectors(source)[0];
  if (!draft || !key) return;
  if (!draft.modelSelectionsBySource[key]) {
    draft.modelSelectionsBySource[key] = structuredClone(modelSelectionsForSource(source));
  }
  if (!selector) return;
  draft.checkpointTiers = normalizeCheckpointRanks(draft.checkpointTiers, [selector]);
}

// Main panel only: choosing another workflow previews the panel source's checkpoints
// mapped into it, so applying the change keeps the same checkpoints where possible.
// Prompt Re-run keeps its own source-switching rules.
function carrySourcePickerDraftSelections(source) {
  const draft = state.sourcePickerDraft;
  const key = sourceKey(source);
  if (
    !draft ||
    sourcePickerContext ||
    !key ||
    key === draft.originSourceKey ||
    draft.editedSourceKeys?.has(key)
  ) {
    return;
  }
  const origin = sourcesForPicker().find((item) => sourceKey(item) === draft.originSourceKey);
  const carried = origin
    ? mapModelSelections(origin, draft.modelSelectionsBySource?.[draft.originSourceKey] || {}, source)
    : null;
  if (carried) {
    draft.modelSelectionsBySource[key] = carried;
    draft.carriedSourceKeys.add(key);
  }
}

// A workflow only previewed with carried checkpoints keeps its stored selection
// unless it becomes the applied source or is edited in the picker.
function sourcePickerDraftOnlyPreviewed(draft, key) {
  return Boolean(
    draft?.carriedSourceKeys?.has(key) &&
      !draft.editedSourceKeys?.has(key) &&
      key !== draft.sourceKey,
  );
}

function markSourcePickerDraftEdited(key) {
  state.sourcePickerDraft?.editedSourceKeys?.add(key);
}

function updateSourcePickerDraftWorkflow(key) {
  const draft = state.sourcePickerDraft;
  const source = sourcesForPicker().find(
    (item) => sourceKey(item) === key && item.available !== false,
  );
  if (!draft || !source) return;
  draft.sourceKey = key;
  draft.searchQuery = "";
  ensureSourcePickerDraftPreferences(source);
  carrySourcePickerDraftSelections(source);
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector("#source-picker-dialog [data-source-workflow-choice]")
      ?.focus({ preventScroll: true });
  });
}

function updateSourcePickerSearch(value) {
  const draft = state.sourcePickerDraft;
  if (!draft) return;
  draft.searchQuery = String(value || "");
  const selectionStart = document.querySelector("[data-checkpoint-search]")?.selectionStart;
  renderSourcePickerDialog();
  queueMicrotask(() => {
    const input = document.querySelector("#source-picker-dialog [data-checkpoint-search]");
    input?.focus({ preventScroll: true });
    if (Number.isInteger(selectionStart)) input?.setSelectionRange(selectionStart, selectionStart);
  });
}

function updateSourcePickerDraftModelSelection(
  key,
  parameterId,
  value,
  checked,
) {
  const draft = state.sourcePickerDraft;
  const source = sourcePickerDraftSource();
  const selector = sourceModelSelectors(source).find(
    (item) => item.parameter_id === parameterId,
  );
  if (
    !draft ||
    !source ||
    sourceKey(source) !== key ||
    source.available === false ||
    !selector ||
    !selector.choices.some((choice) => choice.value === value)
  ) {
    return;
  }
  const stored = draft.modelSelectionsBySource?.[key]?.[parameterId];
  const selected = new Set(
    Array.isArray(stored)
      ? stored
      : modelSelectionsForSource(source)[parameterId] || [],
  );
  if (checked) selected.add(value);
  else selected.delete(value);
  markSourcePickerDraftEdited(key);
  draft.modelSelectionsBySource[key] = {
    ...draft.modelSelectionsBySource[key],
    [parameterId]: [...selected],
  };
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector(
        `#source-picker-dialog [data-source-model-choice][data-source-model-source-key="${CSS.escape(key)}"][data-source-model-parameter-id="${CSS.escape(parameterId)}"][data-source-model-value="${CSS.escape(value)}"]`,
      )
      ?.focus({ preventScroll: true });
  });
}

function updateAllSourcePickerCheckpoints(checked, tierId = null) {
  const draft = state.sourcePickerDraft;
  const source = sourcePickerDraftSource();
  const selector = sourceModelSelectors(source)[0];
  if (!draft || !source || !selector) return;
  ensureSourcePickerDraftPreferences(source);
  const key = sourceKey(source);
  const current = new Set(draft.modelSelectionsBySource[key]?.[selector.parameter_id] || []);
  const layout = normalizeCheckpointTierLayout(selector, draft.checkpointTiers);
  const values = tierId
    ? layout[tierId] || []
    : selector.choices.map((choice) => choice.value);
  for (const value of values) {
    if (checked) current.add(value);
    else current.delete(value);
  }
  markSourcePickerDraftEdited(key);
  draft.modelSelectionsBySource[key] = {
    ...draft.modelSelectionsBySource[key],
    [selector.parameter_id]: [...current],
  };
  renderSourcePickerDialog();
  queueMicrotask(() => {
    const selectorText = tierId
      ? `[data-checkpoint-tier-toggle][data-checkpoint-tier-id="${CSS.escape(tierId)}"]`
      : `[data-action="${checked ? "clear-all-checkpoints" : "select-all-checkpoints"}"]`;
    document.querySelector(`#source-picker-dialog ${selectorText}`)?.focus({ preventScroll: true });
  });
}

function updateSourcePickerTierSelection(tierId, checked) {
  if (!CHECKPOINT_TIER_DEFINITIONS.some((tier) => tier.id === tierId)) return;
  updateAllSourcePickerCheckpoints(checked, tierId);
}

function moveSourcePickerCheckpoint(value, destinationTierId, beforeValue = null) {
  const draft = state.sourcePickerDraft;
  const source = sourcePickerDraftSource();
  const selector = sourceModelSelectors(source)[0];
  if (
    !draft ||
    !source ||
    !selector ||
    draft.searchQuery ||
    !CHECKPOINT_TIER_DEFINITIONS.some((tier) => tier.id === destinationTierId) ||
    !selector.choices.some((choice) => choice.value === value)
  ) {
    return false;
  }
  ensureSourcePickerDraftPreferences(source);
  const identity = selector.choices.find((choice) => choice.value === value)?.checkpoint_id;
  if (!identity) return false;
  const beforeIdentity = selector.choices.find((choice) => choice.value === beforeValue)?.checkpoint_id;
  draft.checkpointTiers = moveCheckpointRank(draft.checkpointTiers, identity, destinationTierId, beforeIdentity);
  draft.ranksEdited = true;
  renderSourcePickerDialog();
  queueMicrotask(() => {
    document
      .querySelector(
        `#source-picker-dialog [data-checkpoint-drag-handle][data-checkpoint-value="${CSS.escape(value)}"]`,
      )
      ?.focus({ preventScroll: true });
  });
  return true;
}

function closeSourcePickerDialog(returnValue, { flushDeferredUpdates = true } = {}) {
  const dialog = document.querySelector("#source-picker-dialog");
  const wasOpen = state.sourcePickerDialogOpen;
  state.sourcePickerDialogOpen = false;
  state.sourcePickerDraft = null;
  sourcePickerContext?.cancel?.();
  if (dialog?.open) dialog.close(returnValue);
  if (wasOpen && flushDeferredUpdates) void flushDeferredSourcePickerUpdates();
}

async function applySourcePickerDialog() {
  const draft = state.sourcePickerDraft;
  const sources = sourcesForPicker();
  const selectedSource = sources.find(
    (source) => sourceKey(source) === draft?.sourceKey && source.available !== false,
  );
  if (!draft || !selectedSource || draft.busy) return;
  const selector = sourceModelSelectors(selectedSource)[0];
  const selectedValues = selector
    ? draft.modelSelectionsBySource?.[draft.sourceKey]?.[selector.parameter_id] || []
    : [];
  if (selector && !selectedValues.length) return;
  if (!await persistCheckpointTierDraft(draft)) return;
  if (sourcePickerContext) {
    const context = sourcePickerContext;
    draft.busy = true;
    draft.error = "";
    renderSourcePickerDialog();
    try {
      const applied = await context.apply(structuredClone(draft));
      if (state.sourcePickerDraft === draft && sourcePickerContext === context && applied) closeSourcePickerDialog("apply");
    } catch (error) {
      if (state.sourcePickerDraft === draft) {
        draft.busy = false;
        draft.error = error.message || "The source could not be loaded.";
        renderSourcePickerDialog();
      }
    }
    return;
  }
  const sourceChanged = draft.sourceKey !== state.activeSourceKey;
  for (const source of sources) {
    if (sourcePickerDraftOnlyPreviewed(draft, sourceKey(source))) continue;
    setModelSelectionsForSource(
      source,
      draft.modelSelectionsBySource?.[sourceKey(source)] || {},
    );
  }
  closeSourcePickerDialog("apply", { flushDeferredUpdates: false });
  state.serverFieldErrors = {};
  state.formError = null;
  if (sourceChanged) {
    // The draft already carried (and possibly edited) this workflow's checkpoints.
    await selectSource(draft.sourceKey, { summary: selectedSource, carryModelSelections: false });
  }
  applyStoredModelSelectionsToActiveParameters();
  renderPanel();
  await saveCheckpointTierPreferences();
  await flushDeferredSourcePickerUpdates({ panelAlreadyRendered: true });
}

async function persistCheckpointTierDraft(draft) {
  if (!draft.ranksEdited) return true;
  const previous = state.checkpointTiers;
  const next = normalizeCheckpointRanks(draft.checkpointTiers);
  if (settingsEqual(previous, next)) return true;
  if (!settingsEqual(previous, draft.checkpointTierBaseline)) {
    draft.error = "Checkpoint ranks changed elsewhere. Cancel and reopen to use the latest ranks.";
    renderSourcePickerDialog();
    return false;
  }
  const sessionId = state.session?.user?.id;
  draft.busy = true;
  draft.savingRanks = true;
  draft.error = "";
  state.checkpointTiers = next;
  checkpointTiersRevision += 1;
  renderSourcePickerDialog();
  renderGallery();
  renderPhotoViewer();
  const saved = await settingsSync?.save();
  if (sessionId !== state.session?.user?.id) return false;
  draft.busy = false;
  draft.savingRanks = false;
  if (saved) {
    draft.checkpointTierBaseline = structuredClone(state.checkpointTiers);
    draft.ranksEdited = false;
  }
  if (!saved) {
    if (settingsEqual(state.checkpointTiers, next)) state.checkpointTiers = previous;
    checkpointTiersRevision += 1;
    settingsSync?.persistLocal();
    draft.error = state.sharedSettingsMessage || "Checkpoint ranks could not be saved. Retry Apply.";
    renderGallery();
    renderPhotoViewer();
  }
  if (state.sourcePickerDraft === draft) renderSourcePickerDialog();
  if (excludedCheckpointRanks(state).length) void reloadGalleryFilters();
  return Boolean(saved && state.sourcePickerDraft === draft);
}

async function changePhotoCheckpointRank(button) {
  if (checkpointRankChange?.status === "saving") return;
  const identity = button.dataset.checkpointId;
  if (!/^cp1_[0-9a-f]{64}$/.test(identity || "")) return;
  const from = checkpointRank(identity, state.checkpointTiers);
  const index = CHECKPOINT_TIER_DEFINITIONS.findIndex((tier) => tier.id === from);
  const to = button.dataset.action === "retry-checkpoint-rank"
    ? (checkpointRankChange?.identity === identity ? checkpointRankChange.to : null)
    : CHECKPOINT_TIER_DEFINITIONS[index + Number(button.dataset.rankStep)]?.id;
  if (!to || to === from) return;
  const previous = state.checkpointTiers;
  const next = moveCheckpointRank(previous, identity, to);
  const change = { identity, from, to, status: "saving" };
  const sessionId = state.session?.user?.id;
  const wasFocused = document.activeElement === button;
  const step = button.dataset.rankStep || "-1";
  checkpointRankChange = change;
  state.checkpointTiers = next;
  checkpointTiersRevision += 1;
  renderGallery();
  renderPhotoViewer();
  const saved = await settingsSync?.save();
  if (sessionId !== state.session?.user?.id || checkpointRankChange !== change) return;
  if (saved) change.status = "saved";
  else {
    if (settingsEqual(state.checkpointTiers, next)) state.checkpointTiers = previous;
    checkpointTiersRevision += 1;
    settingsSync?.persistLocal();
    change.status = "error";
    change.message = state.sharedSettingsMessage || "The rank could not be saved. Try again.";
  }
  renderGallery();
  renderPhotoViewer();
  if (wasFocused && document.querySelector("#photo-viewer")?.open &&
      photoViewerGeneration(state.photoViewerGenerationId)?.checkpoint_id === identity) {
    const controls = [...document.querySelectorAll('#photo-viewer [data-action="rank-checkpoint"]')];
    (controls.find((control) => control.dataset.rankStep === step && !control.disabled) || controls.find((control) => !control.disabled))?.focus({ preventScroll: true });
  }
  if (excludedCheckpointRanks(state).length) void reloadGalleryFilters();
  if (saved) setTimeout(() => {
    if (checkpointRankChange !== change) return;
    checkpointRankChange = null;
    renderPhotoViewer();
  }, 2600);
}

async function saveCheckpointTierPreferences() {
  await settingsSync?.save();
}

function handleSourcePickerDialogClose(event) {
  const wasOpen = state.sourcePickerDialogOpen;
  state.sourcePickerDialogOpen = false;
  state.sourcePickerDraft = null;
  const previous = sourcePickerReturnFocus;
  const owner = sourcePickerContext?.owner;
  sourcePickerContext?.cancel?.();
  sourcePickerContext = null;
  sourcePickerReturnFocus = null;
  queueMicrotask(() => {
    const fallback = document.querySelector(owner === "rerun" && document.querySelector("#gallery-rerun-dialog")?.open ? "#rerun-workflow-source" : "#workflow-source");
    const target = previous?.isConnected ? previous : fallback;
    if (target && !target.disabled) target.focus({ preventScroll: true });
  });
  if (wasOpen) void flushDeferredSourcePickerUpdates();
}

function toggleControlSection(trigger) {
  if (trigger.closest("#gallery-rerun-dialog")) return promptRerun?.controller.toggleSection(trigger);
  const section = trigger.closest("[data-control-section]");
  if (!section) return;
  const open = trigger.getAttribute("aria-expanded") !== "true";
  state.controlSectionOpen[section.dataset.controlSection] = open;
  setControlSectionElementOpen(section, open);
  persistControlSections();
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

function syncCreativeDirectionWarning(textarea) {
  if (!textarea?.id) return;
  const warning = document.querySelector(`[data-creative-direction-warning="${textarea.id}"]`);
  if (!warning) return;
  const text = creativeDirectionWarningText(textarea.value);
  if (warning.textContent !== text) warning.textContent = text;
  warning.hidden = !text;
}

function handleInput(event) {
  const element = event.target;
  if (element.id === "creative-direction" || element.id === "prompt-editor-creative-direction") {
    syncCreativeDirectionWarning(element);
  }
  if (handleRerunResolutionInput(element)) return;
  if (element.matches("[data-prompt-generator-id]")) { updatePromptGeneratorControl(element); return; }
  if (element.id === "auto-generate-limit") {
    const value = element.value.trim() === "" ? null : Number(element.value);
    if (value === null || (Number.isInteger(value) && value >= 1 && value <= 1_000_000)) state.maxAutoGenerations = value;
    return;
  }

  if (element.matches("[data-checkpoint-search]")) {
    updateSourcePickerSearch(element.value);
    return;
  }
  if (element.matches("#collection-form [name=name]")) {
    syncCollectionNameValidation(element);
    return;
  }
  if (
    element.matches(
      "#prompt-editor-dialog [data-prompt-editor-input], #prompt-editor-creative-direction, [name=prompt-editor-assistant-mode], #prompt-editor-thinking-mode, #prompt-editor-instructions",
    )
  ) {
    const dialog = element.closest("#prompt-editor-dialog");
    setPromptEditorAssistantError(dialog, null);
    delete dialog.dataset.promptAssistantCompositionId;
  }
  if (element.id === "prompt-editor-instructions" || element.name === "prompt-editor-assistant-mode") {
    const dialog = element.closest("#prompt-editor-dialog");
    capturePromptInstructions(dialog, promptEditorInstructionOverrides);
    syncPromptInstructions(dialog, promptEditorInstructionOverrides, promptEditorMode(dialog));
    delete dialog.dataset.promptAssistantCompositionId;
    return;
  }
  if (element.id === "prompt-assistant-instructions") {
    capturePromptInstructions(element.closest("#prompt-assistant"), state.promptAssistant.instructionOverrides);
    persistPromptInstructions();
    setPromptAssistantError(null);
    syncServerControls();
    return;
  }
  if (element.matches("[data-prompt-editor-input]")) {
    updatePromptEditorStats(element.value);
    if (state.promptEditorDirectionStatus === "applied") setPromptEditorDirectionSignal("idle");
    return;
  }
  if (element.id === "gallery-scale") {
    updateGalleryScale(element.value, false);
    return;
  }
  if (element.id === "generation-quantity") {
    const filtered = element.value.replace(/[^0-9]/g, "").slice(0, 3);
    if (filtered !== element.value) element.value = filtered;
    if (filtered) { state.generationQuantity = Math.max(1, Math.min(MAX_GENERATION_QUANTITY, Number(filtered))); persistGenerationQuantity(); }
    return;
  }
  if (element.id === "creative-direction") {
    state.promptAssistant.creativeDirection = element.value;
    setPromptAssistantError(null);
    persistCreativeDirectionDraft();
    syncServerControls();
    return;
  }
  if (element.id === "creative-direction-expectations") {
    // Update the badge and count in place so the caret and scroll stay put.
    state.expectations = { ...state.expectations, text: element.value };
    setPromptAssistantError(null);
    syncExpectationControls();
    return;
  }
  if (element.id === "expectations-threshold" || element.id === "expectations-attempts") {
    updateExpectationLimit(element, false);
    return;
  }
  if (element.name === "assistant-mode") {
    const assistant = element.closest("#prompt-assistant");
    capturePromptInstructions(assistant, state.promptAssistant.instructionOverrides);
    state.promptAssistant.mode = state.promptGeneration.enabled ? "refine" : element.value;
    syncPromptInstructions(assistant, state.promptAssistant.instructionOverrides, element.value);
    setPromptAssistantError(null);
    persistCreativeDirectionDraft();
    syncServerControls();
    return;
  }
  if (element.matches("[data-control-id]") && !element.matches("input[type=file]")) {
    const control = updateControlFromElement(element);
    if (control?.semantic_role === "positive_prompt" || control?.id === "prompt.text") {
      syncServerControls();
      setPromptAssistantError(null);
      if (
        state.promptDirectionSignal.status === "applied" &&
        state.promptDirectionSignal.sourceKey === state.activeSourceKey &&
        state.promptDirectionSignal.controlId === control.id
      ) {
        setPromptDirectionSignal("idle");
      }
    }
    syncNumberControlPair(element);
    syncChoiceStrengthControl(control);
    if (element.dataset.resolutionPart || element.dataset.resolutionAxis) {
      const container = element.dataset.resolutionAxis
        ? element.closest("[data-resolution-pair-block]")
        : element.closest("[data-control-block]");
      const grid = container?.querySelector("[data-resolution-grid]");
      const value = resolutionValueForGrid(grid);
      updateResolutionUi(grid, value);
      syncResolutionPresetSelect(container, value);
      queueRecentResolutionRecord(grid);
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
  promptEditorInstructionOverrides = structuredClone(state.promptAssistant.instructionOverrides);
  dialog.dataset.promptControlId = controlId;
  delete dialog.dataset.promptAssistantCompositionId;
  delete dialog.dataset.promptAssistantModel;
  dialog.returnValue = "";
  state.promptEditorDirectionStatus = "idle";
  state.promptEditorDirectionAppliedValue = null;
  dialog.innerHTML = promptEditorMarkup(controlId, label, source.value, {
    ...state.promptAssistant,
    promptGenerationEnabled: state.promptGeneration.enabled,
    composeLabel: expectationPanelPresentation(state).composeLabel,
    composeDisabled: expectationCheckBusy(state),
  });
  syncPromptInstructions(dialog, promptEditorInstructionOverrides, state.promptAssistant.mode);
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
  const assistantThinking = dialog?.querySelector("#prompt-editor-thinking-mode");
  const controlId = dialog?.dataset.promptControlId;
  const control = interfaceInputs(sourceInterface(state.activeSource)).find((item) => item.id === controlId);
  if (!dialog?.open || !editor || !controlId || !control) return;

  state.parameters[controlId] = normalizeInputValue(control, editor.value);
  state.promptEditorDirty = true;
  persistBrowserDraft();
  state.explicitParameterIds.add(controlId);
  delete state.serverFieldErrors[controlId];
  state.formError = null;
  state.promptAssistant.creativeDirection = creativeDirection?.value || "";
  state.promptAssistant.mode = !state.promptGeneration.enabled && assistantMode?.value === "create" ? "create" : "refine";
  state.promptAssistant.think = assistantThinking?.checked !== false;
  capturePromptInstructions(dialog, promptEditorInstructionOverrides);
  state.promptAssistant.instructionOverrides = structuredClone(promptEditorInstructionOverrides);
  persistPromptInstructions();
  persistCreativeDirectionDraft();
  syncServerControls();
  if (dialog.dataset.promptAssistantCompositionId) {
    state.compositionId = dialog.dataset.promptAssistantCompositionId;
    state.promptAssistant.historicalModel = dialog.dataset.promptAssistantModel || null;
  } else {
    state.compositionId = null;
  }
  if (
    state.promptEditorDirectionStatus === "applied" &&
    state.promptEditorDirectionAppliedValue === editor.value
  ) {
    setPromptDirectionSignal("applied", editor.value);
  }
  state.promptEditorDirectionStatus = "idle";
  state.promptEditorDirectionAppliedValue = null;
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
  if (state.promptEditorDirectionStatus === "applied") setPromptEditorDirectionSignal("idle");
  editor.focus();
}

async function readClipboardText() {
  if (!navigator.clipboard?.readText) {
    throw new Error("Clipboard access is unavailable in this browser.");
  }
  return String(await navigator.clipboard.readText() ?? "");
}

async function pastePromptTextFromClipboard(button) {
  const controlId = button.dataset.promptControlId;
  const element = document.querySelector(`[data-control-id="${CSS.escape(controlId || "")}"]`);
  if (!controlId || !element) return;
  const text = await readClipboardText();
  element.value = text;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.focus({ preventScroll: true });
  element.setSelectionRange?.(0, 0);
  toast("Clipboard contents replaced the prompt.", "success");
}

async function pastePromptEditorTextFromClipboard() {
  const editor = document.querySelector("#prompt-editor-dialog[open] [data-prompt-editor-input]");
  if (!editor) return;
  const text = await readClipboardText();
  editor.value = text;
  editor.dispatchEvent(new Event("input", { bubbles: true }));
  editor.focus({ preventScroll: true });
  editor.setSelectionRange?.(0, 0);
  toast("Clipboard contents replaced the prompt.", "success");
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
    schedulePhotoViewerFrame();
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
  commitRecentResolutionValue(grid);
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
    schedulePhotoViewerFrame();
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
  schedulePhotoViewerFrame();
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
  const checkpointHandle = event.target.closest("[data-checkpoint-drag-handle]");
  if (checkpointHandle && event.altKey) {
    if (moveSourcePickerCheckpointFromKeyboard(checkpointHandle, event.key)) {
      event.preventDefault();
    }
    return;
  }
  const collectionControl = event.target.closest(
    "#gallery [data-action='open-collection'][data-collection-id]",
  );
  if (collectionControl && event.altKey) {
    if (moveCollectionFromKeyboard(collectionControl, event.key)) event.preventDefault();
    return;
  }
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
  queueRecentResolutionRecord(grid);
}

function moveSourcePickerCheckpointFromKeyboard(handle, key) {
  if (!state.sourcePickerDraft || state.sourcePickerDraft.searchQuery) return false;
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(key)) return false;
  const source = sourcePickerDraftSource();
  const selector = sourceModelSelectors(source)[0];
  if (!source || !selector) return false;
  ensureSourcePickerDraftPreferences(source);
  const sourcePreference = state.sourcePickerDraft.checkpointTiers;
  const layout = normalizeCheckpointTierLayout(selector, sourcePreference);
  const value = handle.dataset.checkpointValue;
  const tierId = handle.dataset.checkpointTierId;
  const tierIndex = CHECKPOINT_TIER_DEFINITIONS.findIndex((tier) => tier.id === tierId);
  const values = layout[tierId] || [];
  const valueIndex = values.indexOf(value);
  if (tierIndex < 0 || valueIndex < 0) return false;
  if (key === "ArrowLeft") {
    if (valueIndex === 0) return false;
    return moveSourcePickerCheckpoint(value, tierId, values[valueIndex - 1]);
  }
  if (key === "ArrowRight") {
    if (valueIndex === values.length - 1) return false;
    return moveSourcePickerCheckpoint(value, tierId, values[valueIndex + 2] || null);
  }
  const destinationIndex = tierIndex + (key === "ArrowUp" ? -1 : 1);
  const destination = CHECKPOINT_TIER_DEFINITIONS[destinationIndex];
  if (!destination) return false;
  return moveSourcePickerCheckpoint(value, destination.id);
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

function rerunResolutionContext(element) {
  return element?.closest("#gallery-rerun-dialog") ? promptRerun?.controller.resolutionContext() : null;
}

function handleRerunResolutionInput(element) {
  if (!element.closest("#gallery-rerun-dialog") || !element.matches("[data-control-id]")) return false;
  const block = element.dataset.resolutionAxis
    ? element.closest("[data-resolution-pair-block]")
    : element.closest("[data-control-block]");
  const grid = block?.querySelector("[data-resolution-grid]");
  if (!grid || element.disabled) return true;
  const current = resolutionValueForGrid(grid);
  const part = element.dataset.resolutionPart || element.dataset.resolutionAxis;
  if (!part) return true;
  const value = { ...current, [part]: element.value === "" ? null : Number(element.value) };
  rerunResolutionContext(grid)?.set(value.width, value.height);
  updateResolutionUi(grid, value);
  syncResolutionPresetSelect(block, value);
  queueRecentResolutionRecord(grid);
  return true;
}

function setResolutionValue(grid, width, height) {
  if (grid.closest("#gallery-rerun-dialog")) {
    rerunResolutionContext(grid)?.set(width, height);
    updateResolutionUi(grid, resolutionValueForGrid(grid));
    return;
  }
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
  const values = grid.closest("#gallery-rerun-dialog") ? rerunResolutionContext(grid)?.values || {} : state.parameters;
  const widthId = grid.dataset.resolutionWidthId;
  const heightId = grid.dataset.resolutionHeightId;
  if (widthId && heightId) {
    return { width: values[widthId], height: values[heightId] };
  }
  return values[grid.dataset.controlId] || {};
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

function commitResolutionValue(grid, width, height) {
  if (grid.closest("#gallery-rerun-dialog")) {
    rerunResolutionContext(grid)?.set(width, height);
    updateResolutionUi(grid, resolutionValueForGrid(grid));
    syncResolutionPresetSelect(grid.closest("[data-resolution-pair-block], [data-control-block]"), resolutionValueForGrid(grid));
    return;
  }
  const widthId = grid.dataset.resolutionWidthId || grid.dataset.controlId;
  const heightId = grid.dataset.resolutionHeightId;
  if (grid.dataset.resolutionWidthId && grid.dataset.resolutionHeightId) {
    state.parameters[widthId] = width;
    state.parameters[heightId] = height;
    state.explicitParameterIds.add(widthId);
    state.explicitParameterIds.add(heightId);
    delete state.serverFieldErrors[widthId];
    delete state.serverFieldErrors[heightId];
  } else {
    state.parameters[widthId] = { ...(state.parameters[widthId] || {}), width, height };
    state.explicitParameterIds.add(widthId);
    delete state.serverFieldErrors[widthId];
  }
  state.formError = null;
  updateResolutionUi(grid, resolutionValueForGrid(grid));
  syncResolutionPresetSelect(grid.closest("[data-resolution-pair-block], [data-control-block]"), resolutionValueForGrid(grid));
  persistActiveParameterState();
  syncParameterValidation(widthId);
  if (heightId) syncParameterValidation(heightId);
}

function applyResolutionPreset(select) {
  const block = select.closest("[data-resolution-pair-block], [data-control-block]");
  const grid = block?.querySelector("[data-resolution-grid]");
  if (!block || !grid || select.value === "custom") return;
  const [widthRaw, heightRaw] = select.value.split("x");
  const width = Number(widthRaw);
  const height = Number(heightRaw);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return;
  commitResolutionValue(grid, width, height);
  commitRecentResolutionValue(grid);
  syncRecentResolutionsRow(grid, resolutionValueForGrid(grid));
}

function applyRecentResolution(button) {
  const block = button.closest("[data-resolution-pair-block], [data-control-block]");
  const grid = block?.querySelector("[data-resolution-grid]");
  if (!block || !grid) return;
  const [widthRaw, heightRaw] = (button.dataset.resolutionRecentValue || "").split("x");
  const width = Number(widthRaw);
  const height = Number(heightRaw);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return;
  commitResolutionValue(grid, width, height);
  commitRecentResolutionValue(grid);
  syncRecentResolutionsRow(grid, resolutionValueForGrid(grid));
}

function removeRecentResolutionEntry(button) {
  const block = button.closest("[data-resolution-pair-block], [data-control-block]");
  const grid = block?.querySelector("[data-resolution-grid]");
  if (!block) return;
  const badge = button.closest("[data-resolution-recent-value]");
  const [widthRaw, heightRaw] = ((badge || button).dataset.resolutionRecentValue || "").split("x");
  if (grid?.closest("#gallery-rerun-dialog")) rerunResolutionContext(grid)?.remove(Number(widthRaw), Number(heightRaw));
  else {
    state.recentResolutions = removeRecentResolution(state.recentResolutions, Number(widthRaw), Number(heightRaw));
    persistRecentResolutions();
  }
  syncRecentResolutionsRow(grid, resolutionValueForGrid(grid));
}

function recentResolutionsUserId() {
  return state.session?.user?.id || "anonymous";
}

function loadRecentResolutionsForActiveSource() {
  if (recentResolutionsRecordTimer) {
    clearTimeout(recentResolutionsRecordTimer);
    recentResolutionsRecordTimer = null;
  }
  if (!state.activeSourceKey) {
    state.recentResolutions = [];
    return;
  }
  let raw = null;
  try {
    raw = window.localStorage.getItem(recentResolutionKey(recentResolutionsUserId(), state.activeSourceKey));
  } catch {
    raw = null;
  }
  state.recentResolutions = state.recentResolutionsBySource[state.activeSourceKey] || loadRecentResolutions(raw);
}

function persistRecentResolutions() {
  if (state.activeSourceKey) state.recentResolutionsBySource[state.activeSourceKey] = structuredClone(state.recentResolutions);
  settingsSync?.schedule();
  if (!state.activeSourceKey) {
    state.recentResolutions = [];
    return;
  }
  try {
    window.localStorage.setItem(
      recentResolutionKey(recentResolutionsUserId(), state.activeSourceKey),
      JSON.stringify(state.recentResolutions),
    );
  } catch {
    // Storage may be unavailable; the in-memory list still works for this session.
  }
}

function commitRecentResolutionValue(grid) {
  if (grid?.closest("#gallery-rerun-dialog")) {
    rerunResolutionContext(grid)?.record(resolutionValueForGrid(grid));
    return;
  }
  if (!grid || !state.activeSourceKey) return;
  state.recentResolutions = recordRecentResolution(state.recentResolutions, resolutionValueForGrid(grid));
  persistRecentResolutions();
}

// Continuous edits (typing, arrow-key nudges) settle into one recent entry:
// the record is deferred until the last change of the burst.
function queueRecentResolutionRecord(grid) {
  if (!grid) return;
  if (grid.closest("#gallery-rerun-dialog")) {
    rerunResolutionContext(grid)?.queueRecord(resolutionValueForGrid(grid), () => {
      if (grid.isConnected) syncRecentResolutionsRow(grid, resolutionValueForGrid(grid));
    });
    return;
  }
  if (recentResolutionsRecordTimer) clearTimeout(recentResolutionsRecordTimer);
  recentResolutionsRecordTimer = setTimeout(() => {
    recentResolutionsRecordTimer = null;
    if (!state.activeSourceKey) return;
    commitRecentResolutionValue(grid);
    syncRecentResolutionsRow(grid, resolutionValueForGrid(grid));
  }, 600);
}

function syncRecentResolutionsRow(grid, value) {
  const block = grid?.closest("[data-resolution-pair-block], [data-control-block]");
  const editor = block?.querySelector(".resolution-editor");
  if (!block || !editor) return;
  const recent = grid.closest("#gallery-rerun-dialog") ? rerunResolutionContext(grid)?.recent || [] : state.recentResolutions;
  const html = recentResolutionsMarkup(recent, value);
  const existing = block.querySelector("[data-resolution-recent]");
  if (!html) {
    existing?.remove();
    return;
  }
  const template = document.createElement("template");
  template.innerHTML = html;
  const node = template.content.firstElementChild;
  if (existing) existing.replaceWith(node);
  else editor.prepend(node);
}

function syncResolutionPresetSelect(block, value) {
  const select = block?.querySelector("[data-resolution-preset]");
  if (!select) return;
  const preset = resolutionPresetForValue(value);
  if (preset) {
    select.value = preset.key;
    return;
  }
  let custom = select.querySelector('option[value="custom"]');
  if (!custom) {
    custom = document.createElement("option");
    custom.value = "custom";
    select.prepend(custom);
  }
  if (value?.width && value?.height) {
    custom.textContent = `Custom (${value.width} × ${value.height})`;
  }
  select.value = "custom";
}

function renderPanelWithResolutionFocus(grid, handle) {
  if (grid.closest("#gallery-rerun-dialog")) {
    updateResolutionUi(grid, resolutionValueForGrid(grid));
    syncResolutionPresetSelect(grid.closest("[data-resolution-pair-block], [data-control-block]"), resolutionValueForGrid(grid));
    syncRecentResolutionsRow(grid, resolutionValueForGrid(grid));
    grid.querySelector(`[data-resolution-handle="${handle}"]`)?.focus({ preventScroll: true });
    return;
  }
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
  if (id === positivePromptInput(sourceInterface(state.activeSource))?.id) state.promptEditorDirty = true;
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
    collapseActiveModelSelection(control.id, state.parameters[id]);
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
  const block = element.closest("[data-control-block], [data-prompt-generator-block]");
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
    ...validateImageParameters(contract, state.parameters),
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
  syncServerControls();
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

  for (const element of block.querySelectorAll(
    "[data-control-id]:not([data-resolution-grid]), [data-lora-strength]",
  )) {
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
  // Keep unresolved account-scoped receipts for recovery on the next sign-in.
  setSubmissionOwner(null);
  state.pendingSubmission = null;
  stopLiveUpdates();
  stopApplicationStartup();
  state.comfyuiInstances = [];
  state.comfyuiImagePool = null;
  state.comfyuiImagePoolInstanceIds = [];
  state.defaultComfyuiInstanceId = null;
  state.textComfyuiInstanceId = null;
  state.comfyuiInstancesStatus = "idle";
  state.comfyuiInstancesMessage = null;
  state.comfyuiInstanceConfigurationMode = null;
  state.comfyuiInstanceError = null;
  state.comfyuiInstanceWarning = null;
  state.sources = [];
  state.activeSourceKey = null;
  state.activeSource = null;
  state.recentResolutions = [];
  state.sourcePickerDialogOpen = false;
  state.sourcePickerDraft = null;
  state.checkpointTiers = {};
  state.excludedCheckpointRanks = [];
  checkpointRankChange = null;
  state.loraTiers = {};
  state.excludedLoraRanks = [];
  loraRankChange = null;
  state.modelSelectionsBySourceRevision = new Map();
  state.selectedGenerationTargetCount = 0;
  checkpointTiersRevision += 1;
  state.parameters = {};
  state.loraImages = {};
  state.loraStrengthMemory = {};
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
  pendingGenerationIds.clear();
  state.generationActivity = null;
  state.generationSubmissionProgress = null;
  state.generationActivityUnavailable = false;
  state.nextCursor = null;
  state.collections = [];
  state.collectionsStatus = "idle";
  state.collectionsMessage = null;
  setGalleryRoute(null);
  collectionNavigationToken += 1;
  startupGalleryBoundary = null;
  state.galleryStatus = "idle";
  state.galleryMessage = null;
  state.autoGenerate = false;
  state.maxAutoGenerations = 200;
  state.pendingAutoEnabled = undefined;
  state.recentResolutionsBySource = {};
  state.sharedSettingsStatus = "loading";
  state.automation = null;
  state.automationLoaded = false;
  state.autoGenerateStatusMessage = "Checking auto generation…";
  state.autoGenerateCreativeDirection = false;
  clearAutoGeneratePin();
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
  document.title = state.session?.app_title || "ImageGen";
  root.innerHTML = loginMarkup(state.session?.app_title || "ImageGen");
  queueMicrotask(() => root.querySelector("input")?.focus());
}

function renderPasswordChange(forced) {
  stopLiveUpdates();
  stopApplicationStartup();
  document.title = state.session?.app_title || "ImageGen";
  root.innerHTML = passwordChangeMarkup(state.session?.app_title || "ImageGen", forced);
  queueMicrotask(() => root.querySelector("input")?.focus());
}

async function enterApplication() {
  setSubmissionOwner(state.session.user.id);
  state.pendingSubmission = pendingSubmission();
  stopLiveUpdates();
  stopApplicationStartup();
  if (!window.location.hash) window.history.replaceState(null, "", "#/");
  const controller = new AbortController();
  applicationStartupController = controller;
  state.comfyuiInstances = [];
  state.comfyuiImagePool = null;
  state.comfyuiImagePoolInstanceIds = [];
  state.defaultComfyuiInstanceId = null;
  state.textComfyuiInstanceId = null;
  state.comfyuiInstancesStatus = "loading";
  state.comfyuiInstancesMessage = null;
  state.comfyuiInstanceConfigurationMode = null;
  state.comfyuiInstanceError = null;
  state.comfyuiInstanceWarning = null;
  state.sourceCatalogStatus = "loading";
  state.sourceCatalogMessage = null;
  state.services = [];
  state.servicesStatus = "loading";
  state.servicesMessage = null;
  state.generations = [];
  pendingGenerationIds.clear();
  state.generationActivity = null;
  state.generationSubmissionProgress = null;
  state.generationActivityUnavailable = false;
  state.nextCursor = null;
  state.collections = [];
  state.collectionsStatus = "loading";
  state.collectionsMessage = null;
  setGalleryRoute(collectionIdFromHash());
  state.galleryLayout = "grouped";
  state.favoritesMode = "all";
  state.excludedCheckpointRanks = [];
  state.excludedLoraRanks = [];
  state.galleryStatus = "loading";
  state.galleryMessage = null;
  state.autoGenerate = false;
  state.maxAutoGenerations = 200;
  state.pendingAutoEnabled = undefined;
  state.recentResolutionsBySource = {};
  state.sharedSettingsStatus = "loading";
  state.automation = null;
  state.automationLoaded = false;
  state.autoGenerateStatusMessage = "Checking auto generation…";
  state.autoGenerateCreativeDirection = false;
  state.generationQuantity = loadGenerationQuantity();
  clearAutoGeneratePin();
  state.checkpointTiers = {};
  checkpointRankChange = null;
  checkpointTiersRevision += 1;
  state.loraTiers = {};
  loraRankChange = null;
  state.parameterStateBySource = normalizeStoredParameterState(
    readStoredItem(parameterStateStorageKey(sessionStorageUserId())),
  );
  state.loraImages = {};
  state.loraStrengthMemory = {};
  state.activeSourceKey = normalizeStoredActiveSource(
    readStoredItem(activeSourceStorageKey(sessionStorageUserId())),
  );
  state.controlSectionOpen = normalizeStoredControlSections(
    readStoredItem(controlSectionStorageKey(sessionStorageUserId())),
  );
  const creativeDirectionDraft = normalizeStoredCreativeDirectionDraft(
    readStoredItem(creativeDirectionStorageKey(sessionStorageUserId())),
  );
  state.promptAssistant = {
    ...state.promptAssistant,
    creativeDirection: creativeDirectionDraft.creativeDirection,
    mode: creativeDirectionDraft.mode,
    think: creativeDirectionDraft.think,
    instructionOverrides: loadPromptInstructions(),
    available: false,
    message: "Checking Prompt Assistant availability…",
    error: null,
    visionAvailable: undefined,
  };
  state.expectations = normalizeExpectationSettings(null);
  state.expectationCheck = null;
  state.expectationCheckStarting = false;
  state.expectationStatusDismissedId = readStoredItem(expectationDismissedStorageKey());
  expectationStarts.clear();
  state.speechToText = {
    available: false,
    message: "Checking voice input availability…",
  };
  restoreBrowserDraft();
  submissionRecovery = createSubmissionRecovery({
    signal: controller.signal,
    onRecovered: applyRecoveredSubmission,
    onError: (error, pending) => {
      if (["/api/prompt-generations", "/api/generation-preparations"].includes(pending.path)) state.promptGenerationError = error.message;
      else if (pending.path === "/api/prompt-assistant/checks") state.promptAssistant.error = error.message;
      else state.formError = error.message;
      renderPanel();
    },
    onChange: (pending) => {
      state.submissionRecoveryPending = pending;
      syncGenerationSubmissionState();
    },
  });
  window.addEventListener("online", () => submissionRecovery?.start({ immediate: true }), { signal: controller.signal });
  root.innerHTML = shellMarkup(state);
  observeTopbarHeight();
  promptRerunProgress = createPromptRerunProgress(root.querySelector("#prompt-rerun-progress-host"), {
    api, context: () => ({ collectionId: state.currentCollectionId }),
    changed: scheduleActivityRefresh, notify: toast, signal: controller.signal,
  });
  expectationChecks = createExpectationCheck(root.querySelector("#expectation-check-dialog"), {
    api,
    signal: controller.signal,
    currentPrompt: currentPromptValue,
    sourceName: (check) => expectationStarts.get(check?.id)?.sourceName || null,
    onChange: handleExpectationCheckChange,
    applyPrompt: (prompt, attempt) => {
      if (applyExpectationPrompt(prompt)) toast(`Attempt ${attempt.number}'s prompt was placed in the Prompt field.`, "success");
    },
    deleteGenerations: deleteExpectationAttemptImages,
    openImage: openExpectationAttemptImage,
    notify: toast,
    onClose: () => {
      const target = document.querySelector('#expectation-status:not([hidden]) [data-action="view-expectation-check"]') ||
        document.querySelector("#prompt-assistant [data-action=compose-prompt]");
      if (!document.querySelector("dialog[open]")) target?.focus({ preventScroll: true });
    },
  });
  disposeThumbnails = installThumbnails(document.querySelector("#gallery-viewport"));
  document.querySelector("#photo-viewer")?.addEventListener("close", () => {
    if (!document.querySelector("#photo-viewer")?.open) resetPhotoViewerState();
  });
  document.querySelector("#detail-dialog")?.addEventListener("close", (event) => {
    if (event.target.open) return;
    for (const image of event.target.querySelectorAll("img")) image.removeAttribute("src");
    event.target.replaceChildren();
    delete event.target.dataset.generationId;
  });
  document.querySelector("#prompt-editor-dialog")?.addEventListener("close", handlePromptEditorClose);
  document
    .querySelector("#source-picker-dialog")
    ?.addEventListener("close", handleSourcePickerDialogClose);
  document.querySelector("#source-picker-dialog")?.addEventListener("cancel", (event) => {
    if (state.sourcePickerDraft?.savingRanks) event.preventDefault();
  });
  document
    .querySelector("#collection-dialog")
    ?.addEventListener("close", handleCollectionDialogClose);
  document
    .querySelector("#collection-delete-dialog")
    ?.addEventListener("close", handleCollectionDeleteDialogClose);
  document
    .querySelector("#move-dialog")
    ?.addEventListener("close", handleMoveDialogClose);
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
  const preferencesRequest = loadStartupPreferences(controller.signal);
  const promptContextReady = preferencesRequest.then(async () => {
    await Promise.allSettled([
      loadSources({ signal: controller.signal, diagnostic: true }),
      loadPromptGenerators(controller.signal),
    ]);
    if (!controller.signal.aborted) { promptJobsReady = true; await refreshPromptJobs(); }
  });
  const requests = [
    submissionRecovery.start(),
    preferencesRequest,
    refreshAutoGeneration(),
    loadCollections(controller.signal),
    refreshGenerationActivity(),
    servicesRequest,
    preferencesRequest.then(() => loadStartupComfyuiInstances(controller.signal)),
    galleryRequest,
    loadStartupPromptAssistant(controller.signal),
    loadStartupSpeechToText(controller.signal),
    promptContextReady,
    // Restores the sidebar status line and re-enters a check that is still running.
    expectationChecks.refresh(),
  ];
  void Promise.allSettled(requests);
  userStateTimer = window.setInterval(() => void refreshUserState(), 15_000);
  promptJobTimer = window.setInterval(() => void refreshPromptJobs(), 1500);
}

function stopApplicationStartup() {
  closePhotoViewer();
  disposeThumbnails?.();
  disposeThumbnails = null;
  cancelGalleryReads();
  collectionsRequestToken += 1;
  clearInterval(promptJobTimer);
  promptJobTimer = null;
  clearInterval(userStateTimer);
  userStateTimer = null;
  settingsSync = null;
  autoSettingsSync = null;
  state.autoSettingsSaving = false;
  state.autoSettingsStatus = "saved";
  state.autoSettingsMessage = null;
  automationReadToken += 1;
  applicationStartupController?.abort();
  applicationStartupController = null;
  submissionRecovery = null;
  promptJobsReady = false;
  state.submitting = false;
  state.promptGenerationRequest = null;
  state.submissionRecoveryPending = false;
  promptJobPhases.clear();
  promptJobSeen.clear();
  syncServerControls();
}

function requestWasAborted(error, signal) {
  return Boolean(signal?.aborted || error?.name === "AbortError");
}

async function loadStartupPreferences(signal = applicationStartupController?.signal) {
  settingsInterfaces.clear();
  autoSettingsSync = createAutoGenerationSync({ api, current: () => state.automation,
    canSave: () => !state.automationBusy,
    read: () => {
      if (state.sharedSettingsStatus === "conflict") throw new Error("Resolve the shared settings conflict before updating auto generation.");
      if (!sourceInterface(state.activeSource) || state.sourceDetailLoading || state.sourceDetailError) throw new Error("Wait for the workflow settings to load.");
      const errors = validateImageParameters(sourceInterface(state.activeSource), state.parameters);
      if (Object.keys(errors).length || document.querySelector("#auto-generate-limit:invalid")) throw new Error("Review the highlighted controls before updating auto generation.");
      return automationSnapshot();
    },
    apply: applyAutoGenerationState,
    status: (status, message) => { state.autoSettingsStatus = status; state.autoSettingsMessage = message; syncServerControls(); },
    saving: (saving) => { state.autoSettingsSaving = saving; automationReadToken += 1; syncServerControls(); },
    signal, storage: localStorage, storageKey: `cif.auto-settings.v1.${sessionStorageUserId()}` });
  settingsSync = createSettingsSync({ api, read: captureSharedSettings, apply: applySharedSettings,
    storage: { getItem: (key) => localStorage.getItem(key), setItem: (key, value) => localStorage.setItem(key, value) },
    storageKey: `cif.control-panel.v1.${sessionStorageUserId()}`, normalize: normalizePanelSettings,
    prepareMerge: prepareSettingsInterfaces,
    status: (status, message = null) => {
      state.sharedSettingsStatus = status;
      state.sharedSettingsMessage = message;
      syncServerControls();
    }, signal });
  try { await settingsSync.load(); }
  catch (error) {
    if (signal?.aborted) return;
    state.sharedSettingsStatus = "error";
    state.sharedSettingsMessage = error.message;
    syncServerControls();
  }
}

function normalizedCheckpointTiers(value) {
  return normalizeCheckpointRanks(value);
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

async function loadStartupComfyuiInstances(
  signal = applicationStartupController?.signal,
) {
  return loadComfyuiInstances({ signal, diagnostic: true });
}

async function loadComfyuiInstances(
  { signal, diagnostic = false, showLoading = true } = {},
) {
  const previousPanelState = comfyuiInstancePanelState();
  if (showLoading) {
    state.comfyuiInstancesStatus = "loading";
    state.comfyuiInstancesMessage = null;
    renderPanel();
    renderServiceBanner();
  }
  try {
    const options = {
      operation: "ComfyUI runtime status",
      deadlineMs: STARTUP_DEADLINES.comfyuiInstances,
      signal,
    };
    const payload = diagnostic
      ? await startupGet("/api/comfyui-instances", options)
      : await api("/api/comfyui-instances", options);
    if (signal?.aborted) return;
    applyComfyuiInstanceCatalog(payload);
    state.comfyuiInstancesStatus = "ready";
    state.comfyuiInstancesMessage = null;
    // Discovery may recover after startup returned an empty or unavailable CPU catalog.
    if (!showLoading && state.textComfyuiInstanceId &&
        (!state.promptGeneratorSources.length || state.promptGeneratorLoadError) &&
        state.comfyuiInstances.some((item) => item.id === state.textComfyuiInstanceId && item.available)) {
      await loadPromptGenerators(signal);
    }
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    state.comfyuiInstancesStatus = "error";
    state.comfyuiInstancesMessage =
      error.message || "ComfyUI runtime status is temporarily unavailable.";
  }
  if (showLoading || previousPanelState !== comfyuiInstancePanelState()) {
    if (state.sourcePickerDialogOpen) state.servicePanelRefreshPending = true;
    else renderPanel();
    renderServiceBanner();
  }
}

function applyComfyuiInstanceCatalog(payload) {
  const items = Array.isArray(payload?.items)
    ? payload.items.map(normalizeComfyuiInstance).filter(Boolean)
    : [];
  state.comfyuiInstances = items;
  state.comfyuiImagePool = normalizeWorkerPool(payload?.image_pool);
  state.comfyuiImagePoolInstanceIds = Array.isArray(payload?.image_pool_instance_ids)
    ? payload.image_pool_instance_ids.filter((value) => typeof value === "string")
    : [];
  state.defaultComfyuiInstanceId = payload?.default_instance_id || null;
  state.textComfyuiInstanceId = payload?.text_instance_id || null;
  state.comfyuiInstanceConfigurationMode =
    payload?.configuration_mode === "legacy"
      ? "legacy"
      : payload?.configuration_mode === "explicit"
        ? "explicit"
        : null;
  const selected = imageComfyuiInstance();
  if (imagePoolWorkers(state).some((item) => item.available)) {
    const previousInstanceError = state.comfyuiInstanceError;
    state.comfyuiInstanceError = null;
    state.comfyuiInstanceWarning = null;
    if (previousInstanceError && state.formError === previousInstanceError) {
      state.formError = null;
    }
  }
}

function normalizeComfyuiInstance(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const id = typeof value.id === "string" ? value.id.trim() : "";
  if (!id) return null;
  const label = typeof value.label === "string" ? value.label.trim() : "";
  return {
    id,
    label: label || id,
    description:
      typeof value.description === "string" ? value.description.trim() : "",
    is_default: Boolean(value.is_default),
    available: value.available === true,
    role: ["image", "text", "unused"].includes(value.role) ? value.role : "unused",
    // A server that predates pooled execution reports no membership; its
    // assigned image instance is inferred from default_instance_id below.
    in_image_pool: value.in_image_pool === true,
    busy: value.busy === true,
    message: typeof value.message === "string" ? value.message.trim() : "",
    checked_at:
      typeof value.checked_at === "string" ? value.checked_at : null,
  };
}

function normalizeWorkerPool(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const count = (key) => Math.max(0, Number(value[key]) || 0);
  return {
    worker_count: count("worker_count"),
    available_count: count("available_count"),
    idle_count: count("idle_count"),
    busy_count: count("busy_count"),
    free_slot_count: count("free_slot_count"),
    unassigned_queued_count: count("unassigned_queued_count"),
  };
}

function imageComfyuiInstance() {
  return (
    state.comfyuiInstances.find(
      (item) => item.id === state.defaultComfyuiInstanceId,
    ) || null
  );
}

function collectionIdFromHash(hash = window.location.hash) {
  const value = String(hash || "");
  if (!value || value === "#" || value === "#/") return null;
  const match = value.match(/^#\/c\/([^/?#]+)$/u);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

function collectionRouteHash(collectionId) {
  return collectionId ? `#/c/${encodeURIComponent(collectionId)}` : "#/";
}

function currentGalleryRoute() {
  return state.currentCollectionId;
}

function setGalleryRoute(route) {
  state.currentCollectionId = route;
  state.gallerySkippedCursor = null;
}

function galleryNextCursor() {
  return state.nextCursor;
}

function visibleGenerations() {
  return galleryFilterActive(state)
    ? state.generations.filter((generation) => galleryGenerationMatches(state, generation))
    : state.generations;
}

function currentViewHidesThumbnails() {
  return collectionThumbnailsHidden(state.collections, state.currentCollectionId);
}

function generationBelongsToView(generation) {
  return generation.collection_id === state.currentCollectionId;
}

function openCollectionRoute(collectionId) {
  const nextHash = collectionRouteHash(collectionId);
  if (window.location.hash === nextHash) {
    void navigateCollectionView(collectionId);
    return;
  }
  window.history.pushState(null, "", nextHash);
  void navigateCollectionView(collectionId);
}

function handleCollectionHashChange() {
  if (!state.session?.authenticated || !document.querySelector("#collection-bar")) return;
  const collectionId = collectionIdFromHash();
  if (window.location.hash && window.location.hash !== collectionRouteHash(collectionId)) {
    window.history.replaceState(null, "", collectionRouteHash(collectionId));
  }
  void navigateCollectionView(collectionId);
}

async function navigateCollectionView(collectionId) {
  closePhotoViewer();
  const token = ++collectionNavigationToken;
  cancelGalleryReads();
  state.observer?.disconnect();
  for (const generation of state.generations) {
    if (!TERMINAL_GENERATION_STATUSES.has(generation.status)) pendingGenerationIds.add(generation.id);
  }
  setGalleryRoute(collectionId);
  promptRerunProgress?.navigate();
  state.generations = [];
  state.loadingMore = false;
  state.nextCursor = null;
  startupGalleryBoundary = null;
  state.galleryStatus = "loading";
  state.galleryMessage = null;
  renderCollectionBarHost();
  renderGallery();
  await loadCollections();
  if (token !== collectionNavigationToken) return;
  await loadStartupGallery(undefined, { navigationToken: token });
}

async function loadCollections(signal = applicationStartupController?.signal) {
  const token = ++collectionsRequestToken;
  state.collectionsStatus = "loading";
  state.collectionsMessage = null;
  renderCollectionBarHost();
  try {
    const collections = await startupGet("/api/collections", {
      operation: "Collections",
      deadlineMs: STARTUP_DEADLINES.collections,
      signal,
    });
    if (signal?.aborted || token !== collectionsRequestToken) return;
    state.collections = Array.isArray(collections) ? collections : [];
    applyCollectionActivity({ counts: false });
    scheduleActivityRefresh();
    state.collectionsStatus = "ready";
    state.collectionsMessage = null;
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    if (token !== collectionsRequestToken) return;
    state.collectionsStatus = "error";
    state.collectionsMessage = error.message || "Collections are temporarily unavailable.";
    toast(state.collectionsMessage, "error");
  }
  renderCollectionBarHost();
  renderGallery();
}

function renderCollectionBarHost() {
  const host = document.querySelector("#collection-bar-host");
  if (!host) return;
  host.innerHTML = renderCollectionBar(state.collections, state.currentCollectionId, {
    collectionsStatus: state.collectionsStatus,
  });
  syncFavoritesFilterControl();
}

// One button cycles three states. The accessible name stays "Favorites" so the
// control keeps one identity; aria-pressed carries the tri-state, while the title
// and the visible label say which way the view is filtered.
function syncFavoritesFilterControl() {
  const button = document.querySelector('[data-action="toggle-favorites-filter"]');
  if (!button) return;
  const mode = favoritesMode(state);
  const presentation = favoritesFilterPresentation(mode);
  button.dataset.favoritesMode = mode;
  button.setAttribute("aria-pressed", presentation.pressed);
  button.setAttribute("title", presentation.title);
  const label = button.querySelector(".favorites-launch-label");
  if (label && label.textContent !== presentation.label) label.textContent = presentation.label;
}

function galleryPageUrl(cursor = null, collectionId = currentGalleryRoute()) {
  const parameters = new URLSearchParams({ limit: "24" });
  if (cursor) parameters.set("cursor", cursor);
  parameters.set("collection_id", collectionId || "");
  for (const [key, value] of galleryViewParameters(state)) {
    if (key !== "collection_id" && value !== "false") parameters.append(key, value);
  }
  return `/api/generations?${parameters.toString()}`;
}

function comfyuiInstancePanelState() {
  return JSON.stringify({
    status: state.comfyuiInstancesStatus,
    message: state.comfyuiInstancesMessage,
    pool: state.comfyuiImagePool,
    configurationMode: state.comfyuiInstanceConfigurationMode,
    textDefault: state.textComfyuiInstanceId,
    textRuntime: promptRuntimeId(state),
    selectedId: state.defaultComfyuiInstanceId,
    error: state.comfyuiInstanceError,
    warning: state.comfyuiInstanceWarning,
    items: state.comfyuiInstances.map((item) => ({
      id: item.id,
      label: item.label,
      description: item.description,
      available: item.available,
      message: item.message,
      isDefault: item.is_default,
      role: item.role,
      inImagePool: item.in_image_pool,
    })),
  });
}

function cancelGalleryReads() {
  galleryHistoryController?.abort();
  galleryPageController?.abort();
  galleryHistoryController = null;
  galleryPageController = null;
  galleryPageRequest = null;
}

function galleryReadController(parentSignal = applicationStartupController?.signal) {
  const controller = new AbortController();
  const abort = () => controller.abort(parentSignal.reason);
  if (parentSignal?.aborted) abort();
  else parentSignal?.addEventListener("abort", abort, { once: true });
  return { controller, unlink: () => parentSignal?.removeEventListener("abort", abort) };
}

async function loadStartupGallery(
  signal = applicationStartupController?.signal,
  { navigationToken = collectionNavigationToken, allowFallback = true } = {},
) {
  const requestCollectionId = currentGalleryRoute();
  const parentSignal = signal;
  galleryHistoryController?.abort();
  const { controller, unlink } = galleryReadController(parentSignal);
  galleryHistoryController = controller;
  signal = controller.signal;
  const changed = state.galleryStatus !== "loading" || state.galleryMessage !== null;
  state.galleryStatus = "loading";
  state.galleryMessage = null;
  if (changed) renderGallery();
  try {
    const page = await startupGet(galleryPageUrl(null, requestCollectionId), {
      operation: "Gallery history",
      deadlineMs: STARTUP_DEADLINES.gallery,
      signal,
    });
    if (
      signal?.aborted ||
      navigationToken !== collectionNavigationToken ||
      requestCollectionId !== currentGalleryRoute()
    )
      return;
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
    if (
      allowFallback &&
      requestCollectionId &&
      error.status === 404 &&
      navigationToken === collectionNavigationToken
    ) {
      toast("That collection is not available. Returning Home.", "error");
      window.history.replaceState(null, "", "#/");
      state.currentCollectionId = null;
      state.generations = [];
      state.nextCursor = null;
      await loadCollections(signal);
      if (signal.aborted || navigationToken !== collectionNavigationToken) return;
      return loadStartupGallery(parentSignal, {
        navigationToken,
        allowFallback: false,
      });
    }
    if (
      navigationToken !== collectionNavigationToken ||
      requestCollectionId !== currentGalleryRoute()
    )
      return;
    startupGalleryBoundary = null;
    state.galleryStatus = "error";
    state.galleryMessage = error.message || "Gallery history is temporarily unavailable.";
  } finally {
    unlink();
    if (galleryHistoryController === controller) galleryHistoryController = null;
  }
  renderGallery();
  setupPaginationObserver();
  syncServerControls();
}

async function loadStartupPromptAssistant(signal = applicationStartupController?.signal, { quiet = false } = {}) {
  try {
    const assistant = await startupGet("/api/prompt-assistant/status", {
      operation: "Prompt Assistant status",
      deadlineMs: STARTUP_DEADLINES.promptAssistant,
      signal,
    });
    if (signal?.aborted) return;
    const previous = state.promptAssistant;
    state.promptAssistant = {
      ...state.promptAssistant,
      available: Boolean(assistant.available),
      message: assistant.message,
      defaultInstructions: assistant.default_instructions || {},
      visionAvailable: Boolean(assistant.vision_available),
    };
    if (quiet && previous.available === state.promptAssistant.available && previous.visionAvailable === state.promptAssistant.visionAvailable) return;
  } catch (error) {
    if (requestWasAborted(error, signal)) return;
    if (quiet) return;
    state.promptAssistant = {
      ...state.promptAssistant,
      available: false,
      message: error.message || "Prompt Assistant is temporarily unavailable.",
      visionAvailable: false,
    };
  }
  // A background refresh must not replace a field the user is editing.
  if (quiet && document.querySelector("#generation-panel")?.contains(document.activeElement) && document.activeElement.matches("input, textarea, select")) {
    syncPromptAssistantAction();
    syncServerControls();
    return;
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

function modelSelectionStoreKey(source) {
  const key = sourceKey(source);
  const revision = sourceRevision(source);
  if (!key || !revision) return null;
  return JSON.stringify([
    key,
    revision.publication_id || "",
    revision.workflow_sha256 || "",
    revision.api_sha256 || "",
    revision.manifest_sha256 || "",
  ]);
}

function modelSelectionsForSource(source) {
  const storeKey = modelSelectionStoreKey(source);
  const stored = storeKey ? state.modelSelectionsBySourceRevision.get(storeKey) : undefined;
  const activeFallback =
    stored === undefined &&
    sourceKey(source) === state.activeSourceKey &&
    state.activeSource &&
    revisionsMatch(source, state.activeSource)
      ? state.parameters
      : {};
  return normalizeSourceModelSelections(source, stored || {}, activeFallback);
}

function setModelSelectionsForSource(source, selections) {
  const storeKey = modelSelectionStoreKey(source);
  const normalized = normalizeSourceModelSelections(source, selections);
  if (storeKey) {
    settingsSync?.schedule();
    state.modelSelectionsBySourceRevision.set(storeKey, structuredClone(normalized));
  }
  return normalized;
}

function collapseActiveModelSelectionsFromParameters() {
  const source = state.activeSource;
  if (!source || !sourceModelSelectors(source).length) return;
  const selections = Object.fromEntries(
    sourceModelSelectors(source).map((selector) => [
      selector.parameter_id,
      typeof state.parameters[selector.parameter_id] === "string"
        ? [state.parameters[selector.parameter_id]]
        : [],
    ]),
  );
  setModelSelectionsForSource(source, selections);
}

function collapseActiveModelSelection(parameterId, value) {
  const source = state.activeSource;
  if (
    !sourceModelSelectors(source).some(
      (selector) => selector.parameter_id === parameterId,
    ) ||
    typeof value !== "string"
  ) {
    return false;
  }
  const current = modelSelectionsForSource(source);
  setModelSelectionsForSource(source, { ...current, [parameterId]: [value] });
  return true;
}

function applyStoredModelSelectionsToActiveParameters() {
  const source = state.activeSource;
  const contract = sourceInterface(source);
  if (!source || !contract) return;
  const selections = modelSelectionsForSource(source);
  let changed = false;
  for (const selector of sourceModelSelectors(source)) {
    const input = interfaceInputs(contract).find(
      (candidate) =>
        candidate.id === selector.parameter_id && candidate.type === "choice",
    );
    if (!input) continue;
    const allowed = new Set(choiceOptions(input).map((choice) => choice.value));
    const selected = (selections[selector.parameter_id] || []).filter((value) =>
      allowed.has(value),
    );
    if (!selected.length) continue;
    const current = state.parameters[selector.parameter_id];
    const canonical = selected.includes(current) ? current : selected[0];
    state.parameters[selector.parameter_id] = canonical;
    state.explicitParameterIds.add(selector.parameter_id);
    delete state.serverFieldErrors[selector.parameter_id];
    if (canonical !== current) {
      state.parameters = applyChoiceStrengthDefaults(
        contract,
        state.parameters,
        state.explicitParameterIds,
        selector.parameter_id,
      );
      changed = true;
    }
  }
  if (changed || sourceModelSelectors(source).length) persistActiveParameterState();
}

function modelParameterVariantsForSource(source, contract = null) {
  const variants = sourceModelParameterVariants(source, modelSelectionsForSource(source));
  if (!contract || !sourceModelSelectors(source).length) return variants;
  const inputs = new Map(interfaceInputs(contract).map((input) => [input.id, input]));
  for (const variant of variants) {
    for (const [parameterId, value] of Object.entries(variant)) {
      const input = inputs.get(parameterId);
      if (
        input?.type !== "choice" ||
        !choiceOptions(input).some((choice) => choice.value === value)
      ) {
        throw new Error(
          `${source.display_name || "Generation source"} has model choices that no longer match its published interface. Refresh generation sources and choose again.`,
        );
      }
    }
  }
  return variants;
}

function orderedModelParameterVariants(source, contract, preferredValues = {}) {
  const variants = modelParameterVariantsForSource(source, contract);
  const parameterId = sourceModelSelectors(source)[0]?.parameter_id;
  const preferred = parameterId ? preferredValues?.[parameterId] : undefined;
  if (typeof preferred !== "string") return variants;
  return [...variants].sort(
    (first, second) =>
      Number(second[parameterId] === preferred) - Number(first[parameterId] === preferred),
  );
}

function selectedGenerationSource() {
  if (!state.activeSourceKey || state.activeSource?.available === false) return null;
  const summary = state.sources.find(
    (source) => sourceKey(source) === state.activeSourceKey && source.available !== false,
  );
  if (!summary && !state.activeSource) return null;
  return summary && state.activeSource
    ? { ...summary, ...state.activeSource }
    : state.activeSource || summary;
}

function plannedGenerationTargetCount() {
  const source = selectedGenerationSource();
  return source ? modelParameterVariantsForSource(source).length : 0;
}

// One click of Generate queues the selected quantity for every model
// variant (checkpoint) the active source has selected.
function plannedGenerationTotal() {
  return plannedGenerationTargetCount() * state.generationQuantity;
}

function sourceContextIsCurrent(key, revision) {
  return Boolean(
    key &&
      state.activeSourceKey === key &&
      state.activeSource &&
      revisionsMatch({ revision }, state.activeSource),
  );
}

function generationContextIsCurrent(key, revision, comfyuiInstanceId) {
  return Boolean(
    sourceContextIsCurrent(key, revision) &&
      comfyuiInstanceId &&
      state.defaultComfyuiInstanceId === comfyuiInstanceId,
  );
}

function isComfyuiInstanceError(error) {
  const code = String(error?.code || "");
  return Boolean(
    code === "instance_unavailable" ||
      code.startsWith("comfyui_instance_") ||
      error?.fields?.comfyui_instance_id,
  );
}

async function refreshComfyuiInstancesAfterError(message, expectedInstanceId) {
  await loadComfyuiInstances({ showLoading: false });
  if (state.defaultComfyuiInstanceId !== expectedInstanceId) return;
  state.comfyuiInstanceError =
    message || "The GPU image service is unavailable.";
  state.formError = state.comfyuiInstanceError;
  renderPanel();
  renderServiceBanner();
}

function persistActiveParameterState() {
  if (!state.activeSourceKey || !sourceInterface(state.activeSource)) return;
  state.parameterStateBySource[state.activeSourceKey] = {
    interface: structuredClone(sourceInterface(state.activeSource)),
    revision: structuredClone(sourceRevision(state.activeSource)),
    values: structuredClone(state.parameters),
    lora_strength_memory: structuredClone(state.loraStrengthMemory),
    explicitInputIds: [...state.explicitParameterIds],
    selectedPreset: state.selectedPreset,
  };
  persistParameterState();
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
    state.activeSourceKey = reconcileSourceKey(state.sources, state.activeSourceKey, state.parameterStateBySource);
    state.sourceCatalogStatus = "ready";
    const selected = state.sources.find((item) => sourceKey(item) === state.activeSourceKey);
    if (state.activeSourceKey && !selected && state.parameterStateBySource[state.activeSourceKey]) {
      state.activeSource = null;
      state.sourceDetailLoading = false;
      state.sourceDetailError = "Your saved workflow is currently unavailable. Choose a workflow to continue editing.";
      renderPanel();
      return;
    }
    const next = selected || state.sources.find((item) => item.available !== false) || state.sources[0] || null;
    if (!next) {
      persistActiveParameterState();
      state.sourceLoadToken += 1;
      state.activeSourceKey = null;
      persistActiveSourceKey();
      state.activeSource = null;
      state.recentResolutions = [];
      state.parameters = {};
      state.explicitParameterIds = new Set();
      state.sourceDetailLoading = false;
      state.sourceDetailError = null;
      renderPanel();
      return;
    }
    if (state.activeSource && (sourceKey(next) !== sourceKey(state.activeSource) || !revisionsMatch(next, state.activeSource))) {
      loraManagerController?.invalidateSource(sourceKey(state.activeSource));
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

// Checkpoints follow the shared checkpoint identity (then the public value) into the
// newly loaded source. With nothing in common the source keeps its own selection.
function carryModelSelection(migration, { carrySelections = true } = {}) {
  const source = state.activeSource;
  const contract = sourceInterface(source);
  const selector = sourceModelSelectors(source)[0];
  const previousSelector = sourceModelSelectors(migration?.source)[0];
  if (!selector || !previousSelector) return;
  const primary = mapModelChoiceValue(
    migration.source,
    source,
    migration.values?.[previousSelector.parameter_id],
  );
  const declared = interfaceInputs(contract).some(
    (input) => input.id === selector.parameter_id && input.type === "choice",
  );
  if (primary && declared) {
    const changed = state.parameters[selector.parameter_id] !== primary;
    state.parameters[selector.parameter_id] = primary;
    if ((migration.explicitInputIds || []).includes(previousSelector.parameter_id)) {
      state.explicitParameterIds.add(selector.parameter_id);
    }
    if (changed) {
      state.parameters = applyChoiceStrengthDefaults(
        contract,
        state.parameters,
        state.explicitParameterIds,
        selector.parameter_id,
      );
    }
  }
  if (!carrySelections) return;
  const carried = mapModelSelections(migration.source, migration.modelSelections, source);
  if (carried) setModelSelectionsForSource(source, carried);
}

// Changing the source keeps the control panel: every value the destination accepts
// carries over (inputs, checkpoints, LoRAs, preset, recent resolutions). Values it
// rejects and inputs only it declares use its own last-used values or defaults.
async function selectSource(key, { summary = null, signal, diagnostic = false, carryModelSelections = true } = {}) {
  const canonical = reconcileSourceKey(state.sources, key, state.parameterStateBySource);
  if (canonical !== key) { key = canonical; summary = null; }
  const previousSource = state.activeSource;
  if (previousSource && sourceKey(previousSource) !== key) loraManagerController?.invalidateSource(sourceKey(previousSource));
  syncServerControls();
  const previousContract = sourceInterface(state.activeSource);
  const activeMigration = previousContract
    ? {
        sourceKey: state.activeSourceKey,
        source: structuredClone(state.activeSource),
        interface: structuredClone(previousContract),
        values: structuredClone(state.parameters),
        explicitInputIds: [...state.explicitParameterIds],
        modelSelections: structuredClone(modelSelectionsForSource(state.activeSource)),
        loraStrengthMemory: structuredClone(state.loraStrengthMemory),
        selectedPreset: state.selectedPreset,
        recentResolutions: recentResolutionsForInterface(state.recentResolutions, previousContract) === null
          ? null
          : structuredClone(state.recentResolutions),
      }
    : state.pendingSourceMigration;
  const migration = activeMigration?.sourceKey !== key ? activeMigration : null;
  persistActiveParameterState();
  const token = ++state.sourceLoadToken;
  const resolvedSummary = summary || state.sources.find((item) => sourceKey(item) === key) || null;
  state.activeSourceKey = key || null;
  persistActiveSourceKey();
  state.activeSource = resolvedSummary;
  state.pendingSourceMigration = migration;
  loadRecentResolutionsForActiveSource();
  const saved = key ? state.parameterStateBySource[key] : null;
  state.parameters = structuredClone(saved?.values || {});
  state.loraStrengthMemory = structuredClone(saved?.lora_strength_memory || {});
  state.loraImages = {};
  state.explicitParameterIds = new Set(saved?.explicitInputIds || []);
  state.sourceDetailLoading = Boolean(key);
  state.sourceDetailError = null;
  state.selectedPreset = null;
  state.compositionId = null;
  state.promptDirectionSignal = { sourceKey: null, controlId: null, status: "idle", appliedValue: null };
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
    if (previousSource && sourceKey(previousSource) === key && !revisionsMatch(detail, previousSource)) {
      loraManagerController?.invalidateSource(key);
    }
    state.activeSource = { ...(resolvedSummary || {}), ...detail, interface: contract };
    settingsInterfaces.set(key, state.activeSource);
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
      { rejectInvalid: true },
    );
    state.parameters = migrated.values;
    state.explicitParameterIds = new Set(migrated.explicitInputIds);
    if (migration) {
      state.loraStrengthMemory = migrateLoraStrengthMemory(
        contract,
        migration.interface,
        migration.values,
        migration.loraStrengthMemory,
        state.loraStrengthMemory,
      );
      carryModelSelection(migration, { carrySelections: carryModelSelections });
    }
    state.loraStrengthMemory = reconcileLoraStrengthMemory(contract, state.loraStrengthMemory);
    const presetId = migration ? migration.selectedPreset || null : saved?.selectedPreset || null;
    if (presetId && (contract.presets || []).some((preset) => preset.id === presetId)) {
      state.selectedPreset = presetId;
    }
    const selectionKey = modelSelectionStoreKey(state.activeSource);
    if (selectionKey && !state.modelSelectionsBySourceRevision.has(selectionKey)) {
      setModelSelectionsForSource(
        state.activeSource,
        normalizeSourceModelSelections(state.activeSource, {}, state.parameters),
      );
    }
    if (migration) {
      // Keep the checkpoint value and the fan-out selection in agreement on every path.
      applyStoredModelSelectionsToActiveParameters();
      const recent = Array.isArray(migration.recentResolutions)
        ? recentResolutionsForInterface(migration.recentResolutions, contract)
        : null;
      if (recent !== null) {
        state.recentResolutions = recent;
        persistRecentResolutions();
      }
    }
    state.pendingSourceMigration = null;
    state.sourceDetailError = null;
    persistActiveParameterState();
    void loadLoraImages(key, contract, token);
  } catch (error) {
    if (requestWasAborted(error, signal) || token !== state.sourceLoadToken) return;
    state.sourceDetailError = error.message || "The selected source could not be described.";
  } finally {
    if (token === state.sourceLoadToken) {
      state.sourceDetailLoading = false;
      renderPanel();
      autoSettingsSync?.resume();
    }
  }
}

async function loadLoraImages(sourceKey, contract, sourceToken) {
  const controls = interfaceInputs(contract).filter((item) => item.type === "lora_stack");
  await Promise.all(controls.map(async (control) => {
    try {
      const result = await api(loraImagePath(sourceKey, control.id), { deadlineMs: 8000 });
      if (sourceToken !== state.sourceLoadToken || sourceKey !== state.activeSourceKey) return;
      state.loraImages[control.id] = Object.fromEntries((result.items || []).map((item) => [item.id, item]));
      renderPanel();
    } catch {
      // The manager retries image metadata when opened; generation stays usable.
    }
  }));
}

function applyPreset(presetId) {
  const previousPrompt = state.parameters[promptDirectionSignalControl()?.id];
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
  if (previousPrompt !== state.parameters[promptDirectionSignalControl()?.id]) {
    syncServerControls();
  }
  state.serverFieldErrors = {};
  state.formError = null;
  collapseActiveModelSelectionsFromParameters();
  persistActiveParameterState();
  renderPanel();
}

function renderPanel() {
  const panel = document.querySelector("#generation-panel");
  if (!panel) return;
  applyAutomaticPromptPreview({ render: false });
  syncSubmissionSnapshot();
  state.selectedGenerationTargetCount = plannedGenerationTotal();
  const panelView = capturePanelView(panel);
  const contract = sourceInterface(state.activeSource);
  const clientErrors = validateImageParameters(contract, state.parameters);
  state.fieldErrors = { ...clientErrors, ...withoutNulls(state.serverFieldErrors) };
  for (const key of controlSectionKeysWithErrors(contract, state.fieldErrors)) {
    if (state.controlSectionOpen[key] !== true) {
      state.controlSectionOpen[key] = true;
      persistControlSections();
    }
  }
  const selected = state.activeSource || state.sources.find((item) => sourceKey(item) === state.activeSourceKey);
  panel.innerHTML = generationPanelMarkup(state, selected, contract);
  const assistant = panel.querySelector("#prompt-assistant");
  if (assistant) {
    const direction = assistant.querySelector("#creative-direction");
    direction.value = state.promptAssistant.creativeDirection || "";
    syncCreativeDirectionWarning(direction);
    if (state.promptGeneration.enabled) state.promptAssistant.mode = "refine";
    const mode = assistant.querySelector(`[name=assistant-mode][value=${state.promptAssistant.mode}]`);
    if (mode) mode.checked = true;
    const thinkingMode = assistant.querySelector("#prompt-assistant-thinking-mode");
    if (thinkingMode) thinkingMode.checked = state.promptAssistant.think !== false;
    syncPromptInstructions(assistant, state.promptAssistant.instructionOverrides, state.promptAssistant.mode);
  }
  syncPromptAssistantAction();
  syncPromptAssistantError();
  syncPromptDirectionSignalInPanel();
  restorePanelView(panel, panelView);
  syncSpeechControls();
  syncServerControls();
}

function syncPromptAssistantAction() {
  const button = document.querySelector("#prompt-assistant [data-action=compose-prompt]");
  if (!button) return;
  const busy = promptCompositionRequests > 0;
  button.disabled = busy || expectationCheckBusy(state) || !state.promptAssistant.available;
  const label = busy ? "Applying…" : expectationPanelPresentation(state).composeLabel;
  if (button.textContent !== label) button.textContent = label;
  if (busy || state.expectationCheckStarting) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}

function currentPromptValue() {
  const input = promptDirectionSignalControl();
  return input ? String(state.parameters[input.id] ?? "") : null;
}

const expectationStatusMarkupCache = new WeakMap();

// Keeps the Expectations block, the Apply button, and the status line current
// without re-rendering the panel, so typing keeps its caret and scroll.
function syncExpectationControls() {
  syncPromptAssistantAction();
  const panel = document.querySelector("#generation-panel");
  if (!panel) return;
  const view = expectationPanelPresentation(state);
  const block = panel.querySelector("[data-expectations]");
  if (block) {
    const badge = block.querySelector("[data-expectations-badge]");
    if (badge) {
      if (badge.textContent !== view.badge) badge.textContent = view.badge;
      badge.classList.toggle("is-on", view.on);
    }
    const toggle = block.querySelector("#expectations-enabled");
    if (toggle) {
      toggle.checked = view.on;
      toggle.disabled = view.toggleDisabled;
      toggle.closest("label")?.classList.toggle("is-disabled", view.unavailable);
    }
    for (const note of block.querySelectorAll("[data-expectations-note]")) note.hidden = !view.notes[note.dataset.expectationsNote];
    const count = block.querySelector("[data-expectations-count]");
    if (count && count.textContent !== view.countText) count.textContent = view.countText;
    for (const [selector, value] of [
      ["#creative-direction-expectations", view.settings.text],
      ["#expectations-threshold", String(view.settings.threshold)],
      ["#expectations-attempts", String(view.settings.maxAttempts)],
    ]) {
      const field = block.querySelector(selector);
      if (!field) continue;
      field.disabled = view.fieldsDisabled;
      if (document.activeElement !== field && field.value !== value) field.value = value;
    }
  }
  const status = panel.querySelector("#expectation-status");
  if (status) {
    const check = state.expectationCheck;
    const presentation = expectationStatusPresentation(check, {
      dismissed: Boolean(check?.id) && state.expectationStatusDismissedId === check.id,
      currentPrompt: currentPromptValue(),
    });
    status.hidden = presentation.hidden;
    if (status.className !== presentation.className) status.className = presentation.className;
    if (expectationStatusMarkupCache.get(status) !== presentation.inner) {
      const focused = status.contains(document.activeElement) ? document.activeElement.dataset.action : null;
      status.innerHTML = presentation.inner;
      expectationStatusMarkupCache.set(status, presentation.inner);
      if (focused) status.querySelector(`[data-action="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
    }
  }
}

function updateExpectationLimit(element, commit) {
  const key = element.id === "expectations-threshold" ? "threshold" : "maxAttempts";
  const maximum = key === "threshold" ? EXPECTATION_LIMITS.maxThreshold : EXPECTATION_LIMITS.maxAttempts;
  const value = Number(element.value);
  if (element.value.trim() !== "" && Number.isInteger(value) && value >= 1 && value <= maximum) {
    state.expectations = { ...state.expectations, [key]: value };
  } else if (commit) {
    // Out-of-range entries snap to the nearest allowed value when the field is left.
    const bounded = Number.isFinite(value) && element.value.trim() !== "" ? Math.min(maximum, Math.max(1, Math.round(value))) : state.expectations[key];
    state.expectations = { ...state.expectations, [key]: bounded };
  }
  if (commit) element.value = String(state.expectations[key]);
  setPromptAssistantError(null);
  settingsSync?.schedule();
  syncServerControls();
}

function openExpectationCheckDialog() {
  if (!expectationChecks?.open()) toast("Start a check with Apply & verify first.");
}

function expectationDismissedStorageKey() {
  return `cif.expectation-status-dismissed.${sessionStorageUserId()}`;
}

function dismissExpectationStatus() {
  const id = state.expectationCheck?.id;
  if (!id) return;
  state.expectationStatusDismissedId = id;
  writeStoredItem(expectationDismissedStorageKey(), id);
  syncExpectationControls();
  document.querySelector("#prompt-assistant [data-action=compose-prompt]")?.focus({ preventScroll: true });
}

// Creative-direction border signal: the prompt control's border animates
// gold while a composition request is in flight, turns green when the
// composed text lands ("ready to go"), and returns to normal styling when a
// generation is queued or the text is changed in any way.

const DIRECTION_SIGNAL_CLASSES = ["is-direction-composing", "is-direction-applied"];

function setDirectionSignalClasses(element, status) {
  if (!element) return;
  for (const className of DIRECTION_SIGNAL_CLASSES) element.classList.remove(className);
  if (status === "composing") element.classList.add("is-direction-composing");
  else if (status === "applied") element.classList.add("is-direction-applied");
}

function promptDirectionSignalControl() {
  const contract = sourceInterface(state.activeSource);
  return positivePromptInput(contract)
    || interfaceInputs(contract).find((input) => input.id === "prompt.text")
    || null;
}

function setPromptDirectionSignal(status, value = null) {
  const control = promptDirectionSignalControl();
  const normalized = status === "composing" || status === "applied" ? status : "idle";
  state.promptDirectionSignal = {
    sourceKey: state.activeSourceKey,
    controlId: control?.id ?? null,
    status: normalized,
    appliedValue: normalized === "applied" ? value : null,
  };
  setDirectionSignalClasses(
    document.querySelector(
      `[data-control-id="${CSS.escape(state.promptDirectionSignal.controlId || "")}"]`,
    ),
    normalized,
  );
}

function syncPromptDirectionSignalInPanel() {
  const signal = state.promptDirectionSignal;
  if (!signal || signal.sourceKey !== state.activeSourceKey || !signal.controlId) return;
  const element = document.querySelector(
    `[data-control-id="${CSS.escape(signal.controlId)}"]`,
  );
  if (!element) return;
  const status = directionSignalNextStatus({
    status: signal.status,
    appliedValue: signal.appliedValue,
    currentValue: element.value,
  });
  if (status !== signal.status) state.promptDirectionSignal = { ...signal, status };
  setDirectionSignalClasses(element, status);
}

function setPromptEditorDirectionSignal(status, value = null) {
  const normalized = status === "composing" || status === "applied" ? status : "idle";
  state.promptEditorDirectionStatus = normalized;
  state.promptEditorDirectionAppliedValue = normalized === "applied" ? value : null;
  setDirectionSignalClasses(
    document.querySelector("#prompt-editor-dialog[open] #prompt-editor-textarea"),
    normalized,
  );
}

function setPromptAssistantError(message) {
  state.promptAssistant.error = message || null;
  syncPromptAssistantError();
}

function syncPromptAssistantError() {
  const region = document.querySelector("#prompt-assistant-error");
  if (!region) return;
  const message = state.promptAssistant.error || "";
  region.textContent = message;
  region.hidden = !message;
  const thinkingMode = document.querySelector("#prompt-assistant-thinking-mode");
  if (message) thinkingMode?.setAttribute("aria-describedby", region.id);
  else thinkingMode?.removeAttribute("aria-describedby");
}

function setPromptEditorAssistantError(dialog, message) {
  const region = dialog?.querySelector("#prompt-editor-assistant-error");
  if (!region) return;
  const text = message || "";
  region.textContent = text;
  region.hidden = !text;
  const thinkingMode = dialog.querySelector("#prompt-editor-thinking-mode");
  if (text) thinkingMode?.setAttribute("aria-describedby", region.id);
  else thinkingMode?.removeAttribute("aria-describedby");
}

function promptInstructionsStorageKey() {
  return `cif.prompt-instructions.${state.session?.user?.id || "anonymous"}`;
}

function loadPromptInstructions() {
  try {
    const saved = JSON.parse(localStorage.getItem(promptInstructionsStorageKey()) || "{}");
    return Object.fromEntries(["create", "refine"]
      .filter((mode) => typeof saved?.[mode] === "string" && saved[mode].length <= 8000)
      .map((mode) => [mode, saved[mode]]));
  } catch {
    return {};
  }
}

function persistPromptInstructions() {
  settingsSync?.schedule();
  try {
    localStorage.setItem(promptInstructionsStorageKey(), JSON.stringify(state.promptAssistant.instructionOverrides));
  } catch {
    // Editing remains available when browser storage is disabled.
  }
}

function generationQuantityStorageKey() {
  return `cif.generation-quantity.${state.session?.user?.id || "anonymous"}`;
}

function loadGenerationQuantity() {
  try {
    return clampGenerationQuantity(localStorage.getItem(generationQuantityStorageKey()));
  } catch {
    return MIN_GENERATION_QUANTITY;
  }
}

function persistGenerationQuantity() {
  settingsSync?.schedule();
  try {
    localStorage.setItem(generationQuantityStorageKey(), String(state.generationQuantity));
  } catch {
    // The quantity still applies for this session when storage is disabled.
  }
}

// Session persistence for the generation controls: every per-source
// parameter set, the active source, the collapsed sections, and the
// creative-direction draft. Values are scoped per user so several accounts
// on one browser keep distinct settings; loaders validate the stored JSON
// and fall back to defaults when it is missing or corrupt.
function sessionStorageUserId() {
  return state.session?.user?.id || "anonymous";
}

function readStoredItem(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStoredItem(key, value) {
  try {
    if (value === null || value === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Settings still apply for this session when browser storage is disabled.
  }
}

function persistParameterState() {
  settingsSync?.schedule();
  writeStoredItem(parameterStateStorageKey(sessionStorageUserId()), JSON.stringify(state.parameterStateBySource));
}

// Published sources can disappear (or be renamed) between visits; drop
// stored parameter sets for keys the current catalog no longer knows so
// localStorage does not grow without bound.
function persistActiveSourceKey() {
  settingsSync?.schedule();
  writeStoredItem(
    activeSourceStorageKey(sessionStorageUserId()),
    state.activeSourceKey ? JSON.stringify(state.activeSourceKey) : null,
  );
}

function persistControlSections() {
  settingsSync?.schedule();
  writeStoredItem(controlSectionStorageKey(sessionStorageUserId()), JSON.stringify(state.controlSectionOpen));
}

function persistCreativeDirectionDraft() {
  settingsSync?.schedule();
  writeStoredItem(
    creativeDirectionStorageKey(sessionStorageUserId()),
    JSON.stringify({
      creativeDirection: state.promptAssistant.creativeDirection,
      mode: state.promptAssistant.mode,
      think: state.promptAssistant.think,
    }),
  );
}

function applyGenerationQuantity(next) {
  state.generationQuantity = clampGenerationQuantity(next);
  persistGenerationQuantity();
  syncGenerationQuantityControl();
}

function syncGenerationQuantityControl() {
  const panel = document.querySelector("#generation-panel");
  if (!panel) return;
  const input = panel.querySelector("#generation-quantity");
  if (input && document.activeElement !== input) input.value = String(state.generationQuantity);
  const increment = panel.querySelector('[data-action="increment-generation-quantity"]');
  if (increment) increment.disabled = Boolean(state.submitting) || state.generationQuantity >= MAX_GENERATION_QUANTITY;
  const decrement = panel.querySelector('[data-action="decrement-generation-quantity"]');
  if (decrement) decrement.disabled = Boolean(state.submitting) || state.generationQuantity <= MIN_GENERATION_QUANTITY;
  if (input) input.disabled = Boolean(state.submitting);
}

function promptEditorMode(dialog) {
  return !state.promptGeneration.enabled && dialog?.querySelector('[name="prompt-editor-assistant-mode"]:checked')?.value === "create"
    ? "create" : "refine";
}

function capturePromptInstructions(container, overrides) {
  const textarea = container?.querySelector("[data-prompt-instructions]");
  if (!textarea || textarea.disabled) return;
  const mode = textarea.dataset.instructionsMode;
  if (textarea.value === state.promptAssistant.defaultInstructions[mode]) delete overrides[mode];
  else overrides[mode] = textarea.value;
  textarea.setCustomValidity(textarea.value.trim() ? "" : "Enter instructions or reset to the default.");
}

function syncPromptInstructions(container, overrides, mode) {
  const textarea = container?.querySelector("[data-prompt-instructions]");
  if (!textarea) return;
  const value = promptInstructionsForMode({ ...state.promptAssistant, instructionOverrides: overrides }, mode);
  if (textarea.value !== value) textarea.value = value;
  textarea.dataset.instructionsMode = mode;
  textarea.disabled = typeof state.promptAssistant.defaultInstructions[mode] !== "string";
  textarea.setCustomValidity(value.trim() ? "" : "Enter instructions or reset to the default.");
  container.querySelector("[data-instructions-mode-label]").textContent = mode === "create"
    ? "Instructions for a new prompt" : "Instructions for refining your prompt";
  container.querySelector("[data-instructions-mode-hint]").title = PROMPT_INSTRUCTIONS_HINTS[mode];
  container.querySelector('[data-action="reset-prompt-instructions"]').disabled = textarea.disabled;
}

function validatePromptInstructions(textarea) {
  if (!textarea) return true;
  if (!textarea.checkValidity()) {
    const disclosure = textarea.closest("details");
    if (disclosure) disclosure.open = true;
  }
  return textarea.reportValidity();
}

function resetPromptInstructions(button) {
  const dialog = button.closest("#prompt-editor-dialog");
  const container = dialog || button.closest("#prompt-assistant");
  const mode = dialog ? promptEditorMode(dialog) : state.promptAssistant.mode;
  const overrides = dialog ? promptEditorInstructionOverrides : state.promptAssistant.instructionOverrides;
  delete overrides[mode];
  syncPromptInstructions(container, overrides, mode);
  if (dialog) {
    delete dialog.dataset.promptAssistantCompositionId;
    setPromptEditorAssistantError(dialog, null);
  } else {
    persistPromptInstructions();
    setPromptAssistantError(null);
    syncServerControls();
  }
  container.querySelector("[data-prompt-instructions]").focus();
}

function syncPromptAssistantDraftFromPanel() {
  const assistant = document.querySelector("#prompt-assistant");
  if (!assistant) return;
  const promptInput = promptDirectionSignalControl();
  const prompt = promptInput && document.querySelector(
    `[data-control-id="${CSS.escape(promptInput.id)}"]`,
  );
  if (prompt && state.parameters[promptInput.id] !== prompt.value) {
    state.parameters[promptInput.id] = normalizeInputValue(promptInput, prompt.value);
    persistActiveParameterState();
    syncServerControls();
  }
  const direction = assistant.querySelector("#creative-direction");
  const mode = assistant.querySelector('[name="assistant-mode"]:checked');
  const thinkingMode = assistant.querySelector("#prompt-assistant-thinking-mode");
  const automaticCreativeDirection = document.querySelector(
    "#auto-generate-creative-direction",
  );
  const nextDirection = direction?.value ?? state.promptAssistant.creativeDirection ?? "";
  const nextMode = mode?.value === "create" ? "create" : "refine";
  const nextThinkingMode = thinkingMode?.checked ?? state.promptAssistant.think !== false;
  const previousInstructions = promptInstructionsForMode(state.promptAssistant);
  capturePromptInstructions(assistant, state.promptAssistant.instructionOverrides);
  const nextAutomaticCreativeDirection =
    automaticCreativeDirection?.checked ?? state.autoGenerateCreativeDirection;
  if (
    nextDirection !== state.promptAssistant.creativeDirection ||
    nextMode !== state.promptAssistant.mode ||
    nextThinkingMode !== (state.promptAssistant.think !== false) ||
    previousInstructions !== promptInstructionsForMode(state.promptAssistant, nextMode) ||
    nextAutomaticCreativeDirection !== state.autoGenerateCreativeDirection
  ) {
    syncServerControls();
  }
  state.promptAssistant.creativeDirection = nextDirection;
  state.promptAssistant.mode = state.promptGeneration.enabled ? "refine" : nextMode;
  state.promptAssistant.think = nextThinkingMode;
  state.autoGenerateCreativeDirection = nextAutomaticCreativeDirection;
  syncPromptInstructions(assistant, state.promptAssistant.instructionOverrides, nextMode);
  persistPromptInstructions();
  persistCreativeDirectionDraft();
}

function capturePanelView(panel) {
  const view = {
    scrollTop: panel.querySelector("#panel-scroll")?.scrollTop || 0,
    promptInstructionsOpen: Boolean(panel.querySelector(".prompt-preprocessor")?.open),
    textareaHeights: [...panel.querySelectorAll("textarea[id]")]
      .filter((textarea) => textarea.style.height)
      .map((textarea) => ({ id: textarea.id, height: textarea.style.height })),
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
    sectionOpen: Boolean(element.closest(".control-section-body") && element.closest(".control-section.is-expanded")),
  };
}

function restorePanelView(panel, view) {
  const promptInstructions = panel.querySelector(".prompt-preprocessor");
  if (promptInstructions) promptInstructions.open = view.promptInstructionsOpen;
  const scroller = panel.querySelector("#panel-scroll");
  if (scroller) scroller.scrollTop = view.scrollTop;
  for (const { id, height } of view.textareaHeights || []) {
    const textarea = panel.querySelector(`#${CSS.escape(id)}`);
    if (textarea) textarea.style.height = height;
  }
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

function syncSubmissionSnapshot() {
  state.pendingSubmission = pendingSubmission();
  const jobs = pendingPromptJobs();
  const pendingPath = state.pendingSubmission?.path;
  state.promptGenerationBusy = Boolean(state.promptGenerationRequest || jobs.length ||
    ["/api/prompt-generations", "/api/generation-preparations"].includes(pendingPath));
  state.promptPreparationBusy = state.promptGenerationRequest === "images" ||
    pendingPath === "/api/generation-preparations" || jobs.some((job) => job.path === "/api/generation-preparations");
  const phases = jobs.map((job) => promptJobPhases.get(job.id) || "generating");
  state.promptGenerationPhase = phases.includes("refining") ? "refining"
    : phases.length && phases.every((phase) => phase === "queued") ? "queued" : "generating";
}

function syncGenerationButtons() {
  syncSubmissionSnapshot();
  const contract = sourceInterface(state.activeSource);
  const disabled = generationSubmissionDisabled(state, state.activeSource, contract, {
    ...validateImageParameters(contract, state.parameters), ...withoutNulls(state.serverFieldErrors),
  });
  const sync = (button, presentation, disabled) => {
    if (!button) return;
    button.disabled = disabled;
    button.setAttribute("aria-busy", String(presentation.busy));
    const markup = generationButtonContentMarkup(presentation);
    // Preserve the spinner node/animation during polling and settings updates.
    if (button.innerHTML !== markup) button.innerHTML = markup;
  };
  sync(document.querySelector("#generate-button"), generationButtonPresentation(state), disabled);
  sync(document.querySelector('[data-action="generate-prompt"]'), promptGenerationButtonPresentation(state),
    !state.promptGeneratorSource || Boolean(promptRuntimeError(state)) || state.submitting || state.promptGenerationBusy || Boolean(state.pendingSubmission));
  const promptSource = document.querySelector("#prompt-generation-source");
  if (promptSource) promptSource.disabled = state.promptGenerationBusy;
}

function syncGenerationSubmissionState() {
  syncSubmissionSnapshot();
  renderGenerationActivity();
  gallerySelection?.sync();
  const panel = document.querySelector("#generation-panel");
  if (!panel) return;
  const contract = sourceInterface(state.activeSource);
  const errors = {
    ...validateImageParameters(contract, state.parameters),
    ...withoutNulls(state.serverFieldErrors),
  };
  state.fieldErrors = errors;
  state.selectedGenerationTargetCount = plannedGenerationTotal();
  for (const control of interfaceInputs(contract)) {
    const block = panel.querySelector(
      `[data-control-block="${CSS.escape(control.id)}"]`,
    );
    if (block) {
      syncFieldError(block, control.id, errors[control.id]);
      if (errors[control.id]) {
        const section = block.closest("[data-control-section]");
        if (section) setControlSectionElementOpen(section, true);
      }
    }
  }

  let summary = panel.querySelector(".form-error.summary");
  if (state.formError) {
    if (!summary) {
      summary = document.createElement("div");
      summary.className = "form-error summary";
      summary.setAttribute("role", "alert");
      panel.querySelector(".panel-fixed")?.append(summary);
    }
    summary.textContent = state.formError;
  } else {
    summary?.remove();
  }

  syncGenerationButtons();
  const sourcePicker = panel.querySelector("#workflow-source");
  if (sourcePicker) {
    sourcePicker.disabled =
      !state.sources.length || (state.submitting && !state.autoGenerate);
  }
  syncGenerationQuantityControl();
}

// The assistant inputs in force at submission time, snapshotted with every
// generation (single and each batch item) so recall can restore the Creative
// Direction section even for manual generations and for batch items whose
// prompt was composed by a run linked to a sibling item.
function promptAssistantSnapshotPayload() {
  return {
    mode: state.promptAssistant.mode === "create" ? "create" : "refine",
    creative_direction: state.promptAssistant.creativeDirection || "",
    instructions: promptInstructionsForMode(state.promptAssistant) || null,
    thinking_enabled: state.promptAssistant.think !== false,
  };
}

async function generate() {
  if (state.autoGenerate || state.pendingAutoEnabled !== undefined || !state.automationLoaded || state.automationBusy || state.autoSettingsSaving) return false;
  const plannedTotal = plannedGenerationTotal();
  if (plannedTotal > MAX_BATCH_GENERATION_ITEMS) {
    state.formError = `Too many planned generations: ${plannedTotal} selected checkpoints × quantity ${state.generationQuantity} exceeds the ${MAX_BATCH_GENERATION_ITEMS}-item batch limit. Lower the quantity or select fewer checkpoints.`;
    syncGenerationSubmissionState();
    toast(state.formError, "error");
    return false;
  }
  if (expectationCheckBusy(state)) return false;
  if (state.promptGeneration.enabled) return runPromptGeneration(true);
  // With expectations on, the server composes, verifies, and queues the batch.
  if (state.autoGenerateCreativeDirection && expectationsActive(state)) return startExpectationCheck("generate");
  if (state.autoGenerateCreativeDirection && !await composePrompt()) return false;
  if (plannedTotal > 1) {
    return generateSelectedCheckpoints();
  }
  return generateSingleSource();
}

// The request one Generate click submits for a single planned image: the
// first selected model variant, with the assistant inputs snapshotted.
function singleGenerationPayload({ sourceKey, collectionId, revision, contract }) {
  const [modelParameters] = modelParameterVariantsForSource(state.activeSource, contract);
  return {
    source_key: sourceKey,
    collection_id: collectionId,
    revision,
    parameters: {
      ...parametersForRequest(contract, state.parameters),
      ...modelParameters,
    },
    prompt_assistant: promptAssistantSnapshotPayload(),
  };
}

// The requests one Generate click submits for several planned images, in
// submission order (every selected model variant × the quantity).
async function batchGenerationPayloads({ sourceKey, collectionId, revision, contract, source, parameters, quantity, validationParameters = {}, signal }) {
  const modelVariants = orderedModelParameterVariants(source, contract, parameters);
  const sharedParameters = {
    ...parametersForRequest(contract, parameters),
    ...modelVariants[0],
  };
  const validation = await api("/api/generations/validate", {
    method: "POST",
    signal,
    body: JSON.stringify({
      source_key: sourceKey,
      collection_id: collectionId,
      revision,
      parameters: { ...sharedParameters, ...validationParameters },
    }),
  });
  const inputs = new Map(interfaceInputs(contract).map((input) => [input.id, input]));
  // A single planned item keeps the server-resolved seed aligned across the
  // selected checkpoints; a quantity over one lets each item resolve its own
  // seed so random-seed repeats are not duplicates.
  if (quantity === MIN_GENERATION_QUANTITY) {
    for (const [parameterId, value] of Object.entries(validation.resolved_seeds || {})) {
      if (inputs.get(parameterId)?.type === "seed") {
        sharedParameters[parameterId] = String(value);
      }
    }
  }
  const payloads = [];
  for (const modelParameters of modelVariants) {
    for (let repeat = 0; repeat < quantity; repeat += 1) {
      payloads.push({
        source_key: sourceKey,
        collection_id: collectionId,
        revision: structuredClone(revision),
        parameters: { ...sharedParameters, ...modelParameters },
        prompt_assistant: promptAssistantSnapshotPayload(),
      });
    }
  }
  return payloads;
}

// Exactly what Apply (one probe) or Generate (the whole plan) would submit.
async function planGenerationPayloads(purpose, { signal } = {}) {
  const contract = sourceInterface(state.activeSource);
  const context = {
    sourceKey: state.activeSourceKey,
    collectionId: state.currentCollectionId,
    revision: structuredClone(sourceRevision(state.activeSource)),
    contract,
  };
  if (purpose === "apply" || plannedGenerationTotal() <= 1) return [singleGenerationPayload(context)];
  const prompt = promptDirectionSignalControl();
  return batchGenerationPayloads({
    ...context,
    source: selectedGenerationSource(),
    parameters: structuredClone(state.parameters),
    quantity: state.generationQuantity,
    // The server composes the prompt; seed resolution must not fail on a blank one.
    validationParameters: prompt && !String(state.parameters[prompt.id] ?? "").trim() ? { [prompt.id]: "expectation check" } : {},
    signal,
  });
}

async function generateSingleSource() {
  const requestOwnerId = state.session.user.id;
  const signal = applicationStartupController.signal;
  const contract = sourceInterface(state.activeSource);
  const requestSourceKey = state.activeSourceKey;
  const requestRevision = structuredClone(sourceRevision(state.activeSource));
  const requestComfyuiInstanceId = state.defaultComfyuiInstanceId;
  const requestCompositionId = state.compositionId;
  const requestComfyuiInstance = imageComfyuiInstance();
  // Manual generation follows the folder currently on screen.
  const requestCollectionId = state.currentCollectionId;
  if (
    !requestSourceKey ||
    !state.activeSource ||
    !contract ||
    state.activeSource.available === false ||
    !requestComfyuiInstanceId ||
    !requestComfyuiInstance
  )
    return false;
  const errors = validateImageParameters(contract, state.parameters);
  if (Object.keys(errors).length) {
    state.serverFieldErrors = errors;
    state.formError = "Review the highlighted controls.";
    syncGenerationSubmissionState();
    focusFirstInvalid();
    return false;
  }
  beginGenerationActivitySubmission(plannedGenerationTotal());
  state.submitting = true;
  state.formError = null;
  state.serverFieldErrors = {};
  syncGenerationSubmissionState();
  setPromptDirectionSignal("idle");
  let focusErrors = false;
  try {
    const payload = singleGenerationPayload({
      sourceKey: requestSourceKey,
      collectionId: requestCollectionId,
      revision: requestRevision,
      contract,
    });
    if (requestCompositionId) payload.prompt_assistant_run_id = requestCompositionId;
    const generation = await submitGeneration("/api/generations", payload, null, { signal });
    if (state.session?.user?.id !== requestOwnerId) return false;
    if (!TERMINAL_GENERATION_STATUSES.has(generation.status)) pendingGenerationIds.add(generation.id);
    const belongsToCurrentView = generationBelongsToView(generation);
    const current = state.generations.find((item) => item.id === generation.id);
    if (belongsToCurrentView) {
      state.generations = sortGenerationsNewestFirst([
        current || generation,
        ...state.generations.filter((item) => item.id !== generation.id),
      ]);
    }
    if (
      sourceContextIsCurrent(requestSourceKey, requestRevision) &&
      state.compositionId === requestCompositionId
    ) {
      state.compositionId = null;

    }
    if (belongsToCurrentView) upsertGalleryCard(current || generation);
    toast("Generation queued.", "success");
    return true;
  } catch (error) {
    if (signal.aborted || state.session?.user?.id !== requestOwnerId) return false;
    if (error.code === "submission_status_unknown") { submissionRecovery?.start(); return false; }
    if (
      !generationContextIsCurrent(
        requestSourceKey,
        requestRevision,
        requestComfyuiInstanceId,
      )
    ) {
      toast(
        `Generation request for the previous source or runtime failed: ${error.message}`,
        "error",
      );
      return false;
    }
    state.formError = error.message;
    state.serverFieldErrors = normalizeParameterErrors(error.fields);
    focusErrors = Object.keys(state.serverFieldErrors).length > 0;
    if (isComfyuiInstanceError(error)) {
      await refreshComfyuiInstancesAfterError(
        error.message,
        requestComfyuiInstanceId,
      );
    }
    if (["source_republished", "source_unavailable"].includes(error.code)) {
      const message = error.message;
      await loadSources();
      if (state.activeSourceKey === requestSourceKey) state.formError = message;
    }
    return false;
  } finally {
    if (!signal.aborted && state.session?.user?.id === requestOwnerId) {
      await refreshGenerationActivity();
      state.generationSubmissionProgress = null;
      state.submitting = false;
      syncGenerationSubmissionState();
      if (focusErrors) focusFirstInvalid();
    }
  }
}

async function generateSelectedCheckpoints() {
  const requestOwnerId = state.session.user.id;
  const signal = applicationStartupController.signal;
  const contract = sourceInterface(state.activeSource);
  const requestSourceKey = state.activeSourceKey;
  const requestRevision = structuredClone(sourceRevision(state.activeSource));
  const requestComfyuiInstanceId = state.defaultComfyuiInstanceId;
  const requestCompositionId = state.compositionId;
  const requestParameters = structuredClone(state.parameters);
  const requestSource = selectedGenerationSource();
  const requestComfyuiInstance = imageComfyuiInstance();
  const requestCollectionId = state.currentCollectionId;
  if (
    !requestSourceKey ||
    !requestSource ||
    !contract ||
    requestSource.available === false ||
    !requestComfyuiInstanceId ||
    !requestComfyuiInstance
  ) {
    return false;
  }

  const errors = validateImageParameters(contract, requestParameters);
  if (Object.keys(errors).length) {
    state.serverFieldErrors = errors;
    state.formError = "Review the highlighted generation controls.";
    syncGenerationSubmissionState();
    focusFirstInvalid();
    return false;
  }

  beginGenerationActivitySubmission(plannedGenerationTotal());
  state.submitting = true;
  state.formError = null;
  state.serverFieldErrors = {};
  syncGenerationSubmissionState();
  setPromptDirectionSignal("idle");
  let focusErrors = false;
  try {
    const payloads = await batchGenerationPayloads({
      sourceKey: requestSourceKey,
      collectionId: requestCollectionId,
      revision: requestRevision,
      contract,
      source: requestSource,
      parameters: requestParameters,
      quantity: state.generationQuantity,
    });
    // Every item snapshots the assistant inputs; only the first may consume
    // the composition run (a run belongs to one generation).
    const queueTargets = payloads.map((payload, index) => {
      const usesPromptAssistant = Boolean(requestCompositionId && index === 0);
      if (usesPromptAssistant) payload.prompt_assistant_run_id = requestCompositionId;
      return { payload, usesPromptAssistant };
    });
    const batch = await submitGeneration("/api/generations/batch", { items: queueTargets.map(({ payload }) => payload) }, null, { signal });
    if (state.session?.user?.id !== requestOwnerId) return false;
    const queueResults = batch.items.map((item) => item.generation
      ? { status: "fulfilled", value: item.generation }
      : { status: "rejected", reason: Object.assign(new Error(item.error.message), item.error) });
    const queued = [];
    const failures = [];
    let promptAssistantQueued = false;
    for (let index = 0; index < queueResults.length; index += 1) {
      const result = queueResults[index];
      if (result.status === "fulfilled") {
        queued.push(result.value);
        if (!TERMINAL_GENERATION_STATUSES.has(result.value.status)) pendingGenerationIds.add(result.value.id);
        if (queueTargets[index].usesPromptAssistant) promptAssistantQueued = true;
      } else {
        failures.push(result.reason);
      }
    }

    const visibleQueued = queued.filter(
      (generation) => generationBelongsToView(generation),
    );
    if (visibleQueued.length) {
      const queuedIds = new Set(visibleQueued.map((generation) => generation.id));
      const currentById = new Map(
        state.generations.map((generation) => [generation.id, generation]),
      );
      state.generations = sortGenerationsNewestFirst([
        ...visibleQueued.map(
          (generation) => currentById.get(generation.id) || generation,
        ),
        ...state.generations.filter((generation) => !queuedIds.has(generation.id)),
      ]);
      renderGallery();
    }

    if (
      (requestCompositionId ? promptAssistantQueued : queued.length > 0) &&
      sourceContextIsCurrent(requestSourceKey, requestRevision) &&
      state.compositionId === requestCompositionId
    ) {
      state.compositionId = null;

    }

    if (failures.length) {
      const failureSummary = failures
        .slice(0, 3)
        .map((error) => error.message)
        .join(" ");
      const omitted =
        failures.length > 3 ? ` ${failures.length - 3} more failed.` : "";
      state.formError = `Queued ${queued.length} of ${queueTargets.length} planned generations. ${failureSummary}${omitted}`;
      state.serverFieldErrors = normalizeParameterErrors(failures[0]?.fields);
      focusErrors = Object.keys(state.serverFieldErrors).length > 0;
      toast(state.formError, "error");
      if (
        failures.some((error) =>
          ["source_republished", "source_unavailable"].includes(error?.code),
        )
      ) {
        const message = state.formError;
        await loadSources();
        if (state.activeSourceKey === requestSourceKey) {
          state.formError = message;
          renderPanel();
        }
      }
      const instanceFailure = failures.find((error) =>
        isComfyuiInstanceError(error),
      );
      if (
        instanceFailure &&
        generationContextIsCurrent(
          requestSourceKey,
          requestRevision,
          requestComfyuiInstanceId,
        )
      ) {
        await refreshComfyuiInstancesAfterError(
          instanceFailure.message,
          requestComfyuiInstanceId,
        );
      }
    } else {
      toast(
        `${queued.length} generation${queued.length === 1 ? "" : "s"} queued.`,
        "success",
      );
    }
    return (
      queued.length > 0 &&
      failures.length === 0 &&
      queued.length === queueTargets.length
    );
  } catch (error) {
    if (signal.aborted || state.session?.user?.id !== requestOwnerId) return false;
    if (error.code === "submission_status_unknown") { submissionRecovery?.start(); return false; }
    if (
      !generationContextIsCurrent(
        requestSourceKey,
        requestRevision,
        requestComfyuiInstanceId,
      )
    ) {
      toast(
        `Checkpoint batch for the previous source or runtime failed: ${error.message}`,
        "error",
      );
      return false;
    }
    state.formError = error.message;
    state.serverFieldErrors = normalizeParameterErrors(error.fields);
    focusErrors = Object.keys(state.serverFieldErrors).length > 0;
    if (isComfyuiInstanceError(error)) {
      await refreshComfyuiInstancesAfterError(
        error.message,
        requestComfyuiInstanceId,
      );
    }
    if (["source_republished", "source_unavailable"].includes(error.code)) {
      const message = error.message;
      await loadSources();
      if (state.activeSourceKey === requestSourceKey) state.formError = message;
    }
    return false;
  } finally {
    if (!signal.aborted && state.session?.user?.id === requestOwnerId) {
      await refreshGenerationActivity();
      state.generationSubmissionProgress = null;
      state.submitting = false;
      syncGenerationSubmissionState();
      if (focusErrors) focusFirstInvalid();
    }
  }
}


async function composePrompt(
  button,
) {
  if (!state.promptAssistant.available || promptCompositionRequests > 0) return false;
  syncPromptAssistantDraftFromPanel();
  const requestSession = applicationStartupController;
  const requestSourceKey = state.activeSourceKey;
  const requestRevision = structuredClone(sourceRevision(state.activeSource));
  const contract = sourceInterface(state.activeSource);
  const promptInput =
    positivePromptInput(contract) || interfaceInputs(contract).find((input) => input.id === "prompt.text");
  if (!requestSourceKey || !promptInput) return false;
  const visiblePrompt = document.querySelector(
    `[data-control-id="${CSS.escape(promptInput.id)}"]`,
  );
  if (visiblePrompt) {
    state.parameters[promptInput.id] = normalizeInputValue(promptInput, visiblePrompt.value);
    persistActiveParameterState();
  }
  const requestMode = state.promptAssistant.mode;
  const requestPrompt = state.parameters[promptInput.id] || "";
  const requestDirection = state.promptAssistant.creativeDirection || "";
  const requestThink = state.promptAssistant.think !== false;
  const instructionsInput = document.querySelector("#prompt-assistant-instructions");
  if (!validatePromptInstructions(instructionsInput)) {
    return false;
  }
  const requestInstructions = promptInstructionsForMode(state.promptAssistant);
  promptCompositionRequests += 1;
  setPromptAssistantError(null);
  syncPromptAssistantAction();
  setPromptDirectionSignal("composing");
  const requestSignal = state.promptDirectionSignal;
  const clearRequestSignal = () => {
    if (state.promptDirectionSignal === requestSignal) setPromptDirectionSignal("idle");
  };
  renderGenerationActivity();
  try {
    const result = await api("/api/prompt-assistant/compose", {
      method: "POST",
      body: JSON.stringify({
        mode: requestMode,
        prompt: requestPrompt,
        creative_direction: requestDirection,
        think: requestThink,
        ...(instructionsInput?.disabled ? {} : { instructions: requestInstructions }),
      }),
    });
    if (requestSession !== applicationStartupController) return false;
    if (
      !sourceContextIsCurrent(requestSourceKey, requestRevision) ||
      promptInstructionsForMode(state.promptAssistant) !== requestInstructions
    ) {
      toast("Prompt composition finished after its inputs changed and was not applied.");
      clearRequestSignal();
      return false;
    }
    state.parameters[promptInput.id] = result.prompt;
    state.explicitParameterIds.add(promptInput.id);
    persistActiveParameterState();
    state.compositionId = result.composition_id;
    state.promptAssistant.historicalModel = result.model;
    const prompt = document.querySelector(
      `[data-control-id="${CSS.escape(promptInput.id)}"]`,
    );
    if (prompt) prompt.value = result.prompt;
    setPromptDirectionSignal("applied", result.prompt);
    syncParameterValidation(promptInput.id);
    prompt?.focus();
    toast("Creative direction applied to the editable Prompt field.", "success");
    return true;
  } catch (error) {
    if (requestSession !== applicationStartupController) return false;
    if (sourceContextIsCurrent(requestSourceKey, requestRevision)) {
      setPromptDirectionSignal("idle");
      const message = error.message || "Creative direction could not be applied.";
      setPromptAssistantError(message);
      toast(message, "error");
    } else {
      toast(`Prompt composition for the previous source failed: ${error.message}`, "error");
    }
    return false;
  } finally {
    promptCompositionRequests = Math.max(0, promptCompositionRequests - 1);
    syncPromptAssistantAction();
    renderGenerationActivity();
    syncServerControls();
  }
}

function expectationCheckBlockedReason() {
  if (!state.automationLoaded || state.pendingAutoEnabled !== undefined || state.automationBusy || state.autoSettingsSaving) return "Checking auto generation…";
  if (state.autoGenerate || state.automation?.enabled) return "Turn off Auto-generate to verify expectations.";
  if (expectationCheckBusy(state)) return "An expectation check is already running. Stop it or wait for it to finish.";
  if (state.submitting || state.submissionRecoveryPending || state.pendingSubmission || promptCompositionRequests > 0) return "Wait for the current request to finish.";
  if (!state.promptAssistant.available) return state.promptAssistant.message || "Creative Direction is unavailable.";
  if (state.promptAssistant.visionAvailable !== true) return "The Creative Direction model can't inspect images right now, so expectations can't be verified. Apply Creative Direction still works without the check.";
  return null;
}

// Image validation for a check: the server composes the prompt for every attempt.
function expectationCheckParameterErrors(contract) {
  const prompt = promptDirectionSignalControl();
  return clientValidate(contract, prompt ? { ...state.parameters, [prompt.id]: "Prompt preparation pending." } : state.parameters);
}

// Start a server-owned check: compose → one probe image → vision scores →
// revise, until every expectation passes or attempts run out. "generate"
// queues the rest of the planned batch with the qualified prompt after a pass.
async function startExpectationCheck(purpose) {
  const reason = expectationCheckBlockedReason();
  if (reason) {
    setPromptAssistantError(reason);
    return false;
  }
  syncPromptAssistantDraftFromPanel();
  const contract = sourceInterface(state.activeSource);
  const promptInput = promptDirectionSignalControl();
  if (!state.activeSourceKey || !state.activeSource || state.activeSource.available === false || !contract || !promptInput) return false;
  const instructionsInput = document.querySelector("#prompt-assistant-instructions");
  if (!validatePromptInstructions(instructionsInput)) return false;
  const settings = normalizeExpectationSettings(state.expectations);
  const settingsError = Object.values(expectationSettingsErrors(settings))[0];
  if (settingsError) {
    setPromptAssistantError(settingsError);
    document.querySelector("#generation-panel details[data-expectations]")?.setAttribute("open", "");
    document.querySelector("#creative-direction-expectations")?.focus();
    return false;
  }
  const errors = expectationCheckParameterErrors(contract);
  if (Object.keys(errors).length) {
    state.serverFieldErrors = errors;
    state.formError = "Review the highlighted controls.";
    syncGenerationSubmissionState();
    focusFirstInvalid();
    return false;
  }
  const requestOwnerId = state.session.user.id;
  const signal = applicationStartupController.signal;
  const startPrompt = String(state.parameters[promptInput.id] ?? "");
  const requestInstructions = promptInstructionsForMode(state.promptAssistant);
  const snapshot = expectationSnapshotPayload(settings);
  state.expectationCheckStarting = true;
  state.formError = null;
  state.serverFieldErrors = {};
  setPromptAssistantError(null);
  syncGenerationSubmissionState();
  syncServerControls();
  try {
    const items = (await planGenerationPayloads(purpose, { signal })).map((item) => {
      delete item.prompt_assistant_run_id;
      return { ...item, prompt_assistant: { ...item.prompt_assistant, expectations: snapshot } };
    });
    if (state.session?.user?.id !== requestOwnerId) return false;
    // The check composes its own prompt; the probe image consumes that composition.
    state.compositionId = null;
    setPromptDirectionSignal("idle");
    const check = await submitGeneration("/api/prompt-assistant/checks", {
      purpose,
      assistant: {
        mode: state.promptAssistant.mode === "create" ? "create" : "refine",
        prompt: startPrompt,
        creative_direction: state.promptAssistant.creativeDirection || "",
        think: state.promptAssistant.think !== false,
        ...(instructionsInput?.disabled || !requestInstructions ? {} : { instructions: requestInstructions }),
      },
      expectations: snapshot.items,
      threshold: snapshot.threshold,
      max_attempts: snapshot.max_attempts,
      items,
    }, null, { signal });
    if (state.session?.user?.id !== requestOwnerId) return false;
    expectationStarts.set(check.id, { prompt: startPrompt, sourceName: state.activeSource?.display_name || null });
    state.expectationCheckStarting = false;
    expectationChecks?.track(check, { open: true });
    return true;
  } catch (error) {
    if (signal.aborted || state.session?.user?.id !== requestOwnerId) return false;
    if (error.code === "submission_status_unknown") { submissionRecovery?.start(); return false; }
    const message = error.message || "The expectation check could not start.";
    if (error.code === "vision_unavailable") state.promptAssistant.visionAvailable = false;
    const fields = normalizeParameterErrors(error.fields);
    const parameterErrors = Object.fromEntries(Object.entries(fields).filter(([id]) => interfaceInputs(contract).some((input) => input.id === id)));
    if (Object.keys(parameterErrors).length) {
      state.serverFieldErrors = parameterErrors;
      state.formError = message;
      focusFirstInvalid();
    }
    setPromptAssistantError(message);
    toast(message, "error");
    if (error.code === "expectation_check_active") void expectationChecks?.refresh().then(() => expectationChecks?.open());
    if (["source_republished", "source_unavailable"].includes(error.code)) await loadSources();
    return false;
  } finally {
    if (!signal.aborted && state.session?.user?.id === requestOwnerId) {
      state.expectationCheckStarting = false;
      syncGenerationSubmissionState();
      syncServerControls();
    }
  }
}

// The dialog's latest check changed (start, SSE, polling, stop, recovery).
function handleExpectationCheckChange(check, previous) {
  state.expectationCheck = check;
  const record = check ? expectationStarts.get(check.id) : null;
  const observedFinish = Boolean(check && previous?.id === check.id && checkIsActive(previous));
  if (check && !checkIsActive(check) && (observedFinish || (record && !record.finished))) finishExpectationCheck(check, record);
  if (check && checkIsActive(previous) !== checkIsActive(check)) scheduleActivityRefresh();
  syncServerControls();
}

function finishExpectationCheck(check, record) {
  if (record) record.finished = true;
  if (check.status === "passed") {
    const startPrompt = record?.prompt ?? check.starting_prompt;
    // The composition was consumed by the probe image; never reuse its id.
    const applied = Boolean(check.final_prompt) && shouldAutoApply(startPrompt, currentPromptValue()) && applyExpectationPrompt(check.final_prompt);
    const queued = check.queued?.generation_ids?.length || 0;
    toast(check.purpose === "generate" && check.planned_count > 1
      ? `Expectations met. ${queued} more ${queued === 1 ? "image was" : "images were"} queued.`
      : applied ? "Expectations met. The qualified prompt was applied."
        : "Expectations met. Your edited prompt was kept; use the qualified prompt from the check.", "success");
  } else if (check.status === "not_met") {
    const best = scoredAttempt(check);
    toast(best?.score !== null && best?.score !== undefined ? `Expectations weren't met. The best attempt scored ${best.score}/100.` : "Expectations weren't met.", "warning");
  } else if (check.status === "failed") {
    toast(check.error?.message || "The expectation check failed.", "error");
  }
}

function applyExpectationPrompt(prompt) {
  const input = promptDirectionSignalControl();
  if (!input || typeof prompt !== "string") return false;
  state.parameters[input.id] = normalizeInputValue(input, prompt);
  state.explicitParameterIds.add(input.id);
  state.compositionId = null;
  delete state.serverFieldErrors[input.id];
  persistActiveParameterState();
  const field = document.querySelector(`[data-control-id="${CSS.escape(input.id)}"]`);
  if (field) field.value = prompt;
  setPromptDirectionSignal("applied", prompt);
  syncParameterValidation(input.id);
  syncServerControls();
  return true;
}

async function deleteExpectationAttemptImages(ids) {
  const result = await api("/api/gallery/delete", { method: "POST", body: JSON.stringify({ generation_ids: ids, collection_ids: [] }) });
  const failures = (result?.items || []).filter((item) => item.status === "failed");
  await refreshAfterGalleryOperation({ operation: "confirm-delete", plan: { generation_ids: ids }, result });
  if (failures.length) throw new Error(`${ids.length - failures.length} of ${ids.length} attempt images were deleted. ${failures[0].message || ""}`.trim());
}

function openExpectationAttemptImage(generation) {
  const loaded = photoViewerGeneration(generation.id);
  if (loaded?.display_artifact?.kind === "image") openPhotoViewer(generation.id);
  else if (generation.content_url) window.open(generation.content_url, "_blank", "noopener");
}

async function composePromptEditor(button) {
  if (!state.promptAssistant.available) return;
  if (expectationsActive(state)) {
    // Apply & verify from the focused editor: keep its drafts, then verify them.
    if (expectationCheckBusy(state)) return;
    applyPromptEditor();
    await startExpectationCheck("apply");
    return;
  }
  const dialog = button.closest("#prompt-editor-dialog[open]");
  const editor = dialog?.querySelector("[data-prompt-editor-input]");
  const direction = dialog?.querySelector("#prompt-editor-creative-direction");
  const checkedMode = dialog?.querySelector('[name="prompt-editor-assistant-mode"]:checked');
  const thinkingMode = dialog?.querySelector("#prompt-editor-thinking-mode");
  const controlId = dialog?.dataset.promptControlId;
  const requestSourceKey = state.activeSourceKey;
  const requestRevision = structuredClone(sourceRevision(state.activeSource));
  if (
    !dialog ||
    !editor ||
    !direction ||
    !checkedMode ||
    !thinkingMode ||
    !controlId ||
    !requestSourceKey
  )
    return;

  const requestMode = checkedMode.value === "create" ? "create" : "refine";
  const requestPrompt = editor.value;
  const requestDirection = direction.value;
  const requestThink = thinkingMode.checked;
  const instructionsInput = dialog.querySelector("[data-prompt-instructions]");
  capturePromptInstructions(dialog, promptEditorInstructionOverrides);
  if (!validatePromptInstructions(instructionsInput)) return;
  const requestInstructions = instructionsInput?.disabled ? undefined : instructionsInput?.value;
  button.disabled = true;
  button.textContent = "Applying…";
  setPromptEditorAssistantError(dialog, null);
  setPromptEditorDirectionSignal("composing");
  let directionOutcome = "idle";
  let directionAppliedValue = null;
  try {
    const result = await api("/api/prompt-assistant/compose", {
      method: "POST",
      body: JSON.stringify({
        mode: requestMode,
        prompt: requestPrompt,
        creative_direction: requestDirection,
        think: requestThink,
        instructions: requestInstructions,
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
    if (
      editor.value !== requestPrompt || direction.value !== requestDirection ||
      promptEditorMode(dialog) !== requestMode || thinkingMode.checked !== requestThink ||
      (!instructionsInput?.disabled && instructionsInput?.value !== requestInstructions)
    ) {
      toast("Prompt composition finished after its inputs changed and was not applied.");
      return;
    }
    editor.value = result.prompt;
    updatePromptEditorStats(result.prompt);
    setPromptEditorDirectionSignal("applied", result.prompt);
    directionOutcome = "applied";
    directionAppliedValue = result.prompt;
    dialog.dataset.promptAssistantCompositionId = result.composition_id;
    dialog.dataset.promptAssistantModel = result.model;
    editor.focus();
    toast("Creative direction applied in the focused editor. Apply to keep it.", "success");
  } catch (error) {
    if (dialog.open && button.isConnected && sourceContextIsCurrent(requestSourceKey, requestRevision)) {
      const message = error.message || "Creative direction could not be applied.";
      setPromptEditorAssistantError(dialog, message);
      toast(message, "error");
    } else {
      toast(`Focused prompt composition failed: ${error.message}`, "error");
    }
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.textContent = "Apply Creative Direction";
    }
    setPromptEditorDirectionSignal(directionOutcome, directionAppliedValue);
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
  const checkpointHandle = event.target.closest("[data-checkpoint-drag-handle]");
  if (checkpointHandle && event.dataTransfer && !checkpointHandle.disabled) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(
      CHECKPOINT_DRAG_TYPE,
      JSON.stringify({
        sourceKey: checkpointHandle.dataset.checkpointSourceKey,
        parameterId: checkpointHandle.dataset.checkpointParameterId,
        tierId: checkpointHandle.dataset.checkpointTierId,
        value: checkpointHandle.dataset.checkpointValue,
      }),
    );
    checkpointHandle.closest("[data-checkpoint-card]")?.classList.add("is-dragging");
    return;
  }
  const collectionHandle = event.target.closest("[data-collection-drag-handle]");
  if (collectionHandle && event.dataTransfer) {
    const collectionId = collectionHandle.dataset.collectionId;
    // Only the open view's own folders are reorderable: its grid holds exactly the
    // children of the current location, so a drag can never cross parents.
    if (!collectionId || !reorderableCollection(collectionId)) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(COLLECTION_DRAG_TYPE, collectionId);
    collectionHandle.closest("[data-gallery-card='collection']")?.classList.add("is-dragging");
    return;
  }
  const image = event.target.closest("[data-gallery-artifact-id]");
  if (!image || !event.dataTransfer) return;
  event.dataTransfer.effectAllowed = "copy";
  event.dataTransfer.setData(GALLERY_ARTIFACT_DRAG_TYPE, image.dataset.galleryArtifactId);
  image.classList.add("is-dragging");
}

function handleDragEnd(event) {
  event.target.closest("[data-checkpoint-card]")?.classList.remove("is-dragging");
  clearCheckpointDropIndicators();
  event.target
    .closest("[data-collection-drag-handle]")
    ?.closest("[data-gallery-card='collection']")
    ?.classList.remove("is-dragging");
  clearCollectionDropIndicators();
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

function transferHasCheckpoint(dataTransfer) {
  return Array.from(dataTransfer?.types || []).includes(CHECKPOINT_DRAG_TYPE);
}

function transferHasCollection(dataTransfer) {
  return Array.from(dataTransfer?.types || []).includes(COLLECTION_DRAG_TYPE);
}

function reorderableCollection(collectionId) {
  const collection = state.collections.find((item) => item.id === collectionId);
  return Boolean(
    collection &&
      (collection.parent_id ?? null) === (state.currentCollectionId || null) &&
      !galleryFilterActive(state),
  );
}

function collectionGridForEvent(event) {
  const grid = event.target.closest("#gallery .collection-grid");
  return grid && !galleryFilterActive(state) ? grid : null;
}

function clearCollectionDropIndicators() {
  document
    .querySelectorAll(".collection-tile.is-drop-before, .collection-tile.is-drop-after")
    .forEach((element) => element.classList.remove("is-drop-before", "is-drop-after"));
}

// One geometric rule covers a drop on a tile and a drop in the wrapping grid's own gaps:
// the slot is the first tile that follows the pointer in row-major order, and null means
// the end of the list.
function collectionDropBeforeId(event, grid) {
  for (const tile of grid.querySelectorAll("[data-gallery-card='collection']")) {
    const rect = tile.getBoundingClientRect();
    const laterRow = rect.top > event.clientY;
    const sameRowAfter =
      rect.bottom > event.clientY && event.clientX < rect.left + rect.width / 2;
    if (laterRow || sameRowAfter) return tile.dataset.collectionId;
  }
  return null;
}

function showCollectionDropIndicator(event, grid) {
  clearCollectionDropIndicators();
  const tiles = [...grid.querySelectorAll("[data-gallery-card='collection']")];
  const beforeId = collectionDropBeforeId(event, grid);
  const target = beforeId
    ? tiles.find((tile) => tile.dataset.collectionId === beforeId)
    : tiles.at(-1);
  target?.classList.add(beforeId ? "is-drop-before" : "is-drop-after");
}

async function reorderCollection(collectionId, beforeId) {
  const parentId = state.currentCollectionId || null;
  const previous = state.collections;
  const order = reorderedCollectionIds(
    collectionSiblingIds(previous, parentId),
    collectionId,
    beforeId,
  );
  const reordered = applyCollectionOrder(previous, parentId, order);
  if (reordered === previous) return;
  state.collections = reordered;
  renderGallery();
  try {
    const collections = await api("/api/collections/order", {
      method: "PUT",
      body: JSON.stringify({ parent_id: parentId, collection_ids: order }),
    });
    // A refetch started before this write must not reinstate the old order.
    collectionsRequestToken += 1;
    state.collections = Array.isArray(collections) ? collections : state.collections;
    applyCollectionActivity({ counts: false });
    renderGallery();
  } catch (error) {
    state.collections = previous;
    renderGallery();
    toast(error.message || "The folder order could not be saved.", "error");
    if (error.code === "collection_order_stale") await loadCollections();
  }
}

function moveCollectionFromKeyboard(control, key) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(key)) return false;
  const collectionId = control.dataset.collectionId;
  if (!collectionId || !reorderableCollection(collectionId)) return false;
  const ids = collectionSiblingIds(state.collections, state.currentCollectionId || null);
  const index = ids.indexOf(collectionId);
  if (index < 0) return false;
  // Alt with an arrow key is browser history navigation, so every recognized gesture on
  // a reorderable folder is consumed, including the no-op at either end of the row.
  const beforeId =
    key === "ArrowLeft" ? (ids[index - 1] ?? collectionId)
    : key === "ArrowRight" ? (ids[index + 2] ?? null)
    : key === "Home" ? ids[0]
    : null;
  void reorderCollection(collectionId, beforeId);
  return true;
}

function checkpointTierForEvent(event) {
  const tier = event.target.closest("[data-checkpoint-tier]");
  const draft = state.sourcePickerDraft;
  if (
    !tier ||
    !draft ||
    draft.searchQuery ||
    tier.dataset.checkpointSourceKey !== draft.sourceKey
  ) {
    return null;
  }
  return tier;
}

function clearCheckpointDropIndicators() {
  document
    .querySelectorAll(".checkpoint-card.is-drop-before, .checkpoint-tier.is-drop-at-end")
    .forEach((element) => element.classList.remove("is-drop-before", "is-drop-at-end"));
}

function checkpointDropBeforeValue(event, tier) {
  const card = event.target.closest("[data-checkpoint-card]");
  if (!card || !tier.contains(card)) return null;
  const cards = [...tier.querySelectorAll("[data-checkpoint-card]")];
  const index = cards.indexOf(card);
  const rect = card.getBoundingClientRect();
  const after = event.clientX > rect.left + rect.width / 2;
  return after ? cards[index + 1]?.dataset.checkpointValue || null : card.dataset.checkpointValue;
}

function showCheckpointDropIndicator(event, tier) {
  clearCheckpointDropIndicators();
  const beforeValue = checkpointDropBeforeValue(event, tier);
  if (beforeValue) {
    tier
      .querySelector(
        `[data-checkpoint-card][data-checkpoint-value="${CSS.escape(beforeValue)}"]`,
      )
      ?.classList.add("is-drop-before");
  } else {
    tier.classList.add("is-drop-at-end");
  }
}

function handleDragEnter(event) {
  const checkpointTier = checkpointTierForEvent(event);
  if (checkpointTier && transferHasCheckpoint(event.dataTransfer)) {
    event.preventDefault();
    showCheckpointDropIndicator(event, checkpointTier);
    return;
  }
  const collectionGrid = collectionGridForEvent(event);
  if (collectionGrid && transferHasCollection(event.dataTransfer)) {
    event.preventDefault();
    showCollectionDropIndicator(event, collectionGrid);
    return;
  }
  const zone = imageDropzoneForEvent(event);
  if (!zone || !transferHasImageCandidate(event.dataTransfer)) return;
  event.preventDefault();
  zone.classList.add("is-drag-over");
}

function handleDragOver(event) {
  const checkpointTier = checkpointTierForEvent(event);
  if (checkpointTier && transferHasCheckpoint(event.dataTransfer)) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    showCheckpointDropIndicator(event, checkpointTier);
    return;
  }
  const collectionGrid = collectionGridForEvent(event);
  if (collectionGrid && transferHasCollection(event.dataTransfer)) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    showCollectionDropIndicator(event, collectionGrid);
    return;
  }
  const zone = imageDropzoneForEvent(event);
  if (!zone || !transferHasImageCandidate(event.dataTransfer)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
  zone.classList.add("is-drag-over");
}

function handleDragLeave(event) {
  const checkpointTier = checkpointTierForEvent(event);
  if (checkpointTier && transferHasCheckpoint(event.dataTransfer)) {
    if (!checkpointTier.contains(event.relatedTarget)) clearCheckpointDropIndicators();
    return;
  }
  const collectionGrid = collectionGridForEvent(event);
  if (collectionGrid && transferHasCollection(event.dataTransfer)) {
    if (!collectionGrid.contains(event.relatedTarget)) clearCollectionDropIndicators();
    return;
  }
  const zone = imageDropzoneForEvent(event);
  if (!zone || zone.contains(event.relatedTarget)) return;
  zone.classList.remove("is-drag-over");
}

async function handleDrop(event) {
  const checkpointTier = checkpointTierForEvent(event);
  if (checkpointTier && transferHasCheckpoint(event.dataTransfer)) {
    event.preventDefault();
    const beforeValue = checkpointDropBeforeValue(event, checkpointTier);
    clearCheckpointDropIndicators();
    let payload = null;
    try {
      payload = JSON.parse(event.dataTransfer.getData(CHECKPOINT_DRAG_TYPE));
    } catch {
      return;
    }
    if (
      payload?.sourceKey === checkpointTier.dataset.checkpointSourceKey &&
      payload?.parameterId === checkpointTier.dataset.checkpointParameterId &&
      typeof payload?.value === "string"
    ) {
      if (beforeValue !== payload.value) {
        moveSourcePickerCheckpoint(
          payload.value,
          checkpointTier.dataset.checkpointTier,
          beforeValue,
        );
      }
    }
    return;
  }
  const collectionGrid = collectionGridForEvent(event);
  if (collectionGrid && transferHasCollection(event.dataTransfer)) {
    event.preventDefault();
    const beforeId = collectionDropBeforeId(event, collectionGrid);
    clearCollectionDropIndicators();
    const collectionId = event.dataTransfer.getData(COLLECTION_DRAG_TYPE);
    if (collectionId && reorderableCollection(collectionId)) {
      await reorderCollection(collectionId, beforeId);
    }
    return;
  }
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
  gallery.classList.toggle("has-prompt-groups", state.galleryLayout !== "classic");
  gallery.classList.toggle("has-classic-gallery", state.galleryLayout === "classic");
  const groupFocus = document.activeElement?.closest(".prompt-group-header") ? { ...document.activeElement.dataset } : null;
  const focused = gallery.contains(document.activeElement) ? document.activeElement : null;
  const focusedCard = focused?.closest("[data-gallery-card]");
  const focusedIndex = focusedCard ? [...gallery.querySelectorAll("[data-gallery-card]")].indexOf(focusedCard) : -1;
  galleryHover.preserveDuring(() => {
    const mode = favoritesMode(state);
    reconcileGallery(gallery, galleryMarkup(visibleGenerations(), {
      status: state.galleryStatus,
      message: state.galleryMessage,
      // A filtered view is generation cards only: a folder tile would offer a
      // subtree whose contents ignore the filter.
      collections: galleryFilterActive(state) ? [] : state.collections,
      currentCollectionId: state.currentCollectionId,
      favoritesMode: mode,
      excludedCheckpointRanks: excludedCheckpointRanks(state),
      excludedLoraRanks: excludedLoraRanks(state),
      promptGroups: state.galleryLayout === "classic" ? null : galleryGroups?.options(),
      galleryLayout: state.galleryLayout,
      hideThumbnails: currentViewHidesThumbnails(),
      checkpointTiers: state.checkpointTiers,
      loraTiers: state.loraTiers,
    }));
  });
  applyCollectionActivity({ counts: false });
  if (focused?.dataset.action) {
    const key = focused.dataset.generationId ? "generationId" : "collectionId";
    const replacement = [...gallery.querySelectorAll("[data-action]")].find((control) =>
      control.dataset.action === focused.dataset.action && control.dataset[key] === focused.dataset[key]);
    const cards = gallery.querySelectorAll("[data-gallery-card]");
    const fallback = cards[Math.min(focusedIndex, cards.length - 1)]?.querySelector("[data-action]");
    (replacement || fallback || document.querySelector('[data-action="toggle-favorites-filter"]'))?.focus({ preventScroll: true });
  }
  if (groupFocus) {
    const key = Object.keys(groupFocus)[0];
    [...gallery.querySelectorAll(".prompt-group-header button")].find((button) => button.dataset[key] === groupFocus[key])?.focus({ preventScroll: true });
  }
  galleryGroups?.afterRender();
  gallerySelection?.sync();
  const sentinel = document.querySelector("#gallery-sentinel");
  if (sentinel) sentinel.hidden = !galleryNextCursor();
}

function openCollectionDialog(mode, invokingControl, collectionId) {
  const dialog = document.querySelector("#collection-dialog");
  const collection = mode === "rename"
    ? state.collections.find((item) => item.id === collectionId) || null
    : null;
  if (!dialog || dialog.open || (mode === "rename" && !collection)) return;
  if (
    mode === "create" &&
    state.currentCollectionId &&
    collectionDepth(state.collections, state.currentCollectionId) >= 5
  ) {
    toast("Collections cannot be nested more than 5 levels deep.", "error");
    return;
  }
  collectionDialogReturnFocus = invokingControl;
  dialog.innerHTML = collectionDialogMarkup({ mode, collection });
  dialog.showModal();
  queueMicrotask(() => {
    const input = dialog.querySelector("[name=name]");
    input?.focus({ preventScroll: true });
    if (mode === "rename") input?.select();
  });
}

function syncCollectionNameValidation(input) {
  const form = input?.closest("#collection-form");
  if (!form) return false;
  const value = input.value.trim();
  const error = form.querySelector("#collection-name-error");
  const submit = form.querySelector('[type="submit"]');
  let message = "";
  if (!value) message = "Enter a collection name.";
  else if (value.length > 100) message = "Use at most 100 characters.";
  input.setAttribute("aria-invalid", String(Boolean(message)));
  if (error) error.textContent = message;
  if (submit) submit.disabled = Boolean(message);
  return !message;
}

async function submitCollectionForm(form) {
  const input = form.elements.name;
  if (!syncCollectionNameValidation(input)) {
    input.focus();
    return;
  }
  const mode = form.dataset.collectionMode;
  const collectionId = form.dataset.collectionId;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const collection = await api(
      mode === "rename"
        ? `/api/collections/${encodeURIComponent(collectionId)}`
        : "/api/collections",
      {
        method: mode === "rename" ? "PATCH" : "POST",
        body: JSON.stringify({
          name: input.value.trim(),
          ...(mode === "rename"
            ? {}
            : { parent_id: state.currentCollectionId }),
        }),
      },
    );
    state.collections = [
      ...state.collections.filter((item) => item.id !== collection.id),
      collection,
    ];
    closeCollectionDialog("saved");
    renderCollectionBarHost();
    renderGallery();
    await loadCollections();
    toast(mode === "rename" ? "Collection renamed." : "Collection created.", "success");
  } catch (error) {
    submit.disabled = false;
    throw error;
  }
}

function closeCollectionDialog(returnValue) {
  const dialog = document.querySelector("#collection-dialog");
  if (dialog?.open) dialog.close(returnValue);
}

function handleCollectionDialogClose() {
  const previous = collectionDialogReturnFocus;
  collectionDialogReturnFocus = null;
  queueMicrotask(() => {
    const fallback = document.querySelector('[data-action="new-collection"]');
    (previous?.isConnected ? previous : fallback)?.focus({ preventScroll: true });
  });
}

function openCollectionDeleteDialog(invokingControl, collectionId) {
  const dialog = document.querySelector("#collection-delete-dialog");
  const collection =
    state.collections.find((item) => item.id === collectionId) || null;
  if (!dialog || dialog.open || !collection) return;
  const subtree = collectionSubtree(state.collections, collection.id);
  collectionDeleteReturnFocus = invokingControl;
  dialog.innerHTML = collectionDeleteDialogMarkup(collection, subtree);
  dialog.showModal();
  queueMicrotask(() => dialog.querySelector('[type="submit"]')?.focus());
}

async function submitCollectionDelete(form) {
  const collectionId = form.dataset.collectionId;
  const collection = state.collections.find((item) => item.id === collectionId);
  if (!collection) return;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  const response = await fetch(
    `/api/collections/${encodeURIComponent(collectionId)}`,
    {
      method: "DELETE",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": state.session.csrf_token },
    },
  );
  if (![202, 204].includes(response.status)) {
    const payload = await response.json();
    submit.disabled = false;
    throw new Error(payload.error?.message || "Collection deletion failed.");
  }
  const subtreeIds = new Set(
    collectionSubtree(state.collections, collectionId).map((item) => item.id),
  );
  state.collections = state.collections.filter((item) => !subtreeIds.has(item.id));
  state.generations = state.generations.filter(
    (generation) => !subtreeIds.has(generation.collection_id),
  );
  closeCollectionDeleteDialog("deleted");
  toast(
    response.status === 202
      ? "Collection removed. Active generations are being cancelled and deleted."
      : "Collection and its contents were deleted.",
    "success",
  );
  openCollectionRoute(collection.parent_id || null);
}

function closeCollectionDeleteDialog(returnValue) {
  const dialog = document.querySelector("#collection-delete-dialog");
  if (dialog?.open) dialog.close(returnValue);
}

function handleCollectionDeleteDialogClose() {
  const previous = collectionDeleteReturnFocus;
  collectionDeleteReturnFocus = null;
  queueMicrotask(() => {
    const fallback = document.querySelector("#collection-bar a, #collection-bar button");
    (previous?.isConnected ? previous : fallback)?.focus({ preventScroll: true });
  });
}

function openMoveDialog(generationId, invokingControl) {
  const dialog = document.querySelector("#move-dialog");
  const generation = state.generations.find((item) => item.id === generationId);
  if (!dialog || dialog.open || !generation) return;
  moveDialogReturnFocus = invokingControl;
  dialog.innerHTML = moveDialogMarkup(generation, state.collections);
  dialog.showModal();
  queueMicrotask(() => dialog.querySelector("input:checked")?.focus());
}

async function submitGenerationMove(form) {
  const generationId = form.dataset.generationId;
  const generation = state.generations.find((item) => item.id === generationId);
  const selected = form.querySelector('[name="collection_id"]:checked');
  if (!generation || !selected) return;
  const previousCollectionId = generation.collection_id ?? null;
  const collectionId = selected.value || null;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const moved = await api(
      `/api/generations/${encodeURIComponent(generationId)}/move`,
      {
        method: "POST",
        body: JSON.stringify({ collection_id: collectionId }),
      },
    );
    updateCollectionGenerationCount(previousCollectionId, -1);
    updateCollectionGenerationCount(moved.collection_id, 1);
    const index = state.generations.findIndex((item) => item.id === generationId);
    if (index >= 0) state.generations[index] = moved;
    closeMoveDialog("moved");
    if (!generationBelongsToView(moved)) {
      removeGalleryGeneration(generationId);
    } else {
      upsertGalleryCard(moved);
    }
    renderGallery();
    await loadCollections();
    const destination = moved.collection_id
      ? state.collections.find((item) => item.id === moved.collection_id)?.name ||
        "collection"
      : "Home";
    toast(`Moved to ${destination}.`, "success");
  } catch (error) {
    submit.disabled = false;
    throw error;
  }
}

function updateCollectionGenerationCount(collectionId, delta) {
  if (!collectionId) return;
  state.collections = state.collections.map((collection) =>
    collection.id === collectionId
      ? {
          ...collection,
          generation_count: Math.max(
            0,
            (Number(collection.generation_count) || 0) + delta,
          ),
        }
      : collection,
  );
}

function closeMoveDialog(returnValue) {
  const dialog = document.querySelector("#move-dialog");
  if (dialog?.open) dialog.close(returnValue);
}

function handleMoveDialogClose() {
  const previous = moveDialogReturnFocus;
  moveDialogReturnFocus = null;
  queueMicrotask(() => {
    const fallback = document.querySelector("#gallery [data-action=move-generation]");
    (previous?.isConnected ? previous : fallback)?.focus({ preventScroll: true });
  });
}

async function toggleCollectionPreviews(collectionId) {
  const collection = state.collections.find((item) => item.id === collectionId);
  if (!collection) return;
  const next = collection.previews_enabled === false;
  collection.previews_enabled = next;
  renderGallery();
  try {
    await api(`/api/collections/${encodeURIComponent(collectionId)}`, {
      method: "PATCH",
      body: JSON.stringify({ previews_enabled: next }),
    });
  } catch {
    collection.previews_enabled = !next;
    renderGallery();
    toast("Collection preview setting could not be saved.", "error");
  }
}

function upsertGalleryCard(generation) {
  const card = document.querySelector(`#gallery [data-gallery-card="generation"][data-generation-id="${CSS.escape(generation.id)}"]`);
  if (!card) { renderGallery(); return; }
  galleryHover.preserveDuring(() => reconcileGalleryCard(card, galleryCardMarkup(generation, { hideThumbnail: currentViewHidesThumbnails(), checkpointTiers: state.checkpointTiers, loraTiers: state.loraTiers })));
  gallerySelection?.sync();
}

function loadMore() {
  if (galleryPageRequest && galleryPageController && !galleryPageController.signal.aborted) return galleryPageRequest;
  if (!galleryNextCursor()) return Promise.resolve();
  state.loadingMore = true;
  const navigationToken = collectionNavigationToken;
  const requestRoute = currentGalleryRoute();
  const requestedLayout = state.galleryLayout;
  const originalCursor = galleryNextCursor();
  const cursor = requestedLayout === "classic" ? originalCursor : galleryGroups?.paginationCursor(originalCursor) || originalCursor;
  const { controller, unlink } = galleryReadController();
  galleryPageController = controller;
  const request = (async () => {
    try {
      const page = await api(galleryPageUrl(cursor, requestRoute), { signal: controller.signal, deadlineMs: 15_000, operation: "Gallery page" });
      if (controller.signal.aborted || navigationToken !== collectionNavigationToken || requestRoute !== currentGalleryRoute() || requestedLayout !== state.galleryLayout) return;
      if (page.next_cursor && [cursor, originalCursor].includes(page.next_cursor)) throw new Error("The gallery page did not advance. Try again.");
      if (cursor !== originalCursor && !state.gallerySkippedCursor) state.gallerySkippedCursor = originalCursor;
      const known = new Set(state.generations.map((item) => item.id));
      state.generations.push(...page.items.filter((item) => !known.has(item.id)));
      state.nextCursor = page.next_cursor;
      renderGallery();
      setupPaginationObserver();
      return page;
    } finally {
      unlink();
      if (galleryPageController === controller) {
        galleryPageController = null;
        galleryPageRequest = null;
        if (navigationToken === collectionNavigationToken) state.loadingMore = false;
      }
    }
  })();
  galleryPageRequest = request;
  return request;
}

function setupPaginationObserver() {
  state.observer?.disconnect();
  const sentinel = document.querySelector("#gallery-sentinel");
  if (!sentinel || !galleryNextCursor() || !("IntersectionObserver" in window)) return;
  state.observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore().catch(() => {});
    },
    { root: document.querySelector("#gallery-viewport"), rootMargin: "600px" },
  );
  state.observer.observe(sentinel);
}

async function refreshGeneration(
  id,
  {
    insertIf = (detail) => generationBelongsToView(detail),
  } = {},
) {
  const refreshToken = generationRefreshGate.issue(id);
  const navigationToken = collectionNavigationToken;
  try {
    let detail = await api(`/api/generations/${id}`, { signal: applicationStartupController?.signal });
    if (TERMINAL_GENERATION_STATUSES.has(detail.status) && pendingGenerationIds.delete(id)) syncServerControls();
    if (!generationRefreshGate.isCurrent(id, refreshToken) || navigationToken !== collectionNavigationToken) return;
    const index = state.generations.findIndex((item) => item.id === id);
    const previous = index >= 0 ? state.generations[index] : null;
    if (!generationBelongsToView(detail)) {
      if (index >= 0) removeGalleryGeneration(id);
      return;
    }
    if (
      previous?.progress &&
      !TERMINAL_GENERATION_STATUSES.has(detail.status) &&
      progressUpdatedAt(previous.progress) > progressUpdatedAt(detail.progress)
    ) {
      detail = { ...detail, progress: previous.progress };
    }
    const inserted = index < 0;
    const becameViewable =
      index >= 0 &&
      state.generations[index].display_artifact?.kind !== "image" &&
      detail.display_artifact?.kind === "image";
    if (index >= 0) state.generations[index] = detail;
    else if (
      generationBelongsToView(detail) &&
      insertIf(detail)
    )
      state.generations.unshift(detail);
    else return;
    state.generations = sortGenerationsNewestFirst(state.generations);
    const galleryStructureChanged =
      inserted ||
      state.generations[index]?.id !== id ||
      previous?.prompt_fingerprint !== detail.prompt_fingerprint ||
      (galleryFilterActive(state) && galleryGenerationMatches(state, previous) !== galleryGenerationMatches(state, detail));
    if (galleryStructureChanged) renderGallery();
    else upsertGalleryCard(detail);
    syncServerControls();
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
    if (!generationRefreshGate.isCurrent(id, refreshToken) || navigationToken !== collectionNavigationToken) return;
    if (error.status === 404) removeGeneration(id);
  }
}

async function recall(id) {
  const recalled = await api(`/api/generations/${id}/recall`);
  if (!recalled.available) {
    toast(recalled.reason || "Exact recall is unavailable.", "error");
    return;
  }
  const preRecallParameters = structuredClone(state.parameters);
  state.promptEditorDirty = true;
  persistBrowserDraft();
  syncServerControls();
  const runtimeWarning = applyRecalledComfyuiInstance(recalled);
  state.modelSelectionsBySourceRevision = new Map();
  if (recalled.source_available === false) {
    const recalledState = overwriteWithRecall(state, recalled, sourceInterface(state.activeSource));
    state.parameters = recalledState.parameters;
    state.parameters = applyRecallSeedMode(preRecallParameters, sourceInterface(state.activeSource), state.parameters);
    state.explicitParameterIds = recalledState.explicitParameterIds;
    state.promptAssistant = recalledState.promptAssistant;
    state.expectations = recalledState.expectations;
    state.compositionId = null;
    state.serverFieldErrors = {};
    state.formError = null;
    state.selectedPreset = null;
    collapseActiveModelSelectionsFromParameters();
    persistActiveParameterState();
    persistCreativeDirectionDraft();
    renderPanel();
    closePanel(false);
    document.querySelector("#generation-panel")?.scrollIntoView({ block: "start" });
    toast(
      [
        recalled.reason || "Historical settings loaded into the current source.",
        runtimeWarning,
      ]
        .filter(Boolean)
        .join(" "),
      "warning",
    );
    return;
  }
  const key = recalled.source_key || recalled.profile_id;
  const source = await api(`/api/workflows/${encodeURIComponent(key)}`);
  const contract = sourceInterface(source);
  const recalledState = overwriteWithRecall(state, recalled, contract);
  state.activeSourceKey = key;
  persistActiveSourceKey();
  state.activeSource = { ...source, interface: contract };
  state.sourcePickerDialogOpen = false;
  state.sourcePickerDraft = null;
  state.pendingSourceMigration = null;
  loadRecentResolutionsForActiveSource();
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
  state.parameters = applyRecallSeedMode(preRecallParameters, sourceInterface(state.activeSource), state.parameters);
  state.promptAssistant = recalledState.promptAssistant;
  state.expectations = recalledState.expectations;
  state.compositionId = null;
  state.serverFieldErrors = {};
  state.formError = null;
  state.selectedPreset = null;
  collapseActiveModelSelectionsFromParameters();
  persistActiveParameterState();
  persistCreativeDirectionDraft();
  renderPanel();
  closePanel(false);
  document.querySelector("#generation-panel")?.scrollIntoView({ block: "start" });
  toast(
    runtimeWarning || "Exact historical settings loaded. Press Generate when ready.",
    runtimeWarning ? "warning" : "success",
  );
}

function applyRecalledComfyuiInstance(recalled) {
  const recalledState = recalledComfyuiInstanceState(state, recalled);
  Object.assign(state, recalledState.state);
  return recalledState.notice;
}

function favoriteToggleFocus(button) {
  const focused = document.activeElement === button;
  const route = currentGalleryRoute();
  const card = button.closest("[data-gallery-card]");
  const index = [...document.querySelectorAll("#gallery [data-gallery-card]")].indexOf(card);
  const idAttribute = button.dataset.generationId ? "data-generation-id" : "data-collection-id";
  const id = button.getAttribute(idAttribute);
  return () => {
    if (!focused || route !== currentGalleryRoute()) return;
    if (button.isConnected) {
      button.focus({ preventScroll: true });
      return;
    }
    const replacement = document.querySelector(
      `#gallery [data-action="${CSS.escape(button.dataset.action)}"][${idAttribute}="${CSS.escape(id)}"]`,
    );
    const cards = document.querySelectorAll("#gallery [data-gallery-card]");
    const fallback = cards[Math.min(index, cards.length - 1)]?.querySelector("[data-action]");
    (replacement || fallback || document.querySelector('[data-action="toggle-favorites-filter"]'))?.focus({ preventScroll: true });
  };
}

async function toggleFavorite(id, button) {
  const generation = photoViewerGeneration(id);
  if (!generation) return;
  const wasFavorite = Boolean(generation.is_favorite);
  const restoreFocus = favoriteToggleFocus(button);
  button.disabled = true;
  try {
    let favorite = null;
    if (wasFavorite) {
      await api(`/api/generations/${encodeURIComponent(id)}/favorite`, { method: "DELETE" });
    } else {
      favorite = await api(`/api/generations/${encodeURIComponent(id)}/favorite`, { method: "PUT" });
    }
    generationRefreshGate.invalidate(id);
    const updated = { ...generation, ...(favorite?.generation || {}), is_favorite: !wasFavorite };
    state.generations = state.generations.map((item) => item.id === id ? updated : item);
    if (state.photoViewerGenerationId === id) state.photoViewerDetachedGeneration = updated;
    if (favoritesFilterActive(favoritesMode(state))) renderGallery();
    else if (state.generations.some((item) => item.id === id)) upsertGalleryCard(updated);
    updatePhotoViewerFavoriteControl();
    toast(wasFavorite ? "Removed from Favorites." : "Added to Favorites.", "success");
  } finally {
    if (button.isConnected) button.disabled = false;
    restoreFocus();
  }
}

async function toggleCollectionFavorite(id, button) {
  const collection = state.collections.find((item) => item.id === id);
  if (!collection) return;
  const wasFavorite = Boolean(collection.is_favorite);
  const restoreFocus = favoriteToggleFocus(button);
  button.disabled = true;
  try {
    let updated;
    if (wasFavorite) {
      await api(`/api/collections/${encodeURIComponent(id)}/favorite`, { method: "DELETE" });
      updated = { ...collection, is_favorite: false };
    } else {
      updated = await api(`/api/collections/${encodeURIComponent(id)}/favorite`, { method: "PUT" });
    }
    state.collections = state.collections.map((item) => item.id === id ? updated : item);
    if (favoritesFilterActive(favoritesMode(state))) renderGallery();
    else {
      const tile = document.querySelector(`#gallery .collection-tile[data-collection-id="${CSS.escape(id)}"]`);
      if (tile) galleryHover.preserveDuring(() => reconcileGalleryCard(tile, collectionTileMarkup(updated)));
      gallerySelection?.sync();
    }
    toast(wasFavorite ? "Removed from Favorites." : "Added to Favorites.", "success");
  } finally {
    if (button.isConnected) button.disabled = false;
    restoreFocus();
  }
}

function toggleFavoritesFilter() {
  state.favoritesMode = nextFavoritesMode(favoritesMode(state));
  syncFavoritesFilterControl();
  void reloadGalleryFilters();
}

function syncRankFilterControls() {
  const excluded = excludedCheckpointRanks(state);
  for (const button of document.querySelectorAll('[data-action="toggle-checkpoint-rank-filter"]')) {
    const rank = button.dataset.checkpointRank;
    button.setAttribute("aria-pressed", String(!excluded.includes(rank)));
    button.title = `${excluded.includes(rank) ? "Show" : "Hide"} images from rank ${rank}${rank === "C" ? " (includes unranked models)" : ""}`;
  }
}

function syncLoraRankFilterControls() {
  const excluded = excludedLoraRanks(state);
  for (const button of document.querySelectorAll('[data-action="toggle-lora-rank-filter"]')) {
    const rank = button.dataset.loraRank;
    button.setAttribute("aria-pressed", String(!excluded.includes(rank)));
    button.title = `${excluded.includes(rank) ? "Show" : "Hide"} images that use a rank ${rank} LoRA${rank === "C" ? " (includes unranked LoRAs)" : ""}`;
  }
  for (const label of document.querySelectorAll(".lora-rank-launch-label")) label.textContent = loraRankFilterSummary(state);
}

function toggleLoraRankFilter(rank) {
  if (!CHECKPOINT_TIER_DEFINITIONS.some(({ id }) => id === rank)) return;
  const excluded = excludedLoraRanks(state);
  state.excludedLoraRanks = excluded.includes(rank) ? excluded.filter((id) => id !== rank) : [...excluded, rank];
  syncLoraRankFilterControls();
  void reloadGalleryFilters();
}

// One rank per LoRA identity, shared by every workflow. Saved optimistically like
// checkpoint ranks: the badge moves at once and rolls back if the save fails.
async function changeLoraRank(identity, step, { focus = null } = {}) {
  if (loraRankChange?.status === "saving" || !/^lr1_[0-9a-f]{64}$/.test(identity || "")) return;
  const previous = state.loraTiers;
  let move;
  if (step === null) {
    if (loraRankChange?.identity !== identity || loraRankChange.status !== "error") return;
    move = { from: loraRankChange.from, to: loraRankChange.to, ranks: normalizeLoraRanks(previous) };
    const cleaned = Object.fromEntries(Object.entries(move.ranks).map(([tier, values]) => [tier, values.filter((value) => value !== identity)]));
    cleaned[move.to].push(identity);
    move.ranks = cleaned;
  } else {
    move = stepLoraRank(previous, identity, step);
  }
  if (!move) return;
  const change = { identity, from: move.from, to: move.to, status: "saving" };
  const sessionId = state.session?.user?.id;
  const wasFocused = focus && document.activeElement === focus;
  const stepValue = focus?.dataset?.rankStep || "-1";
  loraRankChange = change;
  state.loraTiers = move.ranks;
  renderGallery();
  renderPhotoViewer();
  loraManagerController?.refresh();
  const saved = await settingsSync?.save();
  if (sessionId !== state.session?.user?.id || loraRankChange !== change) return;
  if (saved) change.status = "saved";
  else {
    if (settingsEqual(state.loraTiers, move.ranks)) state.loraTiers = previous;
    settingsSync?.persistLocal();
    change.status = "error";
    change.message = state.sharedSettingsMessage || "The rank could not be saved. Try again.";
    toast(change.message, "error");
  }
  renderGallery();
  renderPhotoViewer();
  loraManagerController?.refresh();
  if (wasFocused) {
    const scope = focus.closest("dialog") || document;
    const controls = [...scope.querySelectorAll(`[data-lora-identity="${CSS.escape(identity)}"][data-rank-step]`)];
    (controls.find((control) => control.dataset.rankStep === stepValue && !control.disabled) || controls.find((control) => !control.disabled))?.focus({ preventScroll: true });
  }
  if (excludedLoraRanks(state).length) void reloadGalleryFilters();
  if (saved) setTimeout(() => {
    if (loraRankChange !== change) return;
    loraRankChange = null;
    renderPhotoViewer();
    loraManagerController?.refresh();
  }, 2600);
}

function toggleCheckpointRankFilter(rank) {
  if (!CHECKPOINT_TIER_DEFINITIONS.some(({ id }) => id === rank)) return;
  const excluded = excludedCheckpointRanks(state);
  state.excludedCheckpointRanks = excluded.includes(rank) ? excluded.filter((id) => id !== rank) : [...excluded, rank];
  syncRankFilterControls();
  void reloadGalleryFilters();
}

async function reloadGalleryFilters() {
  if (!document.querySelector("#gallery")) return;
  const token = ++collectionNavigationToken;
  cancelGalleryReads();
  state.observer?.disconnect();
  if (document.querySelector("#photo-viewer")?.open) state.photoViewerDetachedGeneration = photoViewerGeneration(state.photoViewerGenerationId);
  for (const generation of state.generations) {
    if (!TERMINAL_GENERATION_STATUSES.has(generation.status)) pendingGenerationIds.add(generation.id);
  }
  state.generations = [];
  state.nextCursor = null;
  state.gallerySkippedCursor = null;
  state.loadingMore = false;
  startupGalleryBoundary = null;
  state.galleryStatus = "loading";
  renderGallery();
  await loadStartupGallery(applicationStartupController?.signal, { navigationToken: token });
  if (token === collectionNavigationToken) renderPhotoViewer();
}

function observeTopbarHeight() {
  topbarResizeObserver?.disconnect();
  const topbar = document.querySelector(".topbar");
  if (!topbar) return;
  const measure = () => document.documentElement.style.setProperty("--topbar-height", `${Math.ceil(topbar.getBoundingClientRect().height)}px`);
  measure();
  topbarResizeObserver = new ResizeObserver(measure);
  topbarResizeObserver.observe(topbar);
}

async function openDetail(id) {
  const detail = await api(`/api/generations/${id}`);
  const dialog = document.querySelector("#detail-dialog");
  dialog.dataset.generationId = id;
  dialog.innerHTML = detailMarkup(detail);
  dialog.showModal();
}

function photoViewerGeneration(id) {
  return (
    state.generations.find((item) => item.id === id) ||
    (state.photoViewerDetachedGeneration?.id === id ? state.photoViewerDetachedGeneration : null) ||
    null
  );
}

function photoViewerGenerations() {
  return visibleGenerations().filter((generation) => generation.display_artifact?.kind === "image");
}

function photoViewerNavigation(id) {
  const generations = photoViewerNavigationItems(id);
  const index = generations.findIndex((generation) => generation.id === id);
  return {
    hasOlder: index >= 0 && (index < generations.length - 1 || Boolean(galleryNextCursor())),
    hasNewer: index > 0,
  };
}

function photoViewerNavigationItems(id) {
  const generations = photoViewerGenerations();
  const anchor = photoViewerGeneration(id);
  return anchor && !generations.some((item) => item.id === id)
    ? sortGenerationsNewestFirst([...generations, anchor]) : generations;
}

function photoViewerGenerationDock() {
  syncSubmissionSnapshot();
  return {
    activity: generationActivityMarkup(generationActivitySnapshot()),
  };
}

function renderPhotoViewer() {
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog?.open || !state.photoViewerGenerationId) return;
  const generation = photoViewerGeneration(state.photoViewerGenerationId);
  if (!generation?.display_artifact || generation.display_artifact.kind !== "image") {
    closePhotoViewer();
    return;
  }
  const key = photoKey(generation.display_artifact);
  if (photoViewerLoadKey !== key) {
    photoViewerLoadKey = key;
    photoViewerLoading = true;
    photoViewerLoadError = null;
    const revision = ++photoViewerLoadRevision;
    photoViewerImages ||= createPhotoViewerImages();
    photoViewerImages.show(generation.display_artifact, (image) => {
      if (revision !== photoViewerLoadRevision || !dialog.open) return false;
      const latest = photoViewerGeneration(state.photoViewerGenerationId);
      if (photoKey(latest?.display_artifact) !== key) { renderPhotoViewer(); return false; }
      const changed = photoKey(photoViewerDisplayed?.display_artifact) !== key;
      photoViewerImage = image;
      photoViewerDisplayed = { ...latest };
      photoViewerLoading = false;
      if (changed) resetPhotoViewerView(state.photoViewerMode);
      renderPhotoViewer();
      schedulePhotoViewerFrame(true);
    }).catch((error) => {
      if (revision !== photoViewerLoadRevision || !dialog.open || error.name === "AbortError") return;
      photoViewerLoading = false;
      photoViewerLoadError = "Could not load image. Try again.";
      renderPhotoViewer();
    });
  }
  // Metadata can change during a load. The displayed artifact and action targets
  // remain together until the requested original has finished decoding.
  const displayed = photoViewerDisplayed
    ? { ...(photoViewerGeneration(photoViewerDisplayed.id) || photoViewerDisplayed), display_artifact: photoViewerDisplayed.display_artifact }
    : { ...generation, display_artifact: null };
  const dock = { ...photoViewerGenerationDock(), checkpointTiers: state.checkpointTiers, checkpointRankChange, loraTiers: state.loraTiers, loraRankChange, loading: photoViewerLoading || photoViewerPaging, loadError: photoViewerLoadError };
  const host = dialog.querySelector(".photo-viewer-host");
  reconcilePhotoViewer(host, photoViewerMarkup(displayed, photoViewerNavigation(generation.id), state.photoViewerMode, state.photoViewerPlaybackMode, dock), photoViewerImage);
  const activityHost = host.querySelector(".photo-viewer-activity-host");
  if (activityHost) activityHost.dataset.markup = dock.activity;
  updatePhotoViewerFullscreenControl();
  updatePhotoViewerNextIn();
  syncPhotoViewerPreload();
}

function syncPhotoViewerPreload() {
  const eligible = document.querySelector("#photo-viewer")?.open && !document.hidden &&
    state.photoViewerPlaybackMode === "hold" && !photoViewerLoading && !photoViewerPaging && !photoViewerLoadError &&
    photoViewerDisplayed?.id === state.photoViewerGenerationId;
  photoViewerPreloader.update(eligible ? photoViewerPreloadArtifact(visibleGenerations(), photoViewerDisplayed.id, photoViewerDirection) : null);
}

function openPhotoViewer(id) {
  const generation = photoViewerGeneration(id);
  if (!generation?.display_artifact || generation.display_artifact.kind !== "image") return;
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog) return;
  photoViewerNavigationRevision++;
  state.photoViewerPlaybackMode = "hold";
  if (!dialog.open) resetPhotoViewerView();
  state.photoViewerGenerationId = id;
  if (!dialog.open) dialog.showModal();
  renderPhotoViewer();
  notePhotoViewerActivity();
  dialog.querySelector("[data-action=close-photo]")?.focus({ preventScroll: true });
}

async function navigatePhotoViewer(direction) {
  if (!state.photoViewerGenerationId || !["older", "newer"].includes(direction)) return;
  photoViewerDirection = direction;
  const revision = ++photoViewerNavigationRevision;
  const origin = state.photoViewerGenerationId;
  const route = collectionNavigationToken;
  const layout = state.galleryLayout;
  const current = () => revision === photoViewerNavigationRevision && route === collectionNavigationToken && layout === state.galleryLayout && document.querySelector("#photo-viewer")?.open;
  state.photoViewerPlaybackMode = "hold";
  photoViewerRetryDirection = null;
  photoViewerLoadError = null;
  photoViewerPaging = false;
  try {
    let generations = photoViewerNavigationItems(origin);
    let index = generations.findIndex((generation) => generation.id === origin);
    let target = index < 0 ? null : generations[index + (direction === "older" ? 1 : -1)];
    const cursors = new Set();
    while (!target && index >= 0 && direction === "older" && galleryNextCursor()) {
      const cursor = galleryNextCursor();
      if (cursors.has(cursor)) throw new Error("The gallery page did not advance.");
      cursors.add(cursor);
      photoViewerPaging = true;
      renderPhotoViewer();
      const page = await loadMore();
      if (!current()) return;
      if (!page) break;
      generations = photoViewerNavigationItems(origin);
      index = generations.findIndex((generation) => generation.id === origin);
      target = index < 0 ? null : generations[index + 1];
    }
    if (!current()) return;
    if (target) state.photoViewerGenerationId = target.id;
  } catch (error) {
    if (!current() || error.name === "AbortError") return;
    photoViewerRetryDirection = direction;
    photoViewerLoadError = "Could not load the next gallery page. Try again.";
  } finally {
    if (current()) {
      photoViewerPaging = false;
      renderPhotoViewer();
      notePhotoViewerActivity();
    }
  }
}

function retryPhotoViewer() {
  if (photoViewerRetryDirection) { void navigatePhotoViewer(photoViewerRetryDirection); return; }
  photoViewerLoadKey = null;
  photoViewerLoadError = null;
  renderPhotoViewer();
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
  syncPhotoViewerPreload();
  if (mode === "hold") {
    updatePhotoViewerPlaybackControl();
    updatePhotoViewerNextIn();
    return;
  }

  if (showLatestCompletedSlideshowGeneration({ force: true })) return;
  updatePhotoViewerPlaybackControl();
  updatePhotoViewerNextIn();
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
  photoViewerNavigationRevision++;
  photoViewerPaging = false;
  state.photoViewerGenerationId = latest.id;
  renderPhotoViewer();
  return true;
}

function schedulePhotoViewerFrame(layout = false) {
  photoViewerLayoutPending ||= layout;
  if (photoViewerFrame !== null) return;
  photoViewerFrame = window.requestAnimationFrame(() => {
    photoViewerFrame = null;
    if (!document.querySelector("#photo-viewer")?.open) return;
    const needsLayout = photoViewerLayoutPending;
    photoViewerLayoutPending = false;
    if (needsLayout) layoutPhotoViewerImage();
    else applyPhotoViewerTransform();
  });
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
  if (renderPending) schedulePhotoViewerFrame();
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
  schedulePhotoViewerFrame(true);
}

function updatePhotoViewerFullscreenControl() {
  const button = document.querySelector("#photo-viewer [data-action=toggle-photo-fullscreen]");
  if (!button) return;
  const active = Boolean(document.fullscreenElement);
  button.textContent = active ? "Exit full screen" : "Full screen";
  button.setAttribute("aria-pressed", String(active));
}

function updatePhotoViewerFavoriteControl() {
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog?.open || !state.photoViewerGenerationId) return;
  const button = dialog.querySelector(".photo-viewer-toolbar [data-action=toggle-favorite]");
  if (!button) return;
  const generation = photoViewerGeneration(photoViewerDisplayed?.id || state.photoViewerGenerationId);
  const active = Boolean(generation?.is_favorite);
  const label = active ? "Remove from Favorites" : "Add to Favorites";
  button.setAttribute("aria-pressed", String(active));
  button.setAttribute("aria-label", label);
  button.setAttribute("title", label);
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

function updatePhotoViewerNextIn(now = Date.now()) {
  refreshPhotoViewerNextIn(root, state.generations,
    state.photoViewerPlaybackMode === "slideshow" && state.autoGenerate === true, now);
}

function handlePhotoViewerResize() {
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog?.open) return;
  state.photoViewerNeedsBaseZoom = true;
  schedulePhotoViewerFrame(true);
}

function notePhotoViewerActivity() {
  const dialog = document.querySelector("#photo-viewer");
  if (!dialog?.open) return;
  dialog.classList.add("controls-visible");
  if (state.photoViewerTimer) window.clearTimeout(state.photoViewerTimer);
  state.photoViewerTimer = window.setTimeout(() => {
    if (checkpointRankChange?.status === "saving" || dialog.querySelector(".photo-viewer-checkpoint-block:focus-within")) {
      notePhotoViewerActivity();
      return;
    }
    dialog.classList.remove("controls-visible");
    state.photoViewerTimer = null;
  }, 2000);
}

function closePhotoViewer() {
  const dialog = document.querySelector("#photo-viewer");
  const shouldExitFullscreen = state.photoViewerFullscreenOwned && Boolean(document.fullscreenElement);
  if (dialog?.open) dialog.close();
  resetPhotoViewerState();
  if (shouldExitFullscreen) document.exitFullscreen().catch(() => {});
}

function resetPhotoViewerState() {
  photoViewerNavigationRevision++;
  photoViewerLoadRevision++;
  photoViewerPreloader.dispose();
  photoViewerImages?.dispose();
  photoViewerImages = null;
  photoViewerImage = null;
  photoViewerDisplayed = null;
  photoViewerLoadKey = null;
  photoViewerLoading = false;
  photoViewerPaging = false;
  photoViewerLoadError = null;
  photoViewerRetryDirection = null;
  photoViewerDirection = "older";
  window.cancelAnimationFrame(photoViewerFrame);
  photoViewerFrame = null;
  photoViewerLayoutPending = false;
  document.querySelector("#photo-viewer .photo-viewer-host")?.replaceChildren();
  state.photoViewerDetachedGeneration = null;
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
      await loadCollections();
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
    await loadCollections();
    toast("Generation deleted.", "success");
  } else {
    await refreshGeneration(id);
    await loadCollections();
    toast("Cancellation and deletion are being reconciled.", "success");
  }
}

function removeGeneration(id) {
  pendingGenerationIds.delete(id);
  scheduleActivityRefresh();
  generationRefreshGate.invalidate(id);
  const closesPhotoViewer = state.photoViewerGenerationId === id || photoViewerDisplayed?.id === id;
  if (closesPhotoViewer) closePhotoViewer();
  state.generations = state.generations.filter((item) => item.id !== id);
  document.querySelector(`[data-generation-id="${CSS.escape(id)}"]`)?.remove();
  galleryGroups?.invalidate();
  renderGallery();
  if (state.photoViewerGenerationId && !closesPhotoViewer) renderPhotoViewer();
  syncServerControls();
}

function removeGalleryGeneration(id) {
  generationRefreshGate.invalidate(id);
  const closesPhotoViewer = state.photoViewerGenerationId === id || photoViewerDisplayed?.id === id;
  if (closesPhotoViewer) closePhotoViewer();
  state.generations = state.generations.filter((item) => item.id !== id);
  document.querySelector(`#gallery [data-generation-id="${CSS.escape(id)}"]`)?.remove();
  galleryGroups?.invalidate();
  renderGallery();
  if (state.photoViewerGenerationId && !closesPhotoViewer) renderPhotoViewer();
  syncServerControls();
}

function beginGenerationActivitySubmission(count) {
  const current = state.generationActivity?.run;
  const active = current?.remaining_count > 0;
  state.generationSubmissionProgress = {
    total_count: (active ? current.total_count : 0) + count,
    resolved_count: active ? current.resolved_count : 0,
    remaining_count: (active ? current.remaining_count : 0) + count,
    succeeded_count: active ? current.succeeded_count : 0,
    failed_count: active ? current.failed_count : 0,
    cancelled_count: active ? current.cancelled_count : 0,
  };
}

function generationActivitySnapshot() {
  return {
    ...state,
    generationActivity: {
      ...state.generationActivity,
      remaining_count: Math.max(state.generationActivity?.remaining_count || 0, pendingGenerationIds.size),
    },
    promptAssistantComposing: promptCompositionRequests > 0 || Boolean(state.autoGenerate && state.automation?.status === "preparing" && state.automation?.snapshot?.assistant),
    autoGeneratePromptReady: state.automation?.prompt_ready === true,
    autoGeneratePinned,
    autoGeneratePinnedCollectionId,
  };
}

function renderWorkerPoolStatus() {
  const host = document.querySelector("#worker-pool-status");
  if (!host) return;
  // Patched in place so hover intent, focus and folder cards are undisturbed.
  const label = imageWorkerPoolLabel(state);
  if (host.textContent !== label) host.textContent = label;
  const tooltip = imageWorkerPoolTooltip(state);
  if (host.title !== tooltip) host.title = tooltip;
}

function renderGenerationActivity() {
  const snapshot = generationActivitySnapshot();
  const now = Date.now();
  const info = generationActivityInfo(snapshot, now);
  for (const host of document.querySelectorAll("#generation-activity-host, #photo-viewer[open] .photo-viewer-activity-host")) {
    if (!info) {
      if (host.childNodes.length) host.replaceChildren();
      continue;
    }
    if (!host.querySelector(".activity-pair")) host.innerHTML = generationActivityMarkup(snapshot, now);
    const badge = host.querySelector(".activity-pair");
    for (const [selector, value] of [["[data-activity-current-label]", info.currentLabel], ["[data-activity-current]", info.currentDisplay], ["[data-activity-all]", info.allDisplay], [".activity-tooltip", info.description]]) {
      const node = badge.querySelector(selector);
      if (node.textContent !== value) node.textContent = value;
    }
    if (badge.title !== info.description) badge.title = info.description;
  }
  renderWorkerPoolStatus();
  document.title = generationActivityTitle(snapshot, now, state.session?.app_title || "ImageGen");
}

function applyCollectionActivity({ counts = true } = {}) {
  const activity = state.generationActivity;
  if (!activity) return;
  for (const collection of state.collections) {
    collection.remaining_count = activity.collection_remaining_counts?.[collection.id] || 0;
    if (counts) collection.generation_count = activity.collection_generation_counts?.[collection.id] || 0;
  }
  const byId = new Map(state.collections.map((collection) => [collection.id, collection]));
  for (const tile of document.querySelectorAll('[data-gallery-card="collection"]')) {
    const collection = byId.get(tile.dataset.collectionId);
    const badge = tile.querySelector(".collection-count");
    if (!collection || !badge) continue;
    const markup = collectionCountMarkup(collection);
    if (badge.outerHTML !== markup) badge.outerHTML = markup;
    tile.querySelector(".collection-tile-open")?.setAttribute("aria-label",
      `Open collection ${collection.name}, ${collection.generation_count} generations${collection.remaining_count ? `, ${collection.remaining_count} remaining including nested folders` : ""}`);
  }
}

function scheduleActivityRefresh() {
  if (activityRefreshTimer !== null || !applicationStartupController) return;
  activityRefreshTimer = window.setTimeout(() => {
    activityRefreshTimer = null;
    void refreshGenerationActivity();
  }, 180);
}

function refreshGenerationActivity() {
  const controller = applicationStartupController;
  if (!controller || controller.signal.aborted) return Promise.resolve();
  if (activityRefreshRequest?.controller === controller) {
    activityRefreshRequest.again = true;
    return activityRefreshRequest.promise;
  }
  const request = { controller, again: false, promise: null };
  activityRefreshRequest = request;
  request.promise = (async () => {
    do {
      request.again = false;
      await fetchGenerationActivity(controller);
    } while (request.again && !controller.signal.aborted);
  })().finally(() => {
    if (activityRefreshRequest === request) activityRefreshRequest = null;
  });
  return request.promise;
}

async function fetchGenerationActivity(controller) {
  const token = ++activityRequestToken;
  try {
    const activity = await api("/api/generation-activity", {
      signal: controller.signal,
      operation: "Generation activity",
      deadlineMs: 6000,
    });
    if (token !== activityRequestToken || controller.signal.aborted) return;
    const previouslyRemaining = state.generationActivity?.remaining_count;
    state.generationActivity = activity;
    state.generationActivityReceivedAt = Date.now();
    state.generationActivityUnavailable = false;
    applyCollectionActivity();
    if (previouslyRemaining !== activity.remaining_count) syncServerControls();
  } catch (error) {
    if (token !== activityRequestToken || requestWasAborted(error, controller.signal)) return;
    state.generationActivityUnavailable = true;
  }
  renderGenerationActivity();
}

function startLiveUpdates({ paused = false } = {}) {
  state.eventSource?.close();
  state.liveUpdatesPaused = paused;
  state.pendingLiveUpdates = [];
  const source = new EventSource(`/api/events?last_event_id=${state.lastEventId}`);
  source.addEventListener("prompt_rerun.updated", () => {
    if (state.eventSource !== source) return;
    void promptRerunProgress?.refresh();
    scheduleActivityRefresh();
  });
  source.addEventListener("expectation_check.updated", () => {
    if (state.eventSource !== source) return;
    void expectationChecks?.refresh();
  });
  for (const type of ["preferences.updated", "auto_generation.updated"]) {
    source.addEventListener(type, () => void refreshUserState());
  }
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
  source.onerror = () => {
    if (state.eventSource !== source) return;
    state.automationUnavailable = true;
    state.generationActivityUnavailable = true;
    renderGenerationActivity();
    syncServerControls();
  };
  source.onopen = () => { scheduleActivityRefresh(); void refreshUserState(); void promptRerunProgress?.refresh(); void expectationChecks?.refresh(); submissionRecovery?.start({ immediate: true }); };
  state.eventSource = source;
  startGenerationEtaTimer();
}

function applyLiveUpdate({ type, payload }) {
  if (["generation.queued", "generation.requeued", "generation.terminal", "generation.deleted"].includes(type)) {
    clearGenerationEtaAnchors(payload.generation_id);
  }
  if ((type !== "generation.progress" && type !== "generation.stage") ||
      Date.now() - (state.generationActivityReceivedAt || 0) >= 1000) scheduleActivityRefresh();
  if (type === "generation.deleted") removeGeneration(payload.generation_id);
  else if (type === "generation.progress") applyGenerationProgress(payload);
  else if (payload.generation_id) liveGenerationRefreshQueue.enqueue(payload.generation_id);
}

function applyGenerationProgress(event) {
  const generationId = event?.generation_id;
  const progress = event?.payload?.progress;
  if (!generationId || !progress || !["node", "indeterminate"].includes(progress.kind)) return;
  const index = state.generations.findIndex((item) => item.id === generationId);
  if (index < 0 || TERMINAL_GENERATION_STATUSES.has(state.generations[index].status)) return;
  const current = state.generations[index].progress;
  if (progressUpdatedAt(progress) < progressUpdatedAt(current)) return;
  state.generations[index] = { ...state.generations[index], progress };
  const card = document.querySelector(`[data-generation-id="${CSS.escape(generationId)}"]`);
  const slot = card?.querySelector("[data-generation-progress-slot]");
  if (slot) slot.innerHTML = generationProgressMarkup(state.generations[index]);
  if (
    state.photoViewerGenerationId === generationId &&
    state.photoViewerPlaybackMode === "hold"
  ) {
    renderPhotoViewer();
  }
  updatePhotoViewerNextIn();
}

function progressUpdatedAt(progress) {
  if (!progress?.updated_at) return 0;
  const timestamp = Date.parse(progress.updated_at);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function startGenerationEtaTimer() {
  stopGenerationEtaTimer();
  refreshGenerationEtaCountdowns();
  state.generationEtaTimer = window.setInterval(refreshGenerationEtaCountdowns, 1_000);
}

function stopGenerationEtaTimer() {
  if (state.generationEtaTimer !== null) window.clearInterval(state.generationEtaTimer);
  state.generationEtaTimer = null;
}

function refreshGenerationEtaCountdowns() {
  const now = Date.now();
  renderGenerationActivity();
  refreshGenerationEtaElements(root, now);
  updatePhotoViewerNextIn(now);
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
      if (visibleGenerations.has(generationId) || pendingGenerationIds.has(generationId)) applyLiveUpdate(update);
      continue;
    }
    const visible = visibleGenerations.get(generationId);
    if (visible) {
      const alreadyTerminal = TERMINAL_GENERATION_STATUSES.has(visible.status);
      if (!alreadyTerminal || update.type !== "generation.terminal") applyLiveUpdate(update);
      continue;
    }
    liveGenerationRefreshQueue.enqueue(generationId, {
      insertIf: boundary
        ? (detail) => generationPrecedesBoundary(detail, boundary.oldest)
        : () => true,
    });
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
      await Promise.allSettled([
        refreshGenerationActivity(),
        refreshServices(controller.signal),
        loadComfyuiInstances({ signal: controller.signal, showLoading: false }),
        // Vision support is discovered by the server's health loop; recheck until it is known.
        ...(state.promptAssistant.available && state.promptAssistant.visionAvailable ? [] : [loadStartupPromptAssistant(controller.signal, { quiet: true })]),
      ]);
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
  topbarResizeObserver?.disconnect();
  topbarResizeObserver = null;
  document.documentElement.style.removeProperty("--topbar-height");
  clearGenerationEtaAnchors();
  activityRequestToken += 1;
  window.clearTimeout(activityRefreshTimer);
  activityRefreshTimer = null;
  stopGenerationEtaTimer();
  discardSpeechSession();
  state.eventSource?.close();
  state.eventSource = null;
  state.liveUpdatesPaused = false;
  state.pendingLiveUpdates = [];
  startupGalleryBoundary = null;
  generationRefreshGate.clear();
  liveGenerationRefreshQueue.clear();
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
      {
        instances: state.comfyuiInstances,
        status: state.comfyuiInstancesStatus,
        message: state.comfyuiInstancesMessage,
        selectedInstanceId: state.defaultComfyuiInstanceId,
      },
    );
  }
}

function updateGalleryLayout(value, persist = false) {
  const layout = value === "classic" ? "classic" : "grouped";
  const changed = layout !== state.galleryLayout;
  const viewport = document.querySelector("#gallery-viewport");
  const top = viewport?.getBoundingClientRect().top || 0;
  const anchor = [...document.querySelectorAll('#gallery [data-gallery-card="generation"]')].find((card) => card.getBoundingClientRect().bottom > top);
  const previousTop = anchor?.getBoundingClientRect().top;
  state.galleryLayout = layout;
  document.querySelectorAll("[data-gallery-layout]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.galleryLayout === layout)));
  if (changed) {
    galleryPageController?.abort();
    state.loadingMore = false;
    if (layout === "classic" && state.gallerySkippedCursor) {
      state.nextCursor = state.gallerySkippedCursor;
      state.gallerySkippedCursor = null;
    }
    renderGallery();
    if (viewport && anchor?.isConnected) viewport.scrollTop += anchor.getBoundingClientRect().top - previousTop;
    setupPaginationObserver();
  }
  if (persist) settingsSync?.schedule();
}

function updateGalleryScale(value, persist) {
  state.galleryScale = Number(value);
  applyGalleryScale();
  if (persist) settingsSync?.schedule();
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
  adminLoraController.mount(dialog.querySelector("#admin-lora-host"), state.sources, state.activeSourceKey);
}

function adminMarkup(users, diagnostics) {
  const ordinary = users.filter((item) => item.role === "user");
  return `<div class="dialog-frame admin-frame">
    <header class="dialog-header"><div><h2>Administration</h2><p>Manage accounts, published sources, and LoRAs.</p></div><button type="button" class="icon-button" data-action="close-admin" aria-label="Close administration">×</button></header>
    <div class="admin-content">
      <section><h3>Users</h3><form id="create-user-form" class="inline-form"><label class="field"><span>Username</span><input name="username" required /></label><label class="field"><span>Temporary password</span><input name="temporary_password" type="password" minlength="8" required /></label><button class="button primary" type="submit">Create user</button></form>
      <div class="table-wrap"><table><thead><tr><th>Username</th><th>State</th><th>Created</th><th>Account actions</th></tr></thead><tbody>${ordinary.map((user) => `<tr><td>${escapeForAdmin(user.username)}</td><td>${user.must_change_password ? "Temporary password" : "Active"}</td><td>${new Date(user.created_at).toLocaleDateString()}</td><td><div class="button-row"><button type="button" class="button low" data-action="reset-user-password" data-user-id="${user.id}">Reset password</button><button type="button" class="button destructive low" data-action="delete-user" data-user-id="${user.id}" data-username="${escapeForAdmin(user.username)}">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="4">No ordinary users.</td></tr>'}</tbody></table></div></section>
      <section id="admin-lora-host" aria-label="LoRA administration"></section>
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
    Object.entries(value || {})
      .map(([key, message]) => [key.replace(/^parameters\./, ""), message])
      .filter(([key]) => key !== "comfyui_instance_id"),
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


function captureSharedSettings() {
  const sources = structuredClone(state.parameterStateBySource);
  if (state.activeSourceKey && sourceInterface(state.activeSource)) sources[state.activeSourceKey] = {
    interface: structuredClone(sourceInterface(state.activeSource)),
    revision: structuredClone(sourceRevision(state.activeSource)),
    values: structuredClone(state.parameters), explicitInputIds: [...state.explicitParameterIds],
    lora_strength_memory: structuredClone(state.loraStrengthMemory),
    selectedPreset: state.selectedPreset,
  };
  const recent = structuredClone(state.recentResolutionsBySource);
  if (state.activeSourceKey) recent[state.activeSourceKey] = structuredClone(state.recentResolutions);
  // Import all legacy resolution lists, including sources not currently open.
  if (state.sharedSettingsStatus === "loading") {
    for (const key of Object.keys(sources)) {
      recent[key] ||= loadRecentResolutions(readStoredItem(recentResolutionKey(sessionStorageUserId(), key)));
    }
  }
  return { gallery_scale: state.galleryScale, checkpoint_tiers: structuredClone(state.checkpointTiers), lora_tiers: structuredClone(state.loraTiers), settings: {
    gallery_layout: state.galleryLayout,
    prompt_generation: structuredClone(state.promptGeneration),
    active_source: state.activeSourceKey,
    sources, model_selections: Object.fromEntries(state.modelSelectionsBySourceRevision),
    quantity: state.generationQuantity, control_sections: structuredClone(state.controlSectionOpen),
    recent_resolutions: recent, creative_direction: state.promptAssistant.creativeDirection,
    assistant_mode: state.promptAssistant.mode, assistant_think: state.promptAssistant.think !== false,
    assistant_instructions: structuredClone(state.promptAssistant.instructionOverrides),
    use_creative_direction: state.autoGenerateCreativeDirection, max_generations: state.maxAutoGenerations,
    creative_direction_expectations: expectationSettingsPayload(state.expectations),
  } };
}

async function applySharedSettings(preferences) {
  const saved = normalizePanelSettings(preferences).settings;
  const oldPromptSource = state.promptGeneration.active_source;
  state.promptGeneration = structuredClone(saved.prompt_generation || { enabled: false, active_source: null, sources: {}, previous_assistant_mode: null });
  const previousSource = state.activeSourceKey;
  const changed = !settingsEqual(captureSharedSettings(), preferences);
  state.galleryScale = preferences.gallery_scale;
  updateGalleryLayout(saved.gallery_layout);
  const ranksChanged = !settingsEqual(state.checkpointTiers, normalizedCheckpointTiers(preferences.checkpoint_tiers));
  state.checkpointTiers = normalizedCheckpointTiers(preferences.checkpoint_tiers);
  if (ranksChanged) {
    checkpointTiersRevision += 1; renderGallery(); renderPhotoViewer();
    if (excludedCheckpointRanks(state).length) void reloadGalleryFilters();
  }
  const loraTiers = normalizeLoraRanks(preferences.lora_tiers);
  if (!settingsEqual(state.loraTiers, loraTiers)) {
    state.loraTiers = loraTiers;
    renderGallery(); renderPhotoViewer(); loraManagerController?.refresh();
    if (excludedLoraRanks(state).length) void reloadGalleryFilters();
  }
  state.parameterStateBySource = normalizeStoredParameterState(JSON.stringify(saved.sources));
  state.activeSourceKey = saved.active_source;
  state.generationQuantity = saved.quantity;
  state.controlSectionOpen = saved.control_sections;
  state.modelSelectionsBySourceRevision = new Map(Object.entries(saved.model_selections));
  state.recentResolutionsBySource = saved.recent_resolutions;
  state.autoGenerateCreativeDirection = saved.use_creative_direction;
  state.maxAutoGenerations = saved.max_generations;
  Object.assign(state.promptAssistant, { creativeDirection: saved.creative_direction,
    mode: saved.assistant_mode, think: saved.assistant_think, instructionOverrides: saved.assistant_instructions });
  state.expectations = normalizeExpectationSettings(saved.creative_direction_expectations);
  const parameters = state.parameterStateBySource[state.activeSourceKey];
  if (parameters) {
    state.parameters = structuredClone(parameters.values);
    state.loraStrengthMemory = structuredClone(parameters.lora_strength_memory || {});
    state.explicitParameterIds = new Set(parameters.explicitInputIds);
    state.selectedPreset = parameters.selectedPreset;
  }
  if (state.promptGeneration.enabled) state.promptAssistant.mode = "refine";
  if (!state.promptGeneration.active_source) state.promptGeneration.active_source = defaultSourceKey(state.promptGeneratorSources);
  if (state.promptGeneratorSources.length && oldPromptSource !== state.promptGeneration.active_source) await selectPromptGenerator(state.promptGeneration.active_source);
  loadRecentResolutionsForActiveSource();
  if (state.sources.length && previousSource !== state.activeSourceKey) {
    // Avoid carrying the previous source's live values into a remote source change.
    state.activeSource = null;
    await selectSource(state.activeSourceKey);
  } else if (changed && !document.activeElement?.matches("input, textarea, select")) renderPanel();
  applyGalleryScale();
  syncServerControls();
}

async function retrySharedSettings() {
  try { await settingsSync?.refresh(); await settingsSync?.save(); }
  catch (error) { state.sharedSettingsStatus = "error"; state.sharedSettingsMessage = error.message; syncServerControls(); }
}

async function refreshUserState() {
  if (!applicationStartupController || applicationStartupController.signal.aborted) return;
  const requests = [refreshAutoGeneration(), refreshPromptJobs()];
  if (!document.activeElement?.matches("input, textarea, select") && !state.sourcePickerDialogOpen &&
      !document.querySelector("#prompt-editor-dialog[open]")) requests.push(settingsSync?.refresh());
  await Promise.allSettled(requests);
}

function automationSnapshot({ enabling = false } = {}) {
  const contract = sourceInterface(state.activeSource);
  if (!contract || !state.activeSourceKey) throw new Error("Choose a workflow first.");
  const prompt = positivePromptInput(contract) || interfaceInputs(contract).find((item) => item.id === "prompt.text");
  const direction = state.promptAssistant.creativeDirection || "";
  const parameters = parametersForRequest(contract, state.parameters);
  if (state.promptGeneration.enabled && prompt) parameters[prompt.id] = "";
  const running = !enabling && state.automation?.enabled ? state.automation.snapshot : null;
  // Server-produced prompts are output, not a user edit to the next batch.
  const receivedPrompt = running && prompt && state.parameters[prompt.id] === state.lastAutoPrompt;
  if (receivedPrompt && !state.promptGeneration.enabled) parameters[prompt.id] = running.generation.parameters[prompt.id];
  return {
    prompt_generation: state.promptGeneration.enabled ? promptGenerationPayload() : null,
    generation: { source_key: state.activeSourceKey, revision: sourceRevision(state.activeSource),
      parameters,
      prompt_assistant: promptAssistantSnapshotPayload(),
      collection_id: running ? running.generation.collection_id : state.currentCollectionId,
    },
    variants: orderedModelParameterVariants(state.activeSource, contract, state.parameters),
    quantity: state.generationQuantity,
    assistant: state.autoGenerateCreativeDirection && direction.trim() ? {
      mode: state.promptAssistant.mode, prompt: receivedPrompt && running.assistant ? running.assistant.prompt : !state.promptGeneration.enabled && prompt ? String(state.parameters[prompt.id] || "") : "",
      creative_direction: direction, think: state.promptAssistant.think !== false,
      instructions: promptInstructionsForMode(state.promptAssistant) || null,
    } : null,
    max_generations: state.maxAutoGenerations,
  };
}

async function refreshAutoGeneration() {
  const controller = applicationStartupController;
  if (!controller || state.automationBusy || state.autoSettingsSaving) return;
  const token = ++automationReadToken;
  try {
    const result = await api("/api/auto-generation", { signal: controller.signal, deadlineMs: 5000 });
    if (controller.signal.aborted || token !== automationReadToken) return;
    applyAutoGenerationState(result);
  } catch (error) {
    if (controller.signal.aborted || token !== automationReadToken) return;
    state.automationUnavailable = true;
    state.autoGenerateStatusMessage = "Auto-generation status unavailable. Reconnecting…";
    syncServerControls();
  }
}

function applyAutoGenerationState(result) {
  if (olderAutomaticProgress(state.automation, result)) return;
  state.automation = result;
  state.automationUnavailable = false;
  autoSettingsSync?.observe(result);
  state.automationLoaded = true;
  const latest = state.latestGeneratedPrompt;
  const discardedPreview = latest?.autoCycleId && (latest.autoRevision !== result.revision || latest.autoCycleId !== result.progress?.cycle_id);
  if (discardedPreview) {
    state.latestGeneratedPrompt = null;
    persistBrowserDraft();
  }
  if (!result.progress && result.latest_prompt && result.latest_prompt !== state.lastAutoPrompt) {
    state.lastAutoPrompt = result.latest_prompt;
    receiveGeneratedPrompt(result.latest_prompt, { source: result.snapshot?.generation.source_key, revision: result.snapshot?.generation.revision, generator: result.snapshot?.prompt_generation?.source_key });
  }
  state.autoGenerate = result.enabled;
  state.autoGenerateStatus = result.status;
  applyAutomaticPromptPreview();
  if (discardedPreview) renderPanel();
  const messages = {
    waiting: "Auto generation is on. It continues with the browser closed.",
    preparing: "Preparing the next automatic prompt.",
    generating: "Auto generation is running on the server.",
  };
  state.autoGenerateStatusMessage = result.message
    ? `${result.message}${result.status === "retrying" ? " Retrying automatically." : ""}`
    : result.enabled ? messages[result.status] || "Auto generation is on." : null;
  autoGeneratePinned = result.enabled;
  autoGeneratePinnedCollectionId = result.snapshot?.generation.collection_id ?? null;
  syncServerControls();
  syncGenerationSubmissionState();
  updatePhotoViewerNextIn();
}

function applyAutomaticPromptPreview({ render = true } = {}) {
  const input = positivePromptInput(sourceInterface(state.activeSource));
  const update = automaticPromptUpdate(state.automation, {
    ready: Boolean(input && !state.sourceDetailLoading && state.sharedSettingsStatus !== "loading"),
    source: state.activeSourceKey, revision: sourceRevision(state.activeSource),
    generator: state.promptGeneration.active_source, dirty: state.promptEditorDirty,
    editorOpen: Boolean(document.querySelector("#prompt-editor-dialog[open]")),
  }, state.autoPromptReceipt);
  if (!["apply", "offer"].includes(update.action)) return;
  state.autoPromptReceipt = update.receipt;
  state.lastAutoPrompt = update.prompt;
  receiveGeneratedPrompt(update.prompt, update, { render });
}

async function autoGenerationCommand(path, payload = {}) {
  if (!state.automationLoaded || state.automationBusy || state.autoSettingsSaving) return;
  if (payload.enabled === false) autoSettingsSync?.clear();
  if (path === "/retry") await autoSettingsSync?.flush();
  const controller = applicationStartupController;
  state.automationBusy = true;
  automationReadToken += 1;
  syncServerControls();
  try {
    const result = await api(`/api/auto-generation${path}`, { method: path ? "POST" : "PUT",
      signal: controller.signal,
      body: JSON.stringify({ ...payload, expected_revision: state.automation.revision }),
    });
    if (controller.signal.aborted) return;
    applyAutoGenerationState(result);
  } catch (error) {
    if (!controller.signal.aborted) toast(error.message, "error");
  } finally {
    state.automationBusy = false;
    if (!controller.signal.aborted) { await refreshAutoGeneration(); syncServerControls(); }
  }
}

async function changeAutoGeneration(enabled) {
  state.pendingAutoEnabled = enabled;
  if (enabled) {
    state.promptEditorDirty = false;
    state.latestGeneratedPrompt = null;
    persistBrowserDraft();
  }
  try {
    syncPromptAssistantDraftFromPanel();
    await autoGenerationCommand("", { enabled, ...(enabled ? { snapshot: automationSnapshot({ enabling: true }) } : {}) });
  } catch (error) { toast(error.message, "error"); }
  finally { state.pendingAutoEnabled = undefined; syncServerControls(); if (enabled) autoSettingsSync?.stage(); }
}

function syncServerControls() {
  const control = document.querySelector("#auto-generate");
  if (control) {
    control.checked = state.pendingAutoEnabled ?? state.autoGenerate;
    control.disabled = !state.automationLoaded || state.automationBusy || state.autoSettingsSaving ||
      (!state.autoGenerate && (state.sharedSettingsStatus === "loading" || !sourceInterface(state.activeSource) || state.sourceDetailLoading));
    control.setAttribute("aria-busy", String(!state.automationLoaded || state.automationBusy));
  }
  const flow = document.querySelector("#prompt-pipeline-flow");
  if (flow) {
    const markup = promptPipelineMarkup(state);
    if (flow.innerHTML !== markup) flow.innerHTML = markup;
  }
  renderWorkerPoolStatus();
  for (const id of ["auto-generate", "auto-generate-creative-direction", "prompt-generation-enabled"]) {
    const toggle = document.getElementById(id);
    const label = toggle?.closest("label")?.querySelector("em");
    if (label) label.textContent = toggle.checked ? "On" : "Off";
  }
  const host = document.querySelector("#server-controls");
  const editingServerControl = host?.contains(document.activeElement) &&
    document.activeElement.matches("input, textarea, select");
  if (host && !editingServerControl) {
    host.innerHTML = serverControlsMarkup(state);
  }
  const statusHost = document.querySelector("#automation-status-host");
  if (statusHost) statusHost.innerHTML = automationStatusMarkup(state);
  const settingsHost = document.querySelector("#shared-settings-status-host");
  if (settingsHost) settingsHost.innerHTML = sharedSettingsStatusMarkup(state);
  syncExpectationControls();
  syncGenerationButtons();
  renderGenerationActivity();
}

async function applyRecoveredSubmission(recovered) {
  state.submissionRecoveryPending = false;
  if (recovered.pending.path === "/api/gallery/prompt-rerun") {
    state.generationSubmissionProgress = null;
    promptRerun?.controller.close();
    await applyPromptRerunResult(recovered.result);
    return;
  }
  if (["/api/prompt-generations", "/api/generation-preparations"].includes(recovered.pending.path)) {
    state.promptGenerationError = null;
    await refreshPromptJobs();
    return;
  }
  if (recovered.pending.path === "/api/prompt-assistant/checks") {
    // A recovered receipt is the check itself, not a generation.
    const check = recovered.result;
    if (!expectationStarts.has(check.id)) {
      let prompt = check.starting_prompt;
      try { prompt = JSON.parse(recovered.pending.body).assistant?.prompt ?? prompt; } catch { /* Use the server's starting prompt. */ }
      expectationStarts.set(check.id, { prompt, sourceName: null });
    }
    setPromptAssistantError(null);
    expectationChecks?.track(check, { open: true });
    return;
  }
  const items = recovered.pending.path.endsWith("/batch")
    ? recovered.result.items : [{ generation: recovered.result }];
  for (const { generation } of items) {
    if (!generation) continue;
    if (!TERMINAL_GENERATION_STATUSES.has(generation.status)) pendingGenerationIds.add(generation.id);
    if (generationBelongsToView(generation)) {
      state.generations = sortGenerationsNewestFirst([
        generation, ...state.generations.filter((item) => item.id !== generation.id),
      ]);
    }
  }
  const payload = JSON.parse(recovered.pending.body);
  const inputs = payload.items || [payload];
  if (items.some((item, index) => item.generation && inputs[index]?.prompt_assistant_run_id === state.compositionId)) {
    state.compositionId = null;
  }
  const failures = items.filter((item) => item.error);
  state.formError = failures.length
    ? `${failures.length} submission item(s) failed. ${failures.map((item) => item.error.message).slice(0, 3).join(" ")}` : null;
  renderGallery();
  await refreshGenerationActivity();
}

function validateImageParameters(contract, parameters) {
  if (!state.promptGeneration.enabled && !(state.autoGenerateCreativeDirection && state.promptAssistant.mode === "create")) return clientValidate(contract, parameters);
  const prompt = positivePromptInput(contract);
  return clientValidate(contract, prompt ? { ...parameters, [prompt.id]: "Prompt preparation pending." } : parameters);
}

function normalizePanelSettings(value) {
  if (!value?.settings) return value;
  const normalized = structuredClone(value);
  normalized.checkpoint_tiers = normalizeCheckpointRanks(normalized.checkpoint_tiers);
  normalized.lora_tiers = normalizeLoraRanks(normalized.lora_tiers);
  delete normalized.settings.runtime_id;
  if (normalized.settings.prompt_generation) delete normalized.settings.prompt_generation.runtime_id;
  normalized.settings.gallery_layout = normalized.settings.gallery_layout === "classic" ? "classic" : "grouped";
  const reconcile = (entries, source) => {
    const saved = entries?.[source?.source_key];
    if (!saved || !source?.interface) return;
    saved.values = reconcileInterfaceValues(source.interface, saved.values, saved.interface, saved.explicitInputIds || []);
    const allowed = new Map(interfaceInputs(source.interface).filter((input) => input.type === "lora_stack").map((input) => [input.id, input]));
    saved.lora_strength_memory = Object.fromEntries(Object.entries(saved.lora_strength_memory || {}).filter(([id]) => allowed.has(id)).map(([id, strengths]) => [id, Object.fromEntries(Object.entries(strengths || {}).filter(([itemId, value]) => allowed.get(id).items.some((item) => item.id === itemId) && typeof value === "number" && Number.isFinite(value) && value > 0))]));
    saved.interface = structuredClone(source.interface);
    saved.revision = structuredClone(sourceRevision(source));
  };
  const interfaces = new Map(settingsInterfaces);
  if (state.activeSource?.interface && !interfaces.has(state.activeSourceKey)) interfaces.set(state.activeSourceKey, state.activeSource);
  if (state.promptGeneratorSource?.interface && !interfaces.has(state.promptGeneration.active_source)) interfaces.set(state.promptGeneration.active_source, state.promptGeneratorSource);
  for (const source of interfaces.values()) {
    reconcile(normalized.settings.sources, source);
    reconcile(normalized.settings.prompt_generation?.sources, source);
  }
  return normalized;
}

async function prepareSettingsInterfaces(...values) {
  const signal = applicationStartupController?.signal;
  const keys = new Set(values.flatMap((value) => [
    ...Object.keys(value?.settings?.sources || {}),
    ...Object.keys(value?.settings?.prompt_generation?.sources || {}),
  ]));
  for (const key of keys) {
    try {
      const source = await api(`/api/workflows/${encodeURIComponent(key)}`, { signal, deadlineMs: 5000 });
      if (signal?.aborted) return;
      settingsInterfaces.set(key, source);
      if (state.activeSourceKey === key && state.activeSource) state.activeSource = source;
      if (state.promptGeneration.active_source === key && state.promptGeneratorSource) state.promptGeneratorSource = source;
    } catch {
      if (signal?.aborted) return;
      // Retain unavailable sources for the existing source-error UI and explicit retry.
    }
  }
}

function persistBrowserDraft() {
  if (!state.session?.user?.id) return;
  writeStoredItem(`cif.panel-draft.${sessionStorageUserId()}`, JSON.stringify({
    promptEditorDirty: state.promptEditorDirty,
    latestGeneratedPrompt: state.latestGeneratedPrompt,
    lastAutoPrompt: state.lastAutoPrompt,
  }));
}

function restoreBrowserDraft() {
  state.promptGeneration = { enabled: false, active_source: null, sources: {}, previous_assistant_mode: null };
  state.promptGeneratorSources = [];
  state.promptGeneratorsLoaded = false;
  state.promptGeneratorSource = null;
  state.promptGenerationBusy = false;
  state.promptGenerationRequest = null;
  state.promptPreparationBusy = false;
  state.promptJobsUnavailable = false;
  state.promptGenerationError = null;
  state.promptGenerationReadError = null;
  state.latestGeneratedPrompt = null;
  state.promptEditorDirty = false;
  state.lastAutoPrompt = null;
  state.autoPromptReceipt = null;
  state.automationUnavailable = false;
  try {
    const saved = JSON.parse(readStoredItem(`cif.panel-draft.${sessionStorageUserId()}`) || "null");
    if (saved && typeof saved === "object") {
      state.promptEditorDirty = saved.promptEditorDirty === true;
      if (typeof saved.latestGeneratedPrompt?.prompt === "string") state.latestGeneratedPrompt = saved.latestGeneratedPrompt;
      if (typeof saved.lastAutoPrompt === "string") state.lastAutoPrompt = saved.lastAutoPrompt;
    }
  } catch { state.promptGenerationError = "Saved prompt draft could not be restored."; }
}

async function loadPromptGenerators(signal = applicationStartupController?.signal) {
  try {
    const sources = await api("/api/workflows?output_kind=text", { signal });
    if (signal?.aborted) return;
    state.promptGeneratorSources = sources;
    state.promptGeneratorsLoaded = true;
    state.promptGeneratorLoadError = false;
    if (!state.promptGeneration.active_source) state.promptGeneration.active_source = defaultSourceKey(sources);
    await selectPromptGenerator(state.promptGeneration.active_source, signal);
  } catch (error) {
    if (signal?.aborted) return;
    state.promptGeneratorLoadError = true;
    state.promptGenerationError = error.message;
    renderPanel();
  }
}

async function selectPromptGenerator(key, signal = applicationStartupController?.signal) {
  key = reconcileSourceKey(state.promptGeneratorSources, key, state.promptGeneration.sources);
  state.promptGeneration.active_source = key;
  state.promptGeneratorSource = null;
  state.promptGenerationError = null;
  settingsSync?.schedule();
  renderPanel();
  if (!key) return;
  const token = ++promptGeneratorLoadToken;
  try {
    const source = await api(`/api/workflows/${encodeURIComponent(key)}`, { signal });
    if (signal?.aborted || token !== promptGeneratorLoadToken || key !== state.promptGeneration.active_source) return;
    if (source.output_kind !== "text") throw new Error("The saved prompt source is not a text generator.");
    const saved = state.promptGeneration.sources[key];
    state.promptGeneratorSource = source;
    settingsInterfaces.set(key, source);
    state.promptGeneration.sources[key] = {
      interface: source.interface, revision: sourceRevision(source),
      values: reconcileInterfaceValues(source.interface, saved?.values || {}, saved?.interface, saved?.explicitInputIds || []),
      explicitInputIds: saved?.explicitInputIds || [], selectedPreset: null,
    };
    settingsSync?.schedule();
  } catch (error) {
    if (signal?.aborted || token !== promptGeneratorLoadToken) return;
    state.promptGenerationError = error.message;
    state.promptGeneratorLoadError = true;
  }
  renderPanel();
}

function updatePromptGeneratorControl(element) {
  const source = state.promptGeneratorSource;
  const id = element.dataset.promptGeneratorId || element.dataset.promptGeneratorSeedMode;
  const input = interfaceInputs(source?.interface).find((item) => item.id === id);
  const saved = state.promptGeneration.sources[source?.source_key];
  if (!input || !saved) return;
  if (element.dataset.promptGeneratorSeedMode) {
    const current = seedFormValue(input, saved.values[id]);
    saved.values[id] = { ...current, mode: element.checked ? "random" : "fixed" };
  } else if (input.type === "boolean") saved.values[id] = element.checked;
  else if (input.type === "seed") saved.values[id] = { mode: "fixed", value: element.value };
  else saved.values[id] = normalizeInputValue(input, element.value);
  if (!saved.explicitInputIds.includes(id)) saved.explicitInputIds.push(id);
  state.promptGenerationError = null;
  settingsSync?.schedule();
  syncNumberControlPair(element);
  if (element.dataset.promptGeneratorSeedMode) renderPanel();
}

function promptGenerationPayload() {
  const source = state.promptGeneratorSource;
  if (!source || source.available === false) throw new Error("Choose an available prompt source.");
  const saved = state.promptGeneration.sources[source.source_key];
  const runtimeError = promptRuntimeError(state);
  if (runtimeError) throw new Error(runtimeError);
  return { source_key: source.source_key, revision: sourceRevision(source), parameters: parametersForRequest(source.interface, saved.values) };
}

async function runPromptGeneration(withImages) {
  if (withImages && (state.autoGenerate || state.pendingAutoEnabled !== undefined || !state.automationLoaded || state.automationBusy || state.autoSettingsSaving)) return false;
  if (state.promptGenerationBusy || state.submitting || pendingSubmission()) return false;
  const account = state.session.user.id;
  const signal = applicationStartupController.signal;
  try {
    syncPromptAssistantDraftFromPanel();
    const generator = promptGenerationPayload();
    const contract = sourceInterface(state.activeSource);
    const prompt = positivePromptInput(contract);
    const context = { source: state.activeSourceKey, revision: sourceRevision(state.activeSource), generator: generator.source_key, original: state.parameters[prompt?.id] || "" };
    state.promptGenerationRequest = withImages ? "images" : "prompt";
    state.promptGenerationError = null;
    state.promptEditorDirty = false;
    state.latestGeneratedPrompt = null;
    persistBrowserDraft();
    let payload = generator;
    let path = "/api/prompt-generations";
    if (withImages) {
      const errors = validateImageParameters(contract, state.parameters);
      if (Object.keys(errors).length) throw new Error("Review the highlighted image controls.");
      const assistant = state.autoGenerateCreativeDirection ? {
        mode: "refine", prompt: "", creative_direction: state.promptAssistant.creativeDirection || "",
        think: state.promptAssistant.think !== false, instructions: promptInstructionsForMode(state.promptAssistant, "refine") || null,
      } : null;
      const parameters = parametersForRequest(contract, state.parameters);
      const variants = orderedModelParameterVariants(state.activeSource, contract, state.parameters);
      payload = { items: variants.flatMap((variant) => Array.from({ length: state.generationQuantity }, () => ({
        generation: { source_key: state.activeSourceKey, revision: sourceRevision(state.activeSource),
          parameters: { ...parameters, ...variant }, collection_id: state.currentCollectionId,
          prompt_assistant: promptAssistantSnapshotPayload() },
        prompt_generation: generator, assistant,
      }))) };
      path = "/api/generation-preparations";
    }
    state.submitting = true;
    renderPanel();
    await submitGeneration(path, payload, context, { signal });
    if (state.session?.user?.id !== account) return false;
    state.promptGenerationRequest = null;
    state.submitting = false;
    syncGenerationButtons();
    await refreshPromptJobs();
    return true;
  } catch (error) {
    if (!signal.aborted && state.session?.user?.id === account) {
      if (error.code === "submission_status_unknown") submissionRecovery?.start();
      else state.promptGenerationError = error.message;
    }
    return false;
  } finally {
    if (!signal.aborted && state.session?.user?.id === account) {
      state.promptGenerationRequest = null;
      state.submitting = false;
      renderPanel();
    }
  }
}

function receiveGeneratedPrompt(prompt, context, { render = true } = {}) {
  if (!prompt || context.source !== state.activeSourceKey) return;
  if (context.generator && context.generator !== state.promptGeneration.active_source) return;
  if (context.revision && !revisionsMatch({ revision: context.revision }, state.activeSource)) return;
  const input = positivePromptInput(sourceInterface(state.activeSource));
  if (!input) return;
  if (state.parameters[input.id] === prompt) {
    if (context.autoCycleId && state.latestGeneratedPrompt?.autoCycleId === context.autoCycleId) {
      state.latestGeneratedPrompt = null;
      persistBrowserDraft();
      if (render) renderPanel();
    }
    return;
  }
  if (state.promptEditorDirty || document.querySelector("#prompt-editor-dialog[open]")) {
    state.latestGeneratedPrompt = { prompt, source: context.source, revision: context.revision, generator: context.generator,
      autoRevision: context.autoRevision, autoCycleId: context.autoCycleId };
  } else {
    state.latestGeneratedPrompt = null;
    state.parameters[input.id] = prompt;
    state.explicitParameterIds.add(input.id);
    state.compositionId = null;
    persistActiveParameterState();
  }
  persistBrowserDraft();
  if (render) renderPanel();
}

function applyLatestGeneratedPrompt() {
  const latest = state.latestGeneratedPrompt;
  if (!latest || latest.source !== state.activeSourceKey) return;
  if (latest.autoCycleId && (latest.autoRevision !== state.automation?.revision || latest.autoCycleId !== state.automation?.progress?.cycle_id)) return;
  if (latest.revision && !revisionsMatch({ revision: latest.revision }, state.activeSource)) return;
  if (latest.generator && latest.generator !== state.promptGeneration.active_source) return;
  state.latestGeneratedPrompt = null;
  state.promptEditorDirty = false;
  receiveGeneratedPrompt(latest.prompt, { source: latest.source });
  persistBrowserDraft();
  renderPanel();
}

async function refreshPromptJobs() {
  if (promptJobsRefreshing || !promptJobsReady || !applicationStartupController || !state.session?.user?.id) return;
  const account = state.session.user.id;
  const signal = applicationStartupController.signal;
  const previousError = state.promptGenerationError || state.promptGenerationReadError;
  promptJobsRefreshing = true;
  try {
    const jobs = pendingPromptJobs();
    let unavailable = false;
    let readError = null;
    for (const job of jobs) {
      let result;
      try {
        result = await api(`${job.path}/${encodeURIComponent(job.id)}`, { signal, deadlineMs: 10_000, operation: "Prompt progress" });
      } catch (error) {
        if (signal.aborted || state.session?.user?.id !== account) return;
        if ([404, 410].includes(error.status)) {
          finishPromptJob(job.id);
          promptJobPhases.delete(job.id);
          state.promptGenerationError = "This prompt request is no longer available. Generate a new prompt to try again.";
        } else {
          unavailable = true;
          if (!isTransientError(error) && error.code !== "request_timeout") readError = error.message;
        }
        continue;
      }
      if (signal.aborted || state.session?.user?.id !== account) return;
      const items = job.path === "/api/prompt-generations" ? [result] : result.items;
      for (const item of items) {
        const text = item.prompt || item.raw_prompt;
        const mark = `${item.id}:${item.status}:${text || ""}`;
        if (promptJobSeen.get(item.id) !== mark) {
          promptJobSeen.set(item.id, mark);
          if (text) receiveGeneratedPrompt(text, job);
          if (item.error) state.promptGenerationError = item.error.message;
          if (item.generation && generationBelongsToView(item.generation)) {
            if (!state.generations.some((existing) => existing.id === item.generation.id)) {
              state.generations = sortGenerationsNewestFirst([item.generation, ...state.generations]);
              renderGallery();
            }
            liveGenerationRefreshQueue.enqueue(item.generation.id);
          }
        }
      }
      const complete = items.every((item) => ["succeeded", "accepted", "failed", "discarded"].includes(item.status));
      if (complete) {
        finishPromptJob(job.id);
        promptJobPhases.delete(job.id);
        await refreshGenerationActivity();
      } else {
        promptJobPhases.set(job.id, items.some((item) => item.status === "refining") ? "refining"
          : items.every((item) => item.status === "queued") ? "queued" : "generating");
      }
    }
    if (signal.aborted || state.session?.user?.id !== account) return;
    state.promptJobsUnavailable = unavailable;
    state.promptGenerationReadError = readError;
  } catch (error) {
    if (!signal.aborted) state.promptGenerationReadError = error.message;
  } finally {
    promptJobsRefreshing = false;
    if (!signal.aborted && state.session?.user?.id === account) {
      if (previousError !== (state.promptGenerationError || state.promptGenerationReadError)) renderPanel();
      else syncGenerationButtons();
    }
  }
}
