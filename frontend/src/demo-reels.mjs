const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const encoded = encodeURIComponent;

export function demoReelPath(libraryKey, identity) {
  return `/api/demo-reels/${encoded(libraryKey)}/${encoded(identity)}`;
}

export function demoReelRerunApi(api) {
  return (path, options) => {
    if (path === "/api/gallery/prompt-rerun/preview" && options?.body) {
      const body = JSON.parse(options.body);
      const scope = body.demo_reel;
      if (scope) {
        delete body.demo_reel;
        return api(`${demoReelPath(scope.library_key, scope.lora_identity)}/prompt-rerun/preview`, { ...options, body: JSON.stringify(body) });
      }
    }
    return api(path, options);
  };
}

export function expandDemoReelPrompt(prompt, subject) {
  return String(prompt || "").replaceAll("{subject}", () => subject || "");
}

export function moveDemoReelSlot(slots, id, destination) {
  const index = slots.findIndex((slot) => slot.id === id);
  if (index < 0 || destination < 0 || destination >= slots.length) return slots;
  const result = [...slots];
  result.splice(destination, 0, result.splice(index, 1)[0]);
  return result;
}

export function demoReelGalleryMarkup(view) {
  const groups = (view.slots || []).map((slot, index) => {
    const entries = view.entries.filter((entry) => entry.slot_id === slot.id);
    if (!entries.length) return "";
    return `<section class="demo-reel-slot"><h3>${escape(slot.title || `Slot ${index + 1}`)}</h3><p class="muted demo-reel-prompt">${escape(expandDemoReelPrompt(slot.prompt, view.subject))}</p><div class="demo-reel-grid">${entries.map((entry) => {
      const generation = entry.generation;
      const image = generation?.display_artifact;
      return `<article class="demo-reel-card" data-demo-generation="${escape(generation?.id || "")}">${image ? `<button type="button" class="demo-reel-image" data-demo-action="photo" data-demo-id="${escape(generation.id)}" aria-label="View ${escape(entry.checkpoint_label)} image"><img src="${escape(image.thumbnail_url || image.content_url)}" alt="${escape(entry.checkpoint_label)}" loading="lazy" /></button>` : `<div class="demo-reel-placeholder"><strong>${escape(entry.state)}</strong>${entry.error ? `<span>${escape(entry.error)}</span>` : ""}</div>`}<div class="demo-reel-caption"><strong>${escape(entry.checkpoint_label)}</strong>${generation ? `<div class="demo-reel-actions"><button type="button" data-demo-action="favorite" data-demo-id="${escape(generation.id)}" aria-pressed="${generation.is_favorite}">${generation.is_favorite ? "★ Favorited" : "☆ Favorite"}</button><a href="${escape(image.content_url)}" download>Download</a><button type="button" data-demo-action="details" data-demo-id="${escape(generation.id)}">Details</button><button type="button" data-demo-action="recall" data-demo-id="${escape(generation.id)}">Recall</button><button type="button" data-demo-action="rerun" data-demo-id="${escape(generation.id)}">Re-run prompt</button><button type="button" data-demo-action="copy" data-demo-id="${escape(generation.id)}">Copy</button>${view.can_manage ? `<button type="button" data-demo-action="move" data-demo-id="${escape(generation.id)}">Move</button><button type="button" data-demo-action="regenerate" data-demo-id="${escape(generation.id)}">Regenerate</button><button type="button" class="destructive" data-demo-action="delete" data-demo-id="${escape(generation.id)}">Delete</button>` : ""}</div>` : ""}</div></article>`;
    }).join("")}</div></section>`;
  }).join("");
  return `<div class="dialog-frame demo-reel-frame"><header class="dialog-header"><div><h2 id="demo-reel-title">${escape(view.label)} · Demo Reel</h2><p role="status">${view.completed} of ${view.expected} images · ${escape(view.status)}${view.rating_username ? ` · A/B grades from ${escape(view.rating_username)}` : ""}</p></div><button type="button" class="button secondary" data-demo-action="close">Back to LoRAs</button></header><div class="demo-reel-content">${view.can_manage ? `<div class="button-row"><button type="button" class="button secondary" data-demo-action="settings">Edit Demo Reel</button><button type="button" class="button low" data-demo-action="retry">Retry failed images</button><button type="button" class="button low" data-demo-action="${view.paused ? "resume" : "pause"}">${view.paused ? "Resume" : "Pause"} background generation</button></div>` : ""}${view.message ? `<p class="form-error" role="alert">${escape(view.message)}</p>` : ""}${groups || '<p class="empty-state">No Demo Reel images yet. Images appear after prompts, a generation recipe, and A/B checkpoints are configured, then all users have been idle for five minutes.</p>'}${view.next_offset !== null ? '<button type="button" class="button secondary" data-demo-action="more">Load more</button>' : ""}</div></div>`;
}

function parameterMarkup(control, value, attributes) {
  const label = escape(control.label || control.id);
  if (control.type === "boolean") return `<label class="demo-reel-boolean"><input type="checkbox" ${attributes} ${value ? "checked" : ""} /> ${label}</label>`;
  if (control.type === "choice") return `<label class="field"><span>${label}</span><select ${attributes}>${(control.choices || []).map((choice) => `<option value="${escape(choice.value)}" ${choice.value === value ? "selected" : ""}>${escape(choice.label)}</option>`).join("")}</select></label>`;
  if (["integer", "float", "decimal", "number"].includes(control.type)) return `<label class="field"><span>${label}</span><input type="number" ${attributes} value="${escape(value)}" ${control.minimum !== undefined ? `min="${escape(control.minimum)}"` : ""} ${control.maximum !== undefined ? `max="${escape(control.maximum)}"` : ""} step="${escape(control.step || "any")}" required /></label>`;
  return `<label class="field"><span>${label}</span><textarea rows="2" ${attributes}>${escape(value)}</textarea></label>`;
}

const recipeInputs = (detail) => (detail?.interface?.inputs || []).filter((control) => control.type !== "lora_stack" && control.type !== "seed" && !["image", "image_upload", "mask_upload"].includes(control.type) && control.semantic_role !== "positive_prompt");

export function demoReelSettingsMarkup(config, details = {}, context = {}, users = []) {
  const states = config.progress?.states || {};
  const status = (config.saved_paused ?? config.paused) ? "paused" : states.running ? "generating" : states.blocked ? "blocked" : states.failed ? "failed" : config.progress?.expected && config.progress.completed === config.progress.expected ? "complete" : "waiting";
  const progress = config.progress ? `${config.progress.completed} of ${config.progress.expected} images complete · ${status}` : status;
  const slots = config.slots.map((slot, index) => `<li class="demo-reel-slot-editor" data-demo-slot="${escape(slot.id)}"><div class="button-row"><button type="button" draggable="true" class="icon-button" data-demo-slot-handle="${escape(slot.id)}" aria-label="Reorder slot ${index + 1}" aria-description="Drag or use arrow keys">⠿</button><strong>Slot ${index + 1}</strong><button type="button" data-demo-action="slot-up" data-demo-id="${escape(slot.id)}" ${index === 0 ? "disabled" : ""}>↑</button><button type="button" data-demo-action="slot-down" data-demo-id="${escape(slot.id)}" ${index === config.slots.length - 1 ? "disabled" : ""}>↓</button><button type="button" class="destructive" data-demo-action="slot-delete" data-demo-id="${escape(slot.id)}">Remove slot</button></div><label class="field"><span>Title (optional)</span><input maxlength="120" data-demo-slot-field="title" data-demo-id="${escape(slot.id)}" value="${escape(slot.title)}" /></label><label class="field"><span>Prompt · use {subject} for the LoRA trigger phrase</span><textarea rows="3" maxlength="100000" required data-demo-slot-field="prompt" data-demo-id="${escape(slot.id)}">${escape(slot.prompt)}</textarea></label><p class="muted" data-demo-preview="${escape(slot.id)}">Preview: ${escape(expandDemoReelPrompt(slot.prompt, context.subject || "LoRA subject"))}</p><details><summary>Resolution overrides</summary>${(config.libraries || []).map((library) => {
    const recipe = config.recipes[library.key];
    const dimensions = recipeInputs(details[recipe?.source_key]).filter((input) => ["width", "height"].includes(input.semantic_role));
    return dimensions.length ? `<fieldset><legend>${escape(library.label)} · blank inherits recipe</legend><div class="demo-reel-fields">${dimensions.map((control) => `<label class="field"><span>${escape(control.label)}</span><input type="number" min="${escape(control.minimum)}" max="${escape(control.maximum)}" step="${escape(control.step || 1)}" data-demo-resolution="${escape(control.id)}" data-demo-family="${escape(library.key)}" data-demo-id="${escape(slot.id)}" value="${escape(slot.resolutions?.[library.key]?.[control.id] ?? "")}" placeholder="${escape(recipe.parameters?.[control.id] ?? control.default)}" /></label>`).join("")}</div></fieldset>` : "";
  }).join("")}</details></li>`).join("");
  const recipes = (config.libraries || []).map((library) => {
    const recipe = config.recipes[library.key];
    const detail = details[recipe?.source_key];
    return `<fieldset class="demo-reel-recipe"><legend>${escape(library.label)}</legend><label class="demo-reel-boolean"><input type="checkbox" data-demo-recipe-enabled="${escape(library.key)}" ${recipe ? "checked" : ""} /> Generate Demo Reel images for this model family</label>${recipe ? `<label class="field"><span>Workflow</span><select data-demo-source="${escape(library.key)}">${library.sources.map((source) => `<option value="${escape(source.source_key)}" ${source.source_key === recipe.source_key ? "selected" : ""}>${escape(source.label)}</option>`).join("")}</select></label><label class="field"><span>Default LoRA strength</span><input type="number" min="0.000001" step="any" required data-demo-strength="${escape(library.key)}" value="${escape(recipe.strength)}" /></label><details><summary>Generation settings</summary><p class="muted">Every image uses a new random seed and only its target selectable LoRA.</p><div class="demo-reel-fields">${recipeInputs(detail).map((control) => parameterMarkup(control, recipe.parameters[control.id] ?? control.default, `data-demo-parameter="${escape(control.id)}" data-demo-family="${escape(library.key)}" data-demo-type="${escape(control.type)}"`)).join("")}</div></details>` : ""}</fieldset>`;
  }).join("");
  const override = context.identity && config.recipes[context.libraryKey] ? `<label class="field"><span>${escape(context.label)} · LoRA strength override (optional)</span><input type="number" min="0.000001" step="any" data-demo-lora-strength value="${escape(config.strengths?.[context.libraryKey]?.[context.identity] ?? "")}" placeholder="${escape(config.recipes[context.libraryKey].strength)}" /></label>` : "";
  return `<form class="dialog-frame demo-reel-settings-frame" data-demo-settings-form><header class="dialog-header"><div><h2 id="demo-reel-settings-title">Demo Reel settings</h2><p>Shared prompts apply to every installed LoRA.</p><p role="status" data-demo-settings-status>${escape(progress)}</p></div><button type="button" class="icon-button" data-demo-action="cancel-settings" aria-label="Close Demo Reel settings">×</button></header><div class="demo-reel-content"><label class="demo-reel-boolean"><input type="checkbox" data-demo-paused ${config.paused ? "checked" : ""} /> Pause background generation</label><label class="field"><span>Use A/B checkpoint grades from</span><select data-demo-rating-user>${users.map((user) => `<option value="${escape(user.id)}" ${user.id === config.rating_user_id ? "selected" : ""}>${escape(user.username)}</option>`).join("")}</select></label><h3>Prompt slots</h3><ol class="demo-reel-slots">${slots}</ol><button type="button" class="button secondary" data-demo-action="slot-add">Add prompt slot</button><h3>Generation recipes</h3>${recipes || '<p class="muted">No published LoRA libraries are available.</p>'}${override}<p class="form-error" role="alert" data-demo-settings-error></p></div><footer class="dialog-actions"><span class="muted">Saving waits for five quiet minutes. Changes to prompts or generation settings replace affected images.</span><button type="button" class="button secondary" data-demo-action="reload-settings">Reload saved settings</button><button type="button" class="button secondary" data-demo-action="cancel-settings">Cancel</button><button type="submit" class="button primary">Save</button></footer></form>`;
}

export function createDemoReels({ root, api, isAdmin, user, notify, onPhoto, onDetails, onRecall, onRerun = () => {}, onChanged = () => {}, onClose = () => {} }) {
  const gallery = document.createElement("dialog");
  gallery.id = "demo-reel-dialog";
  gallery.className = "demo-reel-dialog";
  gallery.setAttribute("aria-labelledby", "demo-reel-title");
  const editor = document.createElement("dialog");
  editor.id = "demo-reel-settings-dialog";
  editor.className = "demo-reel-settings-dialog";
  editor.setAttribute("aria-labelledby", "demo-reel-settings-title");
  const transfer = document.createElement("dialog");
  transfer.className = "demo-reel-transfer-dialog";
  transfer.setAttribute("aria-label", "Choose destination collection");
  root.append(gallery, editor, transfer);
  let view = null, context = {}, settings = null, users = [], timer = null, loading = false, revision = 0, dragId = null, transferDraft = null;
  const details = {};
  const items = () => (view?.entries || []).flatMap((entry) => entry.generation ? [entry.generation] : []);
  const find = (id) => items().find((item) => item.id === id);
  const path = () => demoReelPath(context.libraryKey, context.identity);
  const render = () => { if (view) gallery.innerHTML = demoReelGalleryMarkup(view); };
  const renderSettings = () => { if (settings) editor.innerHTML = demoReelSettingsMarkup(settings, details, context, users); };
  async function loadRecipe(sourceKey) {
    details[sourceKey] = await api(`/api/workflows/${encoded(sourceKey)}`);
  }
  async function refresh(append = false) {
    if (loading || !gallery.open) return;
    loading = true;
    const token = revision;
    const scroll = gallery.querySelector(".demo-reel-content")?.scrollTop || 0;
    const offset = append ? view?.next_offset : 0;
    if (append && offset == null) { loading = false; return; }
    try {
      const next = await api(`${path()}?offset=${offset || 0}&limit=60`);
      if (token !== revision || !gallery.open) return;
      if (append) {
        const seen = new Set();
        next.entries = [...view.entries, ...next.entries].filter((entry) => !seen.has(entry.id) && seen.add(entry.id));
      }
      else if (view?.entries.length > 60) {
        for (let start = 60; start < view.entries.length && start < next.expected; start += 60) {
          const page = await api(`${path()}?offset=${start}&limit=60`);
          if (token !== revision || !gallery.open) return;
          next.entries.push(...page.entries);
          next.next_offset = page.next_offset;
        }
      }
      view = next;
      render();
      gallery.querySelector(".demo-reel-content").scrollTop = scroll;
      onChanged();
      return true;
    } catch (error) {
      if (view) { view.message = error.message; render(); }
      else notify(error.message, "error");
      return false;
    } finally { loading = false; }
  }
  async function open(nextContext) {
    if (!gallery.isConnected) root.append(gallery, editor, transfer);
    context = nextContext;
    revision += 1;
    view = { ...nextContext, label: nextContext.label, subject: nextContext.subject, entries: [], slots: [], completed: 0, expected: 0, status: "loading", next_offset: null, can_manage: isAdmin() };
    render();
    if (!gallery.open) gallery.showModal();
    await refresh();
    clearInterval(timer);
    timer = setInterval(() => { if (!document.hidden && !editor.open && !transfer.open) void refresh(); }, 5000);
  }
  function close() { gallery.close(); }
  gallery.addEventListener("close", () => { revision += 1; clearInterval(timer); timer = null; view = null; onClose(); });
  async function openSettings(nextContext = context) {
    if (!isAdmin()) return;
    if (!editor.isConnected) root.append(gallery, editor, transfer);
    context = nextContext;
    const [saved, accounts] = await Promise.all([api("/api/admin/demo-reels"), api("/api/admin/users")]);
    settings = structuredClone(saved);
    settings.saved_paused = saved.paused;
    settings.rating_user_id ||= user()?.id;
    users = accounts;
    await Promise.all(Object.values(settings.recipes).map((recipe) => loadRecipe(recipe.source_key)));
    renderSettings();
    if (!editor.open) editor.showModal();
  }
  async function action(kind, ids) {
    if (kind === "favorite") kind = find(ids[0])?.is_favorite ? "unfavorite" : "favorite";
    await api(`${path()}/actions`, { method: "POST", body: JSON.stringify({ action: kind, generation_ids: ids, revision: view?.revision ?? null }) });
    await refresh();
    if (["delete", "move", "regenerate"].includes(kind)) notify("Removed from Demo Reel. A new random image will be generated when everyone is idle.", "success");
  }
  async function chooseTransfer(kind, id) {
    const collections = await api("/api/collections");
    transferDraft = { kind, id, key: crypto.randomUUID() };
    transfer.innerHTML = `<form class="dialog-frame" data-demo-transfer-form><h2>${kind === "move" ? "Move" : "Copy"} to collection</h2><label class="field"><span>Destination</span><select name="collection_id"><option value="">Gallery root</option>${collections.map((collection) => `<option value="${escape(collection.id)}">${escape(collection.name)}</option>`).join("")}</select></label><p data-demo-transfer-error class="form-error" role="alert"></p><div class="button-row"><button type="button" class="button secondary" data-demo-action="cancel-transfer">Cancel</button><button type="submit" class="button primary">${kind === "move" ? "Move" : "Copy"}</button></div></form>`;
    transfer.showModal();
  }
  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-demo-action]");
    if (!button || button.disabled) return;
    const kind = button.dataset.demoAction, id = button.dataset.demoId;
    try {
      if (kind === "close") close();
      else if (kind === "settings") await openSettings();
      else if (kind === "cancel-settings") editor.close();
      else if (kind === "cancel-transfer") transfer.close();
      else if (kind === "reload-settings") await openSettings();
      else if (kind === "more") await refresh(true);
      else if (kind === "photo") onPhoto(id);
      else if (kind === "details") await onDetails(id);
      else if (kind === "recall") await onRecall(id);
      else if (kind === "rerun") onRerun(id, { library_key: context.libraryKey, lora_identity: context.identity });
      else if (["copy", "move"].includes(kind)) await chooseTransfer(kind, id);
      else if (["favorite", "retry", "pause", "resume"].includes(kind)) await action(kind, id ? [id] : []);
      else if (["delete", "regenerate"].includes(kind)) {
        if (window.confirm("Remove this Demo Reel image? A fresh random image will replace it during the next idle period. Personal copies are kept.")) await action(kind, [id]);
      } else if (kind === "slot-add") { settings.slots.push({ id: crypto.randomUUID(), title: "", prompt: "", resolutions: {} }); renderSettings(); }
      else if (kind === "slot-delete") { settings.slots = settings.slots.filter((slot) => slot.id !== id); renderSettings(); }
      else if (["slot-up", "slot-down"].includes(kind)) {
        settings.slots = moveDemoReelSlot(settings.slots, id, settings.slots.findIndex((slot) => slot.id === id) + (kind === "slot-up" ? -1 : 1)); renderSettings();
      }
    } catch (error) { notify(error.message, "error"); }
  });
  editor.addEventListener("input", (event) => {
    const input = event.target, data = input.dataset;
    if (data.demoSlotField) {
      const slot = settings.slots.find((slot) => slot.id === data.demoId);
      slot[data.demoSlotField] = input.value;
      editor.querySelector(`[data-demo-preview="${CSS.escape(slot.id)}"]`).textContent = `Preview: ${expandDemoReelPrompt(slot.prompt, context.subject || "LoRA subject")}`;
    } else if (data.demoParameter) settings.recipes[data.demoFamily].parameters[data.demoParameter] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
    else if (data.demoStrength) settings.recipes[data.demoStrength].strength = Number(input.value);
    else if (input.matches("[data-demo-paused]")) settings.paused = input.checked;
    else if (input.matches("[data-demo-rating-user]")) settings.rating_user_id = input.value;
    else if (input.matches("[data-demo-lora-strength]")) {
      settings.strengths[context.libraryKey] ||= {};
      if (input.value === "") delete settings.strengths[context.libraryKey][context.identity];
      else settings.strengths[context.libraryKey][context.identity] = Number(input.value);
    } else if (data.demoResolution) {
      const slot = settings.slots.find((slot) => slot.id === data.demoId);
      slot.resolutions ||= {}; slot.resolutions[data.demoFamily] ||= {};
      if (input.value === "") delete slot.resolutions[data.demoFamily][data.demoResolution];
      else slot.resolutions[data.demoFamily][data.demoResolution] = Number(input.value);
      if (!Object.keys(slot.resolutions[data.demoFamily]).length) delete slot.resolutions[data.demoFamily];
    }
  });
  editor.addEventListener("change", async (event) => {
    const input = event.target;
    const family = input.dataset.demoRecipeEnabled || input.dataset.demoSource;
    if (!family) return;
    try {
      if (input.matches("[data-demo-recipe-enabled]") && !input.checked) delete settings.recipes[family];
      else {
        const sourceKey = input.dataset.demoSource ? input.value : settings.libraries.find((entry) => entry.key === family).sources[0].source_key;
        await loadRecipe(sourceKey);
        settings.recipes[family] = { source_key: sourceKey, strength: settings.recipes[family]?.strength || 1, parameters: Object.fromEntries(recipeInputs(details[sourceKey]).map((control) => [control.id, structuredClone(control.default)])) };
      }
      renderSettings();
    } catch (error) { notify(error.message, "error"); }
  });
  editor.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = editor.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const payload = Object.fromEntries(["revision", "paused", "rating_user_id", "slots", "recipes", "strengths"].map((key) => [key, settings[key]]));
      await api("/api/admin/demo-reels", { method: "PUT", body: JSON.stringify(payload) });
      editor.close();
      notify("Demo Reel settings saved. Generation waits for five quiet minutes.", "success");
      await refresh();
    } catch (error) { editor.querySelector("[data-demo-settings-error]").textContent = error.message; }
    finally { button.disabled = false; }
  });
  editor.addEventListener("keydown", (event) => {
    const handle = event.target.closest("[data-demo-slot-handle]");
    if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const id = handle.dataset.demoSlotHandle;
    settings.slots = moveDemoReelSlot(settings.slots, id, settings.slots.findIndex((slot) => slot.id === id) + (event.key === "ArrowUp" ? -1 : 1));
    renderSettings(); editor.querySelector(`[data-demo-slot-handle="${CSS.escape(id)}"]`)?.focus();
  });
  editor.addEventListener("dragstart", (event) => {
    const handle = event.target.closest("[data-demo-slot-handle]");
    if (handle) { dragId = handle.dataset.demoSlotHandle; event.dataTransfer.setData("text/plain", dragId); }
  });
  editor.addEventListener("dragover", (event) => { if (dragId && event.target.closest("[data-demo-slot]")) event.preventDefault(); });
  editor.addEventListener("drop", (event) => {
    const row = event.target.closest("[data-demo-slot]");
    if (!dragId || !row) return;
    event.preventDefault(); settings.slots = moveDemoReelSlot(settings.slots, dragId, settings.slots.findIndex((slot) => slot.id === row.dataset.demoSlot)); dragId = null; renderSettings();
  });
  editor.addEventListener("dragend", () => { dragId = null; });
  transfer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = transfer.querySelector('[type="submit"]'); button.disabled = true;
    try {
      await api(`${path()}/actions`, { method: "POST", body: JSON.stringify({ action: transferDraft.kind, generation_ids: [transferDraft.id], collection_id: new FormData(event.target).get("collection_id") || null, key: transferDraft.key }) });
      transfer.close(); await refresh(); notify("Image saved to your collection.", "success");
    } catch (error) { transfer.querySelector("[data-demo-transfer-error]").textContent = error.message; }
    finally { button.disabled = false; }
  });
  return { open, close, openSettings, action, find, items, path, refresh, loadMore: () => refresh(true), hasMore: () => view?.next_offset != null, isOpen: () => gallery.open };
}
