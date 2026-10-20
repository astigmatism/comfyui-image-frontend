import { loraDefaultPositiveStrength, loraStackError } from "./lora-stack.mjs";
import { loraManagerMarkup, moveEnabledLora, reconcileLoraDraft, subjectPreview } from "./lora-gallery.mjs";
export { loraManagerMarkup } from "./lora-gallery.mjs";

const encoded = (value) => encodeURIComponent(value);

// The footer shows three lines of the Subject line; when a trigger phrase is
// longer than that, the tooltip is the only way to reach the rest of it.
function syncSubjectPreview(scope) {
  const preview = scope?.querySelector("[data-lora-subject-preview]");
  if (!preview) return;
  if (preview.scrollHeight > preview.clientHeight) preview.title = preview.textContent ?? "";
  else preview.removeAttribute("title");
}

export function loraImagePath(sourceKey, controlId) {
  return `/api/workflows/${encoded(sourceKey)}/lora-images/${encoded(controlId)}`;
}

export function loraPublicationRevisionMatches(first, second) {
  if (!first || !second) return !first && !second;
  return ["publication_id", "workflow_sha256", "api_sha256", "manifest_sha256"].every((key) => first[key] === second[key]);
}

export function installLoraManager(root, { api, context, apply, onImages, notify = () => {}, isAdmin = () => false, adminController = null }) {
  let draft = null;
  let returnFocus = null;
  let fileTarget = null;
  let dragId = null;
  let touchDrag = null;
  let refreshingLibrary = false;
  let managementToken = 0;
  const dialog = () => root.querySelector("#lora-manager-dialog");
  // Image work outlives the selection draft, including Cancel and owner closure.
  const imageStores = new Map();
  const storeKey = (ctx) => JSON.stringify([ctx.sourceKey, ctx.control.id,
    ...["publication_id", "workflow_sha256", "api_sha256", "manifest_sha256"].map((key) => ctx.publicationRevision?.[key] || null)]);
  const matches = (store, ctx) => ctx && store.active && store.key === storeKey(ctx);
  const refreshImages = (store) => { if (draft?.imageStore === store) render(); };
  const publishImages = (store) => {
    for (const owner of ["panel", "rerun"]) {
      const ctx = context(store.controlId, owner);
      if (matches(store, ctx) && JSON.stringify(ctx.images || {}) !== JSON.stringify(store.images)) onImages(store.sourceKey, store.controlId, structuredClone(store.images), owner);
    }
  };
  // Thumbnails are shared by every workflow that lists the LoRA, so a saved change
  // makes other workflows' cached image lists stale; they reload when next opened.
  const forgetOtherStores = (store) => {
    for (const [key, other] of imageStores) {
      if (other !== store && !other.loading && !Object.values(other.states || {}).some((item) => item?.saving)) {
        other.active = false;
        imageStores.delete(key);
      }
    }
  };
  function loadImages(store) {
    if (store.loading) return store.loading;
    store.loadError = "";
    store.loading = (async () => {
      try {
        const paths = store.imageSources ? [...new Set(Object.values(store.imageSources).map((source) => loraImagePath(source.sourceKey, source.controlId)))] : [loraImagePath(store.sourceKey, store.controlId)];
        const payloads = await Promise.all(paths.map((path) => api(path)));
        if (!store.active) return;
        store.images = Object.fromEntries(payloads.flatMap((payload) => (payload.items || []).map((item) => [item.id, item])));
        store.loaded = true;
        publishImages(store);
      } catch (error) {
        store.loaded = false;
        store.loadError = `Images could not be loaded: ${error.message || "Try again."}`;
      } finally {
        store.loading = null;
        refreshImages(store);
      }
    })();
    refreshImages(store);
    return store.loading;
  }
  function saveImage(id, action, file = null) {
    const session = draft;
    const store = session?.imageStore;
    if (!store?.loaded || store.loading || store.states[id]?.saving) return;
    if (!session.management && !matches(store, context(session.control.id, session.owner))) {
      store.loadError = "The workflow changed. Reopen the LoRA manager.";
      refreshImages(store);
      return;
    }
    const preview = file ? URL.createObjectURL(file) : null;
    const version = store.images[id]?.version;
    store.states[id] = { saving: true, preview };
    refreshImages(store);
    // Serialize mutations because every response includes the entire image catalog.
    store.queue = store.queue.then(async () => {
      try {
        if (!store.active || !store.loaded) throw new Error("Reload images before trying again.");
        if (!version) throw new Error("Image versions are unavailable. Reload images and try again.");
        const change = { id, version, action };
        const form = new FormData();
        if (file) { change.file_key = "image_0"; form.append("image_0", file); }
        form.append("changes", JSON.stringify([change]));
        const source = store.imageSources?.[id] || { sourceKey: store.sourceKey, controlId: store.controlId };
        const result = await api(loraImagePath(source.sourceKey, source.controlId), { method: "POST", body: form });
        if (!store.active) return;
        store.images = { ...store.images, ...Object.fromEntries((result.items || []).map((item) => [item.id, item])) };
        store.states[id] = { message: "Saved" };
        publishImages(store);
        forgetOtherStores(store);
      } catch (error) {
        const conflict = error.code === "lora_image_conflict";
        const message = conflict
          ? "This image changed elsewhere. Review the latest image and try your edit again."
          : `Image was not saved: ${error.message || "Try again."}`;
        // Reconcile even a lost response: the server may have committed the upload.
        if (store.active) await loadImages(store);
        store.states[id] = { error: true, message };
        if (!dialog()?.open || draft?.imageStore !== store) notify(message, "error");
      } finally {
        if (preview) URL.revokeObjectURL(preview);
        if (store.states[id]?.saving) delete store.states[id];
        refreshImages(store);
      }
    });
  }
  const render = (focus = null) => {
    if (!draft) return;
    const target = dialog();
    const active = target.contains(document.activeElement) ? document.activeElement : null;
    if (!focus && active) {
      const attribute = [...active.attributes].find((item) => item.name.startsWith("data-lora-"));
      if (attribute) {
        focus = `[${attribute.name}="${CSS.escape(attribute.value)}"]`;
        if (active.closest("[data-lora-id]")) focus = `[data-lora-id="${CSS.escape(active.closest("[data-lora-id]").dataset.loraId)}"] ${focus}`;
      }
    }
    const scrollTop = target.querySelector(".lm-dialog-content")?.scrollTop || 0;
    const toolsOpen = target.querySelector(".lm-library-tools")?.open;
    const editor = target.querySelector(".lm-editor");
    const editorKey = editor?.dataset.editorKey;
    const nextEditorKey = JSON.stringify(draft.panel);
    const markup = loraManagerMarkup({ ...draft, admin: isAdmin(), adminController, images: draft.imageStore.images, imageStates: draft.imageStore.states, imagesLoading: !draft.imageStore.loaded || Boolean(draft.imageStore.loading), imageError: draft.imageStore.loadError });
    const frame = target.querySelector(".lm-dialog-frame");
    if (frame) {
      // Keep the file input alive if another thumbnail finishes saving while
      // the native file chooser is open.
      const template = document.createElement("template");
      template.innerHTML = markup;
      frame.replaceWith(template.content.querySelector(".lm-dialog-frame"));
    } else target.innerHTML = markup;
    const newEditor = target.querySelector(".lm-editor");
    if (editor && newEditor && editorKey === nextEditorKey) {
      newEditor.replaceWith(editor);
      for (const input of editor.querySelectorAll("input,textarea,select,button")) input.disabled = Boolean(draft.busy || draft.groupsBusy || adminController?.state.busy || ["install", "edit"].includes(draft.panel?.kind) && (!adminController?.state.library?.eligible || ["awaiting_upload", "running", "repair_required"].includes(adminController?.currentOperation()?.status)) && !input.matches("[data-lora-panel-close]"));
      if (editor.contains(active)) active.focus({ preventScroll: true });
    } else if (newEditor) newEditor.dataset.editorKey = nextEditorKey;
    if (toolsOpen && target.querySelector(".lm-library-tools")) target.querySelector(".lm-library-tools").open = true;
    target.querySelector(".lm-dialog-content").scrollTop = scrollTop;
    syncSubjectPreview(target);
    if (focus) {
      const element = target.querySelector(focus);
      if (element?.disabled) element.closest("[data-lora-id]")?.querySelector("[data-lora-toggle]")?.focus({ preventScroll: true });
      else element?.focus({ preventScroll: true });
    }
    positionMenu();
  };
  function positionMenu() {
    const menu = dialog()?.querySelector("[data-lora-popover]");
    const button = dialog()?.querySelector(`[data-lora-menu="${CSS.escape(draft?.menuId || "")}"]`);
    if (!menu || !button) return;
    const box = dialog().getBoundingClientRect(), anchor = button.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(box.width - menu.offsetWidth - 8, anchor.right - box.left - menu.offsetWidth))}px`;
    menu.style.top = `${Math.max(8, Math.min(box.height - menu.offsetHeight - 8, anchor.bottom - box.top))}px`;
  }
  function createImageStore(ctx) {
    const key = storeKey(ctx);
    let store = imageStores.get(key);
    if (!store) {
      store = { key, sourceKey: ctx.sourceKey, controlId: ctx.control.id, imageSources: ctx.imageSources, active: true,
        images: structuredClone(ctx.images || {}), states: {}, loaded: false, loading: null, loadError: "", queue: Promise.resolve() };
      imageStores.set(key, store);
    }
    return store;
  }
  async function loadGroups(session = draft) {
    if (!session) return;
    session.groupingLoading = true;
    session.groupingError = "";
    render();
    try {
      const path = session.management ? `/api/lora-library/${encoded(session.libraryKey)}/groups` : `/api/workflows/${encoded(session.sourceKey)}/lora-groups/${encoded(session.control.id)}`;
      const grouping = await api(path);
      if (!Array.isArray(grouping.groups) || !grouping.assignments) throw new Error("Group data is unavailable.");
      session.grouping = grouping;
      session.libraryKey = grouping.library_key;
    } catch (error) {
      session.groupingError = `Groups could not be loaded: ${error.message}`;
      session.grouping = null;
    } finally {
      session.groupingLoading = false;
      if (draft === session) render();
    }
  }
  const close = () => dialog()?.close("cancel");
  async function open(button) {
    const owner = button.dataset.controlContext || "panel";
    const ctx = context(button.dataset.loraControlId, owner);
    if (!ctx || dialog()?.open) return;
    returnFocus = button;
    const imageStore = createImageStore(ctx);
    draft = { ...ctx, owner, publicationRevision: structuredClone(ctx.publicationRevision || null), values: structuredClone(ctx.values), memory: { ...ctx.memory }, imageStore, error: "", busy: false, panel: null, menuId: null };
    render();
    configureCancel();
    dialog().showModal();
    dialog().querySelector("[data-lora-toggle]")?.focus({ preventScroll: true });
    const session = draft;
    await Promise.all([loadGroups(session), !Object.values(imageStore.states).some((state) => state.saving) ? loadImages(imageStore) : Promise.resolve()]);
    if (draft === session && isAdmin() && adminController) await adminController.initialize(session.libraryKey);
  }
  function configureCancel() {
    dialog().oncancel = (event) => {
      if (draft?.menuId) { event.preventDefault(); const id = draft.menuId; draft.menuId = null; render(`[data-lora-menu="${CSS.escape(id)}"]`); }
      else if (draft?.busy || draft?.groupsBusy || adminController?.state.busy) event.preventDefault();
    };
  }
  async function managementContext() {
    const library = adminController.state.library;
    if (!library) return { control: { id: "loras", items: [], default: [] }, sourceKey: "library-empty", sourceName: "No published LoRA libraries", values: [], memory: {}, management: true };
    const details = await Promise.all(library.members.map((member) => api(`/api/workflows/${encoded(member.source_key)}`)));
    const imageSources = {};
    for (let index = 0; index < details.length; index += 1) {
      const control = (details[index].interface?.inputs || []).find((item) => item.type === "lora_stack");
      for (const item of control?.items || []) imageSources[item.id] = { sourceKey: library.members[index].source_key, controlId: control.id };
    }
    return { control: { id: "library", items: library.items, default: library.items.map((item) => ({ id: item.id, strength: 0 })) }, sourceKey: `library-${library.key}`, sourceName: library.label, libraryKey: library.key, values: [], memory: {}, management: true, imageSources };
  }
  async function openLibrary(button = null) {
    if (!isAdmin() || !adminController || dialog()?.open) return;
    returnFocus = button || document.activeElement;
    try {
      await adminController.initialize();
      await showManagement();
      configureCancel();
      dialog().showModal();
      dialog().querySelector("[data-lora-cancel]")?.focus();
    } catch (error) { notify(`The LoRA library could not be opened: ${error.message}`, "error"); }
  }
  async function showManagement() {
    const token = ++managementToken;
    const ctx = await managementContext();
    if (token !== managementToken) return;
    draft = { ...ctx, imageStore: createImageStore(ctx), panel: null, menuId: null, error: "", busy: false };
    render();
    if (ctx.libraryKey) await Promise.all([loadGroups(draft), loadImages(draft.imageStore)]);
  }
  function updateStrength(id, strength) {
    if (!draft) return;
    draft.values = draft.values.map((entry) => entry.id === id ? { id, strength } : entry);
    if (strength > 0) draft.memory[id] = strength;
    const preview = dialog()?.querySelector("[data-lora-subject-preview]");
    if (preview) preview.textContent = subjectPreview(draft.control, draft.values, draft.subjectAvailable);
    syncSubjectPreview(dialog());
  }
  function toggle(id) {
    if (!draft) return;
    const previous = draft.values.find((entry) => entry.id === id)?.strength || 0;
    if (previous > 0) draft.memory[id] = previous;
    let strength = previous > 0 ? 0 : draft.memory[id] || loraDefaultPositiveStrength(draft.control);
    if (previous <= 0 && loraStackError(draft.control, draft.values.map((entry) => entry.id === id ? { id, strength } : entry))) strength = loraDefaultPositiveStrength(draft.control);
    updateStrength(id, strength);
    render(`[data-lora-toggle="${CSS.escape(id)}"]`);
  }
  function move(id, to) {
    if (!draft || to < 0 || to >= draft.values.filter((entry) => entry.strength > 0).length) return;
    draft.values = moveEnabledLora(draft.values, id, to);
    render(`[data-lora-order-id="${CSS.escape(id)}"] [data-lora-handle]`);
    const label = draft.control.items.find((item) => item.id === id)?.label || id;
    dialog()?.querySelector("[data-lora-reorder-status]")?.replaceChildren(document.createTextNode(`${label} moved to position ${to + 1} of ${draft.values.filter((entry) => entry.strength > 0).length}.`));
  }
  async function save() {
    if (!draft || draft.busy || draft.reconcileError || adminController?.state.busy || draft.management) return;
    const session = draft;
    const current = context(draft.control.id, draft.owner);
    if (current?.sourceKey !== draft.sourceKey || !loraPublicationRevisionMatches(current?.publicationRevision, draft.publicationRevision)) {
      draft.error = "The workflow changed. Reopen the LoRA manager.";
      render();
      return;
    }
    const error = loraStackError(draft.control, draft.values);
    if (error) { draft.error = error; render(); return; }
    draft.busy = true;
    draft.error = "";
    render();
    try {
      if (draft !== session) return;
      apply(draft.control.id, draft.values, draft.memory, draft.sourceKey, draft.owner);
      dialog().close("apply");
    } catch (error) {
      if (draft !== session) return;
      draft.error = error.message || "Could not apply LoRAs.";
      draft.busy = false;
      render();
    }
  }
  function openPanel(kind, id = null) {
    if (!draft || draft.busy || draft.groupsBusy || adminController?.state.busy) return;
    draft.panel = { kind, id, operationId: adminController?.currentOperation()?.id };
    draft.menuId = null;
    draft.error = "";
    if (kind === "edit") adminController.beginEdit(id);
    render();
    dialog()?.querySelector(".lm-editor input,.lm-editor select,.lm-editor button")?.focus();
  }
  async function mutateGroups(path, method, fields) {
    const session = draft;
    if (!isAdmin() || !session?.grouping || session.groupsBusy) return;
    session.groupsBusy = true;
    session.error = "";
    render();
    try {
      const result = await api(`/api/admin/lora-library/${encoded(session.libraryKey)}/${path}`, { method, body: JSON.stringify({ revision: session.grouping.revision, ...fields }) });
      if (session !== draft) return;
      session.grouping = result;
      session.panel = null;
    } catch (error) {
      // A lost response can also have committed: always reload before another edit.
      await loadGroups(session);
      if (session === draft) session.error = error.message || "The group change could not be saved.";
    } finally {
      session.groupsBusy = false;
      if (session === draft) render();
    }
  }
  async function deleteGroup(id) {
    if (!window.confirm("Delete this group? Its LoRAs will move to Uncategorized. No LoRA files will be deleted.")) return;
    await mutateGroups(`groups/${encoded(id)}`, "DELETE", {});
  }
  adminController?.subscribe(() => {
    if (!draft || !isAdmin()) return;
    const operation = adminController.currentOperation();
    if (["install", "edit"].includes(draft.panel?.kind) && operation?.status === "succeeded" && operation.id !== draft.panel.operationId) draft.panel = null;
    render();
  });
  root.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-lora-open]");
    if (opener) { void open(opener); return; }
    if (!draft || !event.target.closest("#lora-manager-dialog")) return;
    if (event.target.closest("button:disabled")) return;
    const menu = event.target.closest("[data-lora-menu]");
    if (menu) { draft.menuId = draft.menuId === menu.dataset.loraMenu ? null : menu.dataset.loraMenu; render(); dialog()?.querySelector("[data-lora-popover] button")?.focus(); return; }
    const actions = [
      ["[data-lora-details]", "details", "loraDetails"], ["[data-lora-move]", "move", "loraMove"],
      ["[data-lora-edit]", "edit", "loraEdit"], ["[data-lora-group-rename]", "rename", "loraGroupRename"],
    ];
    for (const [selector, kind, key] of actions) {
      const button = event.target.closest(selector);
      if (button) { openPanel(kind, button.dataset[key]); return; }
    }
    if (event.target.closest("[data-lora-panel-close]")) {
      if (!adminController?.state.busy && !draft.groupsBusy) { if (draft.panel?.kind === "edit") adminController.cancelEdit(draft.panel.id); draft.panel = null; render(); }
      return;
    }
    if (event.target.closest("[data-lora-add]")) { openPanel("install"); return; }
    if (event.target.closest("[data-lora-group-add]")) { openPanel("create"); return; }
    const deletion = event.target.closest("[data-lora-delete]");
    if (deletion && isAdmin()) { draft.menuId = null; void adminController.remove(deletion.dataset.loraDelete); render(); return; }
    const groupDeletion = event.target.closest("[data-lora-group-delete]");
    if (groupDeletion) { void deleteGroup(groupDeletion.dataset.loraGroupDelete); return; }
    if (event.target.closest("[data-lora-groups-reload]")) { void loadGroups(); return; }
    if (event.target.closest("[data-lora-library-sync]")) { void adminController.sync(); return; }
    if (event.target.closest("[data-lora-library-retry]")) { void adminController.retryUpload(); return; }
    if (event.target.closest("[data-lora-library-cancel-upload]")) { void adminController.cancelPendingUpload(); return; }
    if (event.target.closest("[data-lora-library-reload]")) { void adminController.initialize(draft.libraryKey).then(() => draft?.management ? showManagement() : loadGroups()); return; }
    const selection = event.target.closest("[data-lora-toggle]");
    if (selection && !selection.disabled) { toggle(selection.dataset.loraToggle); return; }
    if (event.target.closest("[data-lora-cancel]")) { if (!draft.busy && !draft.groupsBusy && !adminController?.state.busy) close(); return; }
    if (event.target.closest("[data-lora-apply]")) { void save(); return; }
    if (event.target.closest("[data-lora-all-off]")) {
      for (const entry of draft.values) if (entry.strength > 0) draft.memory[entry.id] = entry.strength;
      draft.values = draft.values.map((entry) => ({ id: entry.id, strength: 0 }));
      render(); return;
    }
    if (event.target.closest("[data-lora-images-reload]")) {
      if (!Object.values(draft.imageStore.states).some((state) => state.saving)) void loadImages(draft.imageStore);
      return;
    }
    const image = event.target.closest("[data-lora-image-change]");
    if (image && !image.disabled) {
      fileTarget = { id: image.dataset.loraImageChange, session: draft };
      draft.menuId = null;
      dialog().querySelector("[data-lora-file]")?.click();
      return;
    }
    const remove = event.target.closest("[data-lora-image-remove]");
    if (remove && !remove.disabled) { draft.menuId = null; saveImage(remove.dataset.loraImageRemove, "remove"); return; }
    if (draft.menuId && !event.target.closest("[data-lora-popover]")) { draft.menuId = null; render(); }
  });
  root.addEventListener("submit", (event) => {
    if (!draft || !event.target.closest("#lora-manager-dialog")) return;
    event.preventDefault();
    const form = event.target;
    const libraryAction = form.dataset.loraLibraryForm;
    if (libraryAction && isAdmin()) { if (libraryAction === "install") void adminController.install(form); else void adminController.edit(libraryAction, form); return; }
    const action = form.dataset.loraGroupForm;
    if (!action) return;
    const data = new FormData(form);
    if (action === "move") {
      const item = draft.control.items.find((entry) => entry.id === draft.panel.id);
      void mutateGroups(`group-memberships/${encoded(item.lora_identity)}`, "PUT", { group_id: data.get("group_id") });
    } else {
      const path = action === "create" ? "groups" : `groups/${encoded(draft.panel.id)}`;
      void mutateGroups(path, action === "create" ? "POST" : "PATCH", { name: data.get("name") });
    }
  });
  root.addEventListener("change", (event) => {
    if (!draft) return;
    if (event.target.matches("[data-lora-library-family]") && !adminController?.state.busy) {
      adminController.selectLibrary(event.target.value);
      void showManagement().catch((error) => { if (draft) { draft.error = error.message; render(); } });
      return;
    }
    const fileInput = event.target.closest("[data-lora-file]");
    if (fileInput) {
      const file = fileInput.files?.[0];
      fileInput.value = "";
      if (!file || !fileTarget || fileTarget.session !== draft) return;
      const id = fileTarget.id;
      fileTarget = null;
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
        draft.imageStore.states[id] = { error: true, message: "Choose a PNG, JPEG, or WebP image." };
        render(); return;
      }
      saveImage(id, "set", file);
      return;
    }
  });
  root.addEventListener("input", (event) => {
    if (!draft) return;
    if (draft.panel?.kind === "edit" && adminController?.state.editDraft && ["display_name", "trigger_word"].includes(event.target.name)) {
      adminController.state.editDraft[event.target.name === "display_name" ? "displayName" : "triggerWord"] = event.target.value;
    }
  });
  root.addEventListener("keydown", (event) => {
    if (draft?.menuId && event.target.closest("[data-lora-popover]") && ["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const buttons = [...dialog().querySelectorAll("[data-lora-popover] button:not(:disabled)")];
      const index = buttons.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length;
      buttons[next]?.focus();
      return;
    }
    const handle = event.target.closest("#lora-manager-dialog [data-lora-handle]");
    if (!draft || !handle || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const id = handle.closest("[data-lora-order-id]").dataset.loraOrderId;
    move(id, draft.values.filter((entry) => entry.strength > 0).findIndex((entry) => entry.id === id) + (["ArrowUp", "ArrowLeft"].includes(event.key) ? -1 : 1));
  });
  root.addEventListener("dragstart", (event) => {
    const handle = event.target.closest("#lora-manager-dialog [data-lora-handle]");
    if (!draft || !handle) return;
    dragId = handle.closest("[data-lora-order-id]").dataset.loraOrderId;
    event.dataTransfer.setData("text/plain", dragId);
    event.dataTransfer.effectAllowed = "move";
  });
  root.addEventListener("dragover", (event) => {
    if (!dragId) return;
    const row = event.target.closest("#lora-manager-dialog [data-lora-order-id]");
    if (row) { event.preventDefault(); row.classList.add("is-drop-target"); }
  });
  root.addEventListener("dragleave", (event) => event.target.closest("#lora-manager-dialog [data-lora-order-id]")?.classList.remove("is-drop-target"));
  root.addEventListener("drop", (event) => {
    if (!draft || !dragId) return;
    const row = event.target.closest("#lora-manager-dialog [data-lora-order-id]");
    if (!row) return;
    event.preventDefault();
    const index = draft.values.filter((entry) => entry.strength > 0).findIndex((entry) => entry.id === row.dataset.loraOrderId);
    move(dragId, index);
    dragId = null;
  });
  root.addEventListener("dragend", () => { dragId = null; });
  root.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest("#lora-manager-dialog [data-lora-handle]");
    if (!draft || !handle || event.pointerType === "mouse") return;
    touchDrag = handle.closest("[data-lora-order-id]").dataset.loraOrderId;
    handle.setPointerCapture(event.pointerId);
  });
  root.addEventListener("pointermove", (event) => {
    if (!draft || !touchDrag) return;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest("#lora-manager-dialog [data-lora-order-id]");
    dialog()?.querySelectorAll(".is-drop-target").forEach((row) => row.classList.remove("is-drop-target"));
    target?.classList.add("is-drop-target");
  });
  root.addEventListener("pointerup", (event) => {
    if (!draft || !touchDrag) return;
    const id = touchDrag;
    touchDrag = null;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest("#lora-manager-dialog [data-lora-order-id]");
    dialog()?.querySelectorAll(".is-drop-target").forEach((row) => row.classList.remove("is-drop-target"));
    if (target) move(id, draft.values.filter((entry) => entry.strength > 0).findIndex((entry) => entry.id === target.dataset.loraOrderId));
  });
  root.addEventListener("pointercancel", () => { touchDrag = null; });
  root.addEventListener("close", (event) => {
    if (event.target !== dialog() || dialog().open) return;
    // Native close is queued. A user can already be editing the sidebar by the
    // time it arrives; do not steal that focus and commit an unfinished edit.
    const restoreFocus = document.activeElement === document.body || event.target.contains(document.activeElement);
    const controlId = draft?.control.id;
    const owner = draft?.owner;
    fileTarget = null;
    managementToken += 1;
    draft = null;
    if (!restoreFocus) return;
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    else if (controlId) root.querySelector(`${owner === "rerun" ? "#gallery-rerun-dialog[open]" : "#generation-panel"} [data-lora-open][data-lora-control-id="${CSS.escape(controlId)}"]`)?.focus({ preventScroll: true });
  }, true);
  return {
    close, openLibrary,
    closeForOwner: (owner) => { if (draft?.owner === owner) close(); },
    isOpen: () => Boolean(dialog()?.open),
    // A null key means every workflow changed (a shared LoRA library operation).
    invalidateSource: (sourceKey) => {
      for (const [key, store] of imageStores) {
        if (sourceKey === null || store.sourceKey === sourceKey) { store.active = false; imageStores.delete(key); }
      }
      if (sourceKey !== null && draft?.sourceKey !== sourceKey) return;
      if (draft && !refreshingLibrary) close();
    },
    refreshLibrarySources: async (refresh) => {
      const session = draft;
      refreshingLibrary = true;
      if (session) { session.busy = true; session.menuId = null; render(); }
      try {
        for (const store of imageStores.values()) store.active = false;
        imageStores.clear();
        const result = await refresh();
        if (!session || draft !== session) return result;
        if (session.management) {
          // Discovery has updated the publications; the controller reloads its
          // library snapshot after this callback, so fetch it before rebuilding.
          await adminController.loadLibrary();
          await showManagement();
        } else {
          const current = context(session.control.id, session.owner);
          if (!current || current.sourceKey !== session.sourceKey) throw new Error("The workflow is unavailable. Reopen the LoRA manager after refreshing workflows.");
          const published = await api(`/api/workflows/${encoded(current.sourceKey)}`);
          if (!loraPublicationRevisionMatches(published.revision, current.publicationRevision)) throw new Error("The workflow refresh is incomplete. Reopen the LoRA manager after refreshing workflows.");
          const reconciled = reconcileLoraDraft(session, current);
          Object.assign(session, reconciled, { imageStore: createImageStore(current), panel: null, reconcileError: false, error: "" });
          await Promise.all([loadGroups(session), loadImages(session.imageStore)]);
        }
        return result;
      } catch (error) {
        if (draft === session && session) { session.reconcileError = true; session.error = error.message; }
        throw error;
      } finally {
        refreshingLibrary = false;
        if (draft) { draft.busy = false; render(); }
      }
    },
    refresh: () => { if (draft && dialog()?.open) render(); },
  };
}
