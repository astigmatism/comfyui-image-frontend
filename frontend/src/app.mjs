import { api, setCsrfToken, upload } from "./api.mjs";
import {
  clientValidate,
  defaultsForContract,
  normalizeInputValue,
  overwriteWithRecall,
  resolutionSummary,
  scaleToLayout,
  snapResolutionValue,
} from "./lib.mjs";
import {
  detailMarkup,
  galleryCardMarkup,
  galleryMarkup,
  generationPanelMarkup,
  loginMarkup,
  passwordChangeMarkup,
  serviceBannerMarkup,
  shellMarkup,
} from "./render.mjs";

const root = document.querySelector("#app");

const state = {
  session: null,
  workflows: [],
  activeProfileId: null,
  activeProfile: null,
  controls: {},
  selectedPreset: null,
  recallIdentity: null,
  compositionId: null,
  promptAssistant: { mode: "refine", creativeDirection: "", available: false, message: null },
  generations: [],
  nextCursor: null,
  loadingMore: false,
  galleryScale: 45,
  services: [],
  submitting: false,
  serverFieldErrors: {},
  formError: null,
  panelOpen: false,
  assistantOpen: false,
  eventSource: null,
  lastEventId: 0,
  serviceTimer: null,
  scaleTimer: null,
  observer: null,
  changingPasswordFromApp: false,
};

let activeResolutionDrag = null;

async function initialize() {
  bindDelegatedEvents();
  try {
    const session = await api("/api/auth/session");
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

function bindDelegatedEvents() {
  root.addEventListener("submit", handleSubmit);
  root.addEventListener("click", handleClick);
  root.addEventListener("change", handleChange);
  root.addEventListener("input", handleInput);
  root.addEventListener("keydown", handleKeyDown);
  root.addEventListener("keyup", handleKeyUp);
  root.addEventListener("pointerdown", handlePointerDown);
  root.addEventListener("pointermove", handlePointerMove);
  root.addEventListener("pointerup", handlePointerEnd);
  root.addEventListener("pointercancel", handlePointerEnd);
  root.addEventListener(
    "toggle",
    (event) => {
      if (event.target.id === "prompt-assistant") state.assistantOpen = event.target.open;
    },
    true,
  );
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
    state.controls[clearUpload.dataset.clearUpload] = null;
    renderPanel();
    return;
  }
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  try {
    if (action === "generate") await generate();
    else if (action === "logout") await logout();
    else if (action === "change-password") {
      state.changingPasswordFromApp = true;
      renderPasswordChange(false);
    } else if (action === "cancel-password") await enterApplication();
    else if (action === "toggle-panel") {
      state.panelOpen = !state.panelOpen;
      document.querySelector(".app-shell")?.classList.toggle("panel-open", state.panelOpen);
      target.setAttribute("aria-expanded", String(state.panelOpen));
    } else if (action === "close-panel") closePanel();
    else if (action === "compose-prompt") await composePrompt(target);
    else if (action === "recall") await recall(target.dataset.generationId);
    else if (action === "open-detail") await openDetail(target.dataset.generationId);
    else if (action === "cancel-generation") await cancelGeneration(target.dataset.generationId, target);
    else if (action === "delete-generation") await deleteGeneration(target.dataset.generationId);
    else if (action === "load-more") await loadMore();
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
  if (element.id === "workflow-source") {
    await selectWorkflow(element.value);
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
    state.controls[id] = element.value === "random" ? "random" : 0;
    state.serverFieldErrors[id] = null;
    renderPanel();
    return;
  }
  if (element.matches("input[type=file][data-control-id]")) {
    await handleUpload(element);
    return;
  }
  if (element.matches("[data-control-id]")) {
    updateControlFromElement(element);
    renderPanel();
  }
}

function handleInput(event) {
  const element = event.target;
  if (element.id === "gallery-scale") {
    updateGalleryScale(element.value, false);
    return;
  }
  if (element.id === "creative-direction") {
    state.promptAssistant.creativeDirection = element.value;
    return;
  }
  if (element.name === "assistant-mode") {
    state.promptAssistant.mode = element.value;
    return;
  }
  if (element.matches("[data-control-id]") && !element.matches("input[type=file]")) {
    updateControlFromElement(element);
    if (element.dataset.resolutionPart) {
      const grid = element.closest("[data-control-block]")?.querySelector("[data-resolution-grid]");
      updateResolutionUi(grid, state.controls[element.dataset.controlId]);
    }
  }
}

function handlePointerDown(event) {
  if (event.button !== 0) return;
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
  if (!activeResolutionDrag || event.pointerId !== activeResolutionDrag.pointerId) return;
  event.preventDefault();
  updateResolutionFromPointer(event, activeResolutionDrag);
}

function handlePointerEnd(event) {
  if (!activeResolutionDrag || event.pointerId !== activeResolutionDrag.pointerId) return;
  const { captureTarget, grid, mode, pointerId } = activeResolutionDrag;
  activeResolutionDrag = null;
  try {
    if (captureTarget.hasPointerCapture(pointerId)) captureTarget.releasePointerCapture(pointerId);
  } catch {
    // The browser may release capture before pointercancel reaches the delegated handler.
  }
  renderPanelWithResolutionFocus(grid.dataset.controlId, mode);
}

function handleKeyDown(event) {
  const handle = event.target.closest("[data-resolution-handle]");
  if (!handle || handle.disabled) return;
  const grid = handle.closest("[data-resolution-grid]");
  if (!grid) return;
  const mode = handle.dataset.resolutionHandle;
  const current = state.controls[grid.dataset.controlId] || {};
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

function handleKeyUp(event) {
  const handle = event.target.closest("[data-resolution-handle]");
  if (!handle || !["ArrowLeft", "ArrowRight", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const grid = handle.closest("[data-resolution-grid]");
  if (grid) renderPanelWithResolutionFocus(grid.dataset.controlId, handle.dataset.resolutionHandle);
}

function updateResolutionFromPointer(event, drag) {
  const rect = drag.grid.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const limits = resolutionGridLimits(drag.grid);
  const current = state.controls[drag.grid.dataset.controlId] || {};
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
  const id = grid.dataset.controlId;
  state.controls[id] = { width, height };
  delete state.serverFieldErrors[id];
  state.formError = null;
  updateResolutionUi(grid, state.controls[id]);
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
  const block = grid.closest("[data-control-block]");
  const widthInput = block?.querySelector('[data-resolution-part="width"]');
  const heightInput = block?.querySelector('[data-resolution-part="height"]');
  const caption = block?.querySelector("[data-resolution-summary]");
  if (widthInput) widthInput.value = value?.width ?? "";
  if (heightInput) heightInput.value = value?.height ?? "";
  if (caption) caption.textContent = summary.text;
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

function renderPanelWithResolutionFocus(controlId, handle) {
  renderPanel();
  queueMicrotask(() => {
    document
      .querySelector(`[data-resolution-grid][data-control-id="${CSS.escape(controlId)}"] [data-resolution-handle="${handle}"]`)
      ?.focus();
  });
}

function updateControlFromElement(element) {
  const id = element.dataset.controlId;
  const control = state.activeProfile?.contract?.controls?.find((item) => item.id === id);
  if (!id || !control) return;
  if (element.dataset.resolutionPart) {
    const current = state.controls[id] || {};
    state.controls[id] = {
      ...current,
      [element.dataset.resolutionPart]: element.value === "" ? null : Number.parseInt(element.value, 10),
    };
  } else if (element.dataset.jsonControl) {
    try {
      state.controls[id] = JSON.parse(element.value);
    } catch {
      state.serverFieldErrors[id] = "Enter valid JSON.";
      return;
    }
  } else if (control.type === "boolean") {
    state.controls[id] = element.checked;
  } else if (control.type === "seed") {
    state.controls[id] = element.value === "" ? "random" : Number.parseInt(element.value, 10);
  } else {
    state.controls[id] = normalizeInputValue(control, element.value);
  }
  delete state.serverFieldErrors[id];
  state.formError = null;
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
    const session = await api("/api/auth/session");
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
  const session = await api("/api/auth/session");
  state.session = session;
  setCsrfToken(session.csrf_token);
  renderLogin();
}

function renderLogin() {
  stopLiveUpdates();
  root.innerHTML = loginMarkup(state.session?.app_title || "ComfyUI Gallery");
  queueMicrotask(() => root.querySelector("input")?.focus());
}

function renderPasswordChange(forced) {
  stopLiveUpdates();
  root.innerHTML = passwordChangeMarkup(state.session?.app_title || "ComfyUI Gallery", forced);
  queueMicrotask(() => root.querySelector("input")?.focus());
}

async function enterApplication() {
  stopLiveUpdates();
  const [workflows, preferences, services, page, assistant] = await Promise.all([
    api("/api/workflows"),
    api("/api/preferences"),
    api("/api/services"),
    api("/api/generations?limit=24"),
    api("/api/prompt-assistant/status"),
  ]);
  state.workflows = workflows;
  state.galleryScale = preferences.gallery_scale;
  state.services = services;
  state.generations = page.items;
  state.nextCursor = page.next_cursor;
  state.promptAssistant = {
    ...state.promptAssistant,
    available: assistant.available,
    message: assistant.message,
  };
  if (!state.activeProfileId || !workflows.some((item) => item.profile_id === state.activeProfileId)) {
    state.activeProfileId = workflows[0]?.profile_id || null;
    state.activeProfile = null;
    state.controls = {};
    state.recallIdentity = null;
    state.selectedPreset = null;
  }
  if (state.activeProfileId) {
    state.activeProfile = await api(`/api/workflows/${state.activeProfileId}`);
    if (!Object.keys(state.controls).length) {
      state.controls = defaultsForContract(state.activeProfile.contract);
    }
  }
  root.innerHTML = shellMarkup(state);
  renderPanel();
  renderGallery();
  renderServiceBanner();
  applyGalleryScale();
  setupPaginationObserver();
  startLiveUpdates();
}

async function selectWorkflow(profileId) {
  state.activeProfileId = profileId || null;
  state.activeProfile = profileId ? await api(`/api/workflows/${profileId}`) : null;
  state.controls = defaultsForContract(state.activeProfile?.contract);
  state.selectedPreset = null;
  state.recallIdentity = null;
  state.compositionId = null;
  state.serverFieldErrors = {};
  state.formError = null;
  state.assistantOpen = false;
  renderPanel();
}

function applyPreset(presetId) {
  state.selectedPreset = presetId;
  const contract = state.activeProfile?.contract;
  state.controls = defaultsForContract(contract);
  const preset = contract?.presets?.find((item) => item.id === presetId);
  if (preset) Object.assign(state.controls, structuredClone(preset.values || {}));
  state.serverFieldErrors = {};
  state.formError = null;
  renderPanel();
}

function renderPanel() {
  const panel = document.querySelector("#generation-panel");
  if (!panel) return;
  const clientErrors = clientValidate(state.activeProfile?.contract, state.controls);
  state.fieldErrors = { ...clientErrors, ...withoutNulls(state.serverFieldErrors) };
  panel.innerHTML = generationPanelMarkup(
    state,
    state.workflows.find((item) => item.profile_id === state.activeProfileId),
    state.activeProfile?.contract,
  );
  const assistant = panel.querySelector("#prompt-assistant");
  if (assistant) {
    assistant.open = state.assistantOpen;
    const direction = assistant.querySelector("#creative-direction");
    direction.value = state.promptAssistant.creativeDirection || "";
    const mode = assistant.querySelector(`[name=assistant-mode][value=${state.promptAssistant.mode}]`);
    if (mode) mode.checked = true;
    const message = assistant.querySelector("#assistant-message");
    const button = assistant.querySelector("[data-action=compose-prompt]");
    if (!state.promptAssistant.available) {
      message.textContent = state.promptAssistant.message || "Prompt Assistant is unavailable.";
      button.disabled = true;
    } else if (state.promptAssistant.historicalModel) {
      message.textContent = `Historical composition used ${state.promptAssistant.historicalModel}; recall will not invoke it again.`;
    }
  }
}

async function generate() {
  if (!state.activeProfile) return;
  const errors = clientValidate(state.activeProfile.contract, state.controls);
  if (Object.keys(errors).length) {
    state.serverFieldErrors = errors;
    state.formError = "Review the highlighted controls.";
    renderPanel();
    focusFirstInvalid();
    return;
  }
  state.submitting = true;
  state.formError = null;
  state.serverFieldErrors = {};
  renderPanel();
  try {
    const generation = await api("/api/generations", {
      method: "POST",
      body: JSON.stringify({
        profile_id: state.activeProfileId,
        controls: state.controls,
        preset_id: state.selectedPreset,
        requested_outputs: [],
        prompt_assistant_run_id: state.compositionId,
        expected_identity: state.recallIdentity,
      }),
    });
    state.generations = [generation, ...state.generations.filter((item) => item.id !== generation.id)];
    state.compositionId = null;
    upsertGalleryCard(generation, true);
    toast("Generation queued.", "success");
  } catch (error) {
    state.formError = error.message;
    state.serverFieldErrors = error.fields || {};
    if (error.code === "workflow_unavailable") state.recallIdentity = null;
  } finally {
    state.submitting = false;
    renderPanel();
  }
}

async function composePrompt(button) {
  if (!state.promptAssistant.available) return;
  button.disabled = true;
  button.textContent = "Composing…";
  try {
    const result = await api("/api/prompt-assistant/compose", {
      method: "POST",
      body: JSON.stringify({
        mode: state.promptAssistant.mode,
        prompt: state.controls["prompt.text"] || "",
        creative_direction: state.promptAssistant.creativeDirection || "",
      }),
    });
    state.controls["prompt.text"] = result.prompt;
    state.compositionId = result.composition_id;
    state.promptAssistant.historicalModel = result.model;
    state.assistantOpen = true;
    renderPanel();
    const prompt = document.querySelector('[data-control-id="prompt.text"]');
    prompt?.focus();
    toast("Prompt composed and placed in the editable Prompt field.", "success");
  } catch (error) {
    const message = document.querySelector("#assistant-message");
    if (message) message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Compose Prompt";
  }
}

async function handleUpload(input) {
  const file = input.files?.[0];
  if (!file) return;
  const id = input.dataset.controlId;
  input.disabled = true;
  try {
    const result = await upload(`/api/uploads/${input.dataset.uploadKind}`, file);
    state.controls[id] = result.id;
    delete state.serverFieldErrors[id];
    renderPanel();
  } catch (error) {
    state.serverFieldErrors[id] = error.message;
    renderPanel();
  }
}

function renderGallery() {
  const gallery = document.querySelector("#gallery");
  if (!gallery) return;
  gallery.innerHTML = galleryMarkup(state.generations);
  const sentinel = document.querySelector("#gallery-sentinel");
  if (sentinel) sentinel.hidden = !state.nextCursor;
}

function upsertGalleryCard(generation, prepend = false) {
  const gallery = document.querySelector("#gallery");
  if (!gallery) return;
  const empty = gallery.querySelector(".empty-gallery");
  empty?.remove();
  const existing = gallery.querySelector(`[data-generation-id="${CSS.escape(generation.id)}"]`);
  if (existing) {
    existing.outerHTML = galleryCardMarkup(generation);
  } else if (prepend) {
    gallery.insertAdjacentHTML("afterbegin", galleryCardMarkup(generation));
  } else {
    gallery.insertAdjacentHTML("beforeend", galleryCardMarkup(generation));
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

async function refreshGeneration(id) {
  try {
    const detail = await api(`/api/generations/${id}`);
    const index = state.generations.findIndex((item) => item.id === id);
    if (index >= 0) state.generations[index] = detail;
    else state.generations.unshift(detail);
    upsertGalleryCard(detail, index < 0);
    const dialog = document.querySelector("#detail-dialog");
    if (dialog?.open && dialog.dataset.generationId === id) dialog.innerHTML = detailMarkup(detail);
  } catch (error) {
    if (error.status === 404) removeGeneration(id);
  }
}

async function recall(id) {
  const recalled = await api(`/api/generations/${id}/recall`);
  if (!recalled.available) {
    toast(recalled.reason || "Exact recall is unavailable.", "error");
    return;
  }
  const profile = await api(`/api/workflows/${recalled.profile_id}`);
  Object.assign(state, overwriteWithRecall(state, recalled));
  state.activeProfile = profile;
  state.selectedPreset = null;
  state.assistantOpen = Boolean(recalled.prompt_assistant);
  renderPanel();
  closePanel(false);
  document.querySelector("#generation-panel")?.scrollIntoView({ block: "start" });
  toast("Exact historical settings loaded. Press Generate when ready.", "success");
}

async function openDetail(id) {
  const detail = await api(`/api/generations/${id}`);
  const dialog = document.querySelector("#detail-dialog");
  dialog.dataset.generationId = id;
  dialog.innerHTML = detailMarkup(detail);
  dialog.showModal();
}

async function cancelGeneration(id, button) {
  const buttonLabel = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "Stopping…";
  }
  try {
    const result = await api(`/api/generations/${id}/cancel`, { method: "POST" });
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
  if (!window.confirm("Permanently delete this generation and all application-owned files?")) return;
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
  state.generations = state.generations.filter((item) => item.id !== id);
  document.querySelector(`[data-generation-id="${CSS.escape(id)}"]`)?.remove();
  if (!state.generations.length) renderGallery();
}

function startLiveUpdates() {
  stopLiveUpdates();
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
      const payload = JSON.parse(event.data);
      if (event.lastEventId) state.lastEventId = Math.max(state.lastEventId, Number(event.lastEventId));
      if (type === "generation.deleted") removeGeneration(payload.generation_id);
      else if (payload.generation_id) refreshGeneration(payload.generation_id).catch(() => {});
    });
  }
  source.onerror = () => {};
  state.eventSource = source;
  state.serviceTimer = window.setInterval(refreshServices, 10000);
}

function stopLiveUpdates() {
  state.eventSource?.close();
  state.eventSource = null;
  if (state.serviceTimer) window.clearInterval(state.serviceTimer);
  state.serviceTimer = null;
  state.observer?.disconnect();
}

async function refreshServices() {
  try {
    state.services = await api("/api/services");
    renderServiceBanner();
    renderPanel();
  } catch {
    // Session expiry is handled by normal API interaction; avoid disruptive polling errors.
  }
}

function renderServiceBanner() {
  const banner = document.querySelector("#service-banner");
  if (banner) banner.innerHTML = serviceBannerMarkup(state.services);
}

function updateGalleryScale(value, persist) {
  state.galleryScale = Number(value);
  applyGalleryScale();
  const input = document.querySelector("#gallery-scale");
  input?.setAttribute("aria-valuetext", `${state.galleryScale}%`);
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
    <header class="dialog-header"><div><h2>Administration</h2><p>Manage accounts and workflow registration only.</p></div><button type="button" class="icon-button" data-action="close-admin" aria-label="Close administration">×</button></header>
    <div class="admin-content">
      <section><h3>Users</h3><form id="create-user-form" class="inline-form"><label class="field"><span>Username</span><input name="username" required /></label><label class="field"><span>Temporary password</span><input name="temporary_password" type="password" minlength="12" required /></label><button class="button primary" type="submit">Create user</button></form>
      <div class="table-wrap"><table><thead><tr><th>Username</th><th>State</th><th>Created</th><th>Account actions</th></tr></thead><tbody>${ordinary.map((user) => `<tr><td>${escapeForAdmin(user.username)}</td><td>${user.must_change_password ? "Temporary password" : "Active"}</td><td>${new Date(user.created_at).toLocaleDateString()}</td><td><div class="button-row"><button type="button" class="button low" data-action="reset-user-password" data-user-id="${user.id}">Reset password</button><button type="button" class="button destructive low" data-action="delete-user" data-user-id="${user.id}" data-username="${escapeForAdmin(user.username)}">Delete</button></div></td></tr>`).join("") || '<tr><td colspan="4">No ordinary users.</td></tr>'}</tbody></table></div></section>
      <section><div class="section-heading"><h3>Workflow diagnostics</h3><button type="button" class="button secondary" data-action="refresh-workflows">Refresh discovery</button></div><div class="diagnostic-list">${diagnostics.map((item) => `<article class="diagnostic ${item.accepted ? "accepted" : "rejected"}"><strong>${escapeForAdmin(item.basename)}</strong><span>${item.accepted ? "Registered" : "Rejected"}</span><p>${escapeForAdmin(item.message)}</p><code>${escapeForAdmin(item.code)}</code></article>`).join("") || '<p class="muted">No discovery diagnostics yet.</p>'}</div></section>
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
  const password = window.prompt("Enter a new temporary password (at least 12 characters):");
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
  state.workflows = await api("/api/workflows");
  if (!state.workflows.some((item) => item.profile_id === state.activeProfileId)) {
    await selectWorkflow(state.workflows[0]?.profile_id || "");
  }
  await openAdmin();
  toast("Workflow discovery refreshed.", "success");
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
  queueMicrotask(() => document.querySelector('[aria-invalid="true"]')?.focus());
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

function escapeForAdmin(value) {
  const span = document.createElement("span");
  span.textContent = String(value ?? "");
  return span.innerHTML;
}

function renderFatal(error) {
  root.innerHTML = `<main class="auth-page"><section class="auth-card"><h1>Application unavailable</h1><p class="form-error">${escapeForAdmin(error.message || "Startup failed.")}</p><button class="button primary" data-action="reload">Reload</button></section></main>`;
}

initialize();
