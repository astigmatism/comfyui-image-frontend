const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

function decimalParts(value) {
  const [mantissa, exponent = "0"] = String(value).split(/[eE]/);
  const [whole, fraction = ""] = mantissa.split(".");
  const scale = fraction.length - Number(exponent);
  return scale >= 0
    ? [BigInt(whole + fraction), 10n ** BigInt(scale)]
    : [BigInt(whole + fraction) * 10n ** BigInt(-scale), 1n];
}

export function loraStackError(control, value) {
  if (!Array.isArray(value) || value.length !== control.items.length) return "Include every LoRA exactly once.";
  const ids = new Set(control.items.map((item) => item.id));
  for (const entry of value) {
    if (!entry || Object.keys(entry).sort().join() !== "id,strength" || !ids.delete(entry.id)) return "Include every LoRA exactly once.";
    const strength = entry.strength;
    if (typeof strength !== "number" || !Number.isFinite(strength)) return "Enter a finite LoRA strength.";
    if (strength < control.minimum || strength > control.maximum) return `LoRA strength must be from ${control.minimum} to ${control.maximum}.`;
    const [numerator, denominator] = decimalParts(strength);
    const [stepNumerator, stepDenominator] = decimalParts(control.step);
    if ((numerator * stepDenominator) % (denominator * stepNumerator) !== 0n) return `Use LoRA strength increments of ${control.step}.`;
  }
  return null;
}

export function moveLora(value, id, targetIndex) {
  const result = structuredClone(value);
  const from = result.findIndex((entry) => entry.id === id);
  if (from < 0 || targetIndex < 0 || targetIndex >= result.length) return result;
  result.splice(targetIndex, 0, result.splice(from, 1)[0]);
  return result;
}

export function soloLora(value, id) {
  return value.map((entry) => ({ ...entry, strength: entry.id === id ? 1 : 0 }));
}

function allowsStrength(control, strength) {
  if (!Number.isFinite(strength) || strength < control.minimum || strength > control.maximum) return false;
  const [numerator, denominator] = decimalParts(strength);
  const [stepNumerator, stepDenominator] = decimalParts(control.step);
  return stepNumerator > 0n && (numerator * stepDenominator) % (denominator * stepNumerator) === 0n;
}

const openMixers = new Set();

export function loraStackMarkup(control, value, disabled = false, errorId = null, mixerOpen = openMixers.has(control.id)) {
  const rows = Array.isArray(value) ? value : control.default;
  const items = new Map(control.items.map((item) => [item.id, item]));
  const active = rows.filter((entry) => entry.strength > 0);
  const quickAllowed = allowsStrength(control, 1);
  const onStrength = quickAllowed ? 1 : allowsStrength(control, control.step) ? control.step : null;
  const quickPicks = rows.map((entry) => {
    const item = items.get(entry.id);
    const trigger = item?.trigger_word;
    const label = trigger || item?.label || entry.id;
    const picked = active.length === 1 && entry.strength === 1;
    const description = !quickAllowed ? "This workflow does not allow strength 1; use Mix & adjust." : trigger ? "Fills Subject name when a prompt source is available." : "Trigger word not published; Subject name stays unchanged.";
    return `<button id="lora-solo-${escape(control.id)}-${escape(entry.id)}" type="button" class="lora-quick-pick ${entry.strength > 0 ? "is-active" : ""}" data-lora-solo="${escape(entry.id)}" data-lora-trigger-word="${escape(trigger || "")}" aria-label="Use only ${escape(label)} at strength 1" aria-description="${description}" aria-pressed="${picked}" ${disabled || !quickAllowed ? "disabled" : ""}><span class="lora-quick-copy"><span>${escape(label)}</span><small>${trigger ? "fills Subject" : "trigger unverified"}</small></span><span class="lora-quick-check" aria-hidden="true">${entry.strength > 0 ? "✓" : ""}</span></button>`;
  }).join("");
  const selected = active[0] || rows[0];
  const selectedLabel = items.get(selected?.id)?.trigger_word || items.get(selected?.id)?.label || selected?.id || "LoRA";
  const quickStrength = `<span class="lora-active-count" data-lora-active-count ${active.length === 1 ? "hidden" : ""}>${active.length} active</span><label class="lora-quick-strength" ${active.length !== 1 ? "hidden" : ""}>Strength <input id="lora-quick-strength-${escape(control.id)}" type="number" data-lora-quick-strength="${escape(selected?.id || "")}" data-lora-strength min="${control.minimum}" max="${control.maximum}" step="${control.step}" value="${escape(selected?.strength || 0)}" aria-label="${escape(selectedLabel)} strength" ${errorId ? `aria-invalid="true" aria-describedby="${escape(errorId)}"` : ""} ${disabled ? "disabled" : ""} /></label>`;
  const mixer = rows.map((entry) => {
    const item = items.get(entry.id);
    const label = item?.trigger_word || item?.label || entry.id;
    const usage = item?.description || "Use: trigger words have not been verified for this LoRA. The strength control loads it; no <lora:...> tag is needed.";
    const tooltipId = `lora-usage-${control.id}-${entry.id}`;
    const shared = `${errorId ? `aria-invalid="true" aria-describedby="${escape(errorId)}"` : ""} data-lora-strength min="${control.minimum}" max="${control.maximum}" step="${control.step}" value="${escape(entry.strength)}" ${disabled ? "disabled" : ""}`;
    return `<li class="lora-row ${entry.strength > 0 ? "is-active" : ""}" data-lora-id="${escape(entry.id)}"><button id="lora-handle-${escape(control.id)}-${escape(entry.id)}" type="button" class="icon-button lora-handle" data-lora-handle draggable="${!disabled}" aria-label="Reorder ${escape(label)}" aria-description="Drag to reorder, or use the Up and Down arrow keys." ${disabled ? "disabled" : ""}>⠿</button><label class="lora-enable-hit"><input id="lora-enable-${escape(control.id)}-${escape(entry.id)}" type="checkbox" class="lora-enabled" data-lora-enabled data-lora-enable-strength="${escape(onStrength ?? "")}" aria-label="Enable ${escape(label)}" ${entry.strength > 0 ? "checked" : ""} ${disabled || onStrength === null ? "disabled" : ""} /></label><span class="lora-label"><button type="button" class="lora-title" aria-describedby="${escape(tooltipId)}">${escape(label)}</button><span id="${escape(tooltipId)}" class="activity-tooltip lora-tooltip" role="tooltip" popover="manual">${escape(usage)}</span></span><input id="lora-range-${escape(control.id)}-${escape(entry.id)}" type="range" ${shared} aria-label="${escape(label)} strength slider" ${entry.strength > 0 ? "" : "hidden"} /><input id="lora-number-${escape(control.id)}-${escape(entry.id)}" type="number" ${shared} aria-label="${escape(label)} strength" /></li>`;
  }).join("");
  return `<div class="lora-stack" data-lora-control="${escape(control.id)}"><p class="lora-quick-hint">${quickAllowed ? "Quick pick · one LoRA at 1.0" : "Strength 1.0 unavailable · use Mix & adjust"}</p><div class="lora-quick-grid">${quickPicks}</div><div class="lora-quick-footer">${mixerOpen ? `<span class="lora-active-count" data-lora-active-count>${active.length} active</span>` : quickStrength}<div class="lora-quick-actions"><button id="lora-clear-${escape(control.id)}" type="button" class="lora-clear" data-lora-clear ${disabled || !active.length ? "disabled" : ""}>All off</button><button id="lora-mixer-${escape(control.id)}" type="button" class="lora-mixer-toggle" data-lora-mixer aria-expanded="${mixerOpen}" ${disabled ? "disabled" : ""}>${mixerOpen ? "Close mixer −" : "Mix & adjust ＋"}</button></div></div>${mixerOpen ? `<div class="lora-mixer"><p class="hint">Applied top to bottom. Strength 0 skips a LoRA.</p><ol class="lora-list">${mixer}</ol></div>` : ""}<span class="visually-hidden" data-lora-status role="status" aria-live="polite"></span></div>`;
}

// Delegation survives panel rerenders. Configuration stays in application state;
// DOM moves keep focus, each row's exact entry, and the slider paired by public ID.
export function installLoraControls(root, { read, write, refresh = () => {}, onSolo = () => false }) {
  let dragging = null;
  let touchDrag = null;
  let tooltipLabel = null;
  let tooltipTimer = null;
  const hideTooltip = () => {
    clearTimeout(tooltipTimer);
    const tooltip = tooltipLabel?.querySelector(".lora-tooltip");
    if (tooltip?.matches(":popover-open")) tooltip.hidePopover();
    tooltipLabel = null;
  };
  const showTooltip = (label) => {
    if (!label) return;
    clearTimeout(tooltipTimer);
    if (label === tooltipLabel) return;
    hideTooltip();
    tooltipLabel = label;
    const tooltip = label.querySelector(".lora-tooltip");
    tooltip.showPopover();
    const anchor = label.querySelector(".lora-title").getBoundingClientRect();
    const bounds = tooltip.getBoundingClientRect();
    tooltip.style.left = `${Math.max(12, Math.min(anchor.left, innerWidth - bounds.width - 12))}px`;
    tooltip.style.top = `${Math.max(12, anchor.bottom + bounds.height + 9 <= innerHeight - 12 ? anchor.bottom + 9 : anchor.top - bounds.height - 9)}px`;
  };
  const context = (target) => {
    const stack = target?.closest?.("[data-lora-control]");
    const row = target?.closest?.("[data-lora-id]");
    return stack && row ? { stack, row, id: stack.dataset.loraControl, item: row.dataset.loraId } : null;
  };
  const contextForAction = (target) => {
    const stack = target?.closest?.("[data-lora-control]");
    return stack ? { stack, id: stack.dataset.loraControl } : null;
  };
  const announce = (id, message) => {
    const status = root.querySelector(`[data-lora-control="${CSS.escape(id)}"] [data-lora-status]`);
    if (status) status.textContent = message;
  };
  const syncLocal = (stack, values, source, settled) => {
    const active = values.filter((entry) => entry.strength > 0);
    const activeIds = new Set(active.map((entry) => entry.id));
    for (const pick of stack.querySelectorAll("[data-lora-solo]")) {
      const enabled = activeIds.has(pick.dataset.loraSolo);
      pick.classList.toggle("is-active", enabled);
      pick.setAttribute("aria-pressed", String(active.length === 1 && active[0].id === pick.dataset.loraSolo && active[0].strength === 1));
      pick.querySelector(".lora-quick-check").textContent = enabled ? "✓" : "";
    }
    for (const count of stack.querySelectorAll("[data-lora-active-count]")) {
      count.textContent = `${active.length} active`;
      if (stack.querySelector(".lora-quick-strength")) count.hidden = active.length === 1 || (source?.matches("[data-lora-quick-strength]") && !settled);
    }
    const quickLabel = stack.querySelector(".lora-quick-strength");
    const quickInput = quickLabel?.querySelector("input");
    if (quickInput) {
      quickLabel.hidden = active.length !== 1 && !(source === quickInput && !settled);
      if (source !== quickInput || settled && active.length === 1) {
        const selected = active[0] || values[0];
        const pick = [...stack.querySelectorAll("[data-lora-solo]")].find((button) => button.dataset.loraSolo === selected?.id);
        quickInput.dataset.loraQuickStrength = selected?.id || "";
        quickInput.setAttribute("aria-label", `${pick?.querySelector(".lora-quick-copy > span")?.textContent || selected?.id || "LoRA"} strength`);
        quickInput.value = selected?.strength ?? 0;
      }
    }
    for (const row of stack.querySelectorAll("[data-lora-id]")) {
      const entry = values.find((candidate) => candidate.id === row.dataset.loraId);
      const enabled = Boolean(entry?.strength > 0);
      row.classList.toggle("is-active", enabled);
      row.querySelector("[data-lora-enabled]").checked = enabled;
      const range = row.querySelector('input[type="range"]');
      if (range) {
        range.hidden = !enabled && !(range === source && !settled);
        if (range !== source && Number.isFinite(entry?.strength)) range.value = entry.strength;
      }
    }
    stack.querySelector("[data-lora-clear]").disabled = active.length === 0;
  };
  const move = (ctx, index) => {
    const values = read(ctx.id);
    const next = moveLora(values, ctx.item, index);
    if (index < 0 || index >= values.length) return;
    write(ctx.id, next);
    const list = ctx.stack.querySelector("ol");
    const nodes = new Map([...list.children].map((row) => [row.dataset.loraId, row]));
    next.forEach((entry) => list.append(nodes.get(entry.id)));
    ctx.row.querySelector("[data-lora-handle]").focus();
    refresh();
    announce(ctx.id, `${ctx.row.querySelector(".lora-title").textContent} moved to position ${index + 1} of ${next.length}.`);
  };
  for (const type of ["input", "change"]) root.addEventListener(type, (event) => {
    if (!event.target.matches("[data-lora-strength]")) return;
    const ctx = context(event.target) || (event.target.matches("[data-lora-quick-strength]")
      ? { ...contextForAction(event.target), item: event.target.dataset.loraQuickStrength }
      : null);
    if (!ctx || event.target.disabled) return;
    event.stopPropagation();
    const strength = event.target.value === "" ? null : Number(event.target.value);
    write(ctx.id, read(ctx.id).map((entry) => entry.id === ctx.item ? { ...entry, strength } : entry));
    const sibling = ctx.row?.querySelector(event.target.type === "range" ? 'input[type="number"]' : 'input[type="range"]');
    if (sibling && (event.target.type === "range" || event.target.validity.valid && strength !== null)) sibling.value = event.target.value;
    syncLocal(ctx.stack, read(ctx.id), event.target, type === "change");
  }, true);
  root.addEventListener("change", (event) => {
    if (!event.target.matches("[data-lora-enabled]")) return;
    const ctx = context(event.target);
    if (!ctx || event.target.disabled) return;
    event.stopPropagation();
    write(ctx.id, read(ctx.id).map((entry) => entry.id === ctx.item ? { ...entry, strength: event.target.checked ? Number(event.target.dataset.loraEnableStrength) : 0 } : entry));
    refresh();
  }, true);
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && tooltipLabel) {
      event.preventDefault(); event.stopPropagation();
      hideTooltip();
      return;
    }
    if (!event.target.matches("[data-lora-handle]") || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    const ctx = context(event.target);
    if (!ctx || event.target.disabled) return;
    event.preventDefault(); event.stopPropagation();
    move(ctx, read(ctx.id).findIndex((entry) => entry.id === ctx.item) + (event.key === "ArrowUp" ? -1 : 1));
  }, true);
  root.addEventListener("pointerover", (event) => {
    showTooltip(event.target.closest?.(".lora-label"));
  });
  root.addEventListener("pointerout", (event) => {
    if (tooltipLabel && !tooltipLabel.contains(event.relatedTarget)) {
      tooltipTimer = setTimeout(hideTooltip, 150);
    }
  });
  root.addEventListener("focusin", (event) => {
    const label = event.target.closest?.(".lora-label");
    if (label) showTooltip(label);
    else hideTooltip();
  });
  root.addEventListener("focusout", (event) => {
    if (tooltipLabel?.contains(event.target) && !tooltipLabel.contains(event.relatedTarget)) hideTooltip();
  });
  root.addEventListener("click", (event) => {
    const solo = event.target.closest?.("[data-lora-solo]");
    if (solo && !solo.disabled) {
      const ctx = contextForAction(solo);
      if (!ctx) return;
      const id = solo.dataset.loraSolo;
      write(ctx.id, soloLora(read(ctx.id), id));
      const trigger = solo.dataset.loraTriggerWord;
      const subjectSet = trigger ? onSolo(trigger) : false;
      refresh();
      const label = solo.querySelector(".lora-quick-copy > span")?.textContent || id;
      const result = subjectSet ? "Subject name updated." : trigger ? "Subject name is unavailable, so it was unchanged." : "No verified trigger word is published, so Subject name is unchanged.";
      announce(ctx.id, `${label} set to 1. Other LoRAs off. ${result}`);
      return;
    }
    const mixer = event.target.closest?.("[data-lora-mixer]");
    if (mixer && !mixer.disabled) {
      const ctx = contextForAction(mixer);
      if (!ctx) return;
      if (openMixers.has(ctx.id)) openMixers.delete(ctx.id);
      else openMixers.add(ctx.id);
      refresh();
      return;
    }
    const clear = event.target.closest?.("[data-lora-clear]");
    if (clear && !clear.disabled) {
      const ctx = contextForAction(clear);
      if (!ctx) return;
      write(ctx.id, read(ctx.id).map((entry) => ({ ...entry, strength: 0 })));
      refresh();
      root.querySelector(`[data-lora-control="${CSS.escape(ctx.id)}"] [data-lora-mixer]`)?.focus({ preventScroll: true });
      announce(ctx.id, "All LoRAs off.");
      return;
    }
    if (event.target.matches(".lora-title")) showTooltip(event.target.closest(".lora-label"));
  });
  root.addEventListener("pointerdown", (event) => {
    if (!tooltipLabel?.contains(event.target)) hideTooltip();
  });
  root.addEventListener("scroll", hideTooltip, true);
  window.addEventListener("resize", hideTooltip);
  // Native HTML dragging does not reorder on touch screens. Capture only a
  // non-mouse handle gesture so the rest of the panel still scrolls normally.
  root.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" || !event.target.matches("[data-lora-handle]") || event.target.disabled) return;
    const ctx = context(event.target);
    if (!ctx) return;
    event.preventDefault();
    event.target.setPointerCapture(event.pointerId);
    touchDrag = { ...ctx, pointerId: event.pointerId, handle: event.target, target: null };
    ctx.row.classList.add("is-dragging");
  });
  root.addEventListener("pointermove", (event) => {
    if (!touchDrag || touchDrag.pointerId !== event.pointerId) return;
    const ctx = context(document.elementFromPoint(event.clientX, event.clientY));
    touchDrag.target?.classList.remove("lora-drop-target");
    touchDrag.target = ctx?.stack === touchDrag.stack ? ctx.row : null;
    touchDrag.target?.classList.add("lora-drop-target");
  });
  const endTouchDrag = (event) => {
    if (!touchDrag || touchDrag.pointerId !== event.pointerId) return;
    const active = touchDrag;
    touchDrag = null;
    active.row.classList.remove("is-dragging");
    active.target?.classList.remove("lora-drop-target");
    if (event.type === "pointerup" && active.target) {
      move(active, read(active.id).findIndex((entry) => entry.id === active.target.dataset.loraId));
    }
    if (active.handle.hasPointerCapture(event.pointerId)) active.handle.releasePointerCapture(event.pointerId);
  };
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) root.addEventListener(type, endTouchDrag);
  root.addEventListener("dragstart", (event) => {
    if (!event.target.matches("[data-lora-handle]")) return;
    dragging = context(event.target);
    if (!dragging || event.target.disabled) { event.preventDefault(); return; }
    event.stopPropagation();
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", dragging.item);
  }, true);
  root.addEventListener("dragover", (event) => {
    const ctx = context(event.target);
    if (!dragging || ctx?.stack !== dragging.stack) return;
    event.preventDefault(); event.stopPropagation();
    event.dataTransfer.dropEffect = "move";
  }, true);
  root.addEventListener("drop", (event) => {
    const ctx = context(event.target);
    if (!dragging || ctx?.stack !== dragging.stack) return;
    event.preventDefault(); event.stopPropagation();
    move(dragging, read(ctx.id).findIndex((entry) => entry.id === ctx.item));
    dragging = null;
  }, true);
  root.addEventListener("dragend", () => { dragging = null; }, true);
}
