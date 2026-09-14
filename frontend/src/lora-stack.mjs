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

export function loraStackMarkup(control, value, disabled = false, errorId = null) {
  const rows = Array.isArray(value) ? value : control.default;
  const items = new Map(control.items.map((item) => [item.id, item]));
  return `<div class="lora-stack" data-lora-control="${escape(control.id)}"><p class="hint">Applied top to bottom. Strength 0 skips a LoRA.</p><ol class="lora-list">${rows.map((entry) => {
    const item = items.get(entry.id);
    const label = item?.label || entry.id;
    const usage = item?.description || "Use: trigger words have not been verified for this LoRA. The strength control loads it; no <lora:...> tag is needed.";
    const tooltipId = `lora-usage-${control.id}-${entry.id}`;
    const shared = `${errorId ? `aria-invalid="true" aria-describedby="${escape(errorId)}"` : ""} data-lora-strength min="${control.minimum}" max="${control.maximum}" step="${control.step}" value="${escape(entry.strength)}" ${disabled ? "disabled" : ""}`;
    return `<li class="lora-row" data-lora-id="${escape(entry.id)}"><button type="button" class="icon-button lora-handle" data-lora-handle draggable="${!disabled}" aria-label="Reorder ${escape(label)}" aria-description="Drag to reorder, or use the Up and Down arrow keys." ${disabled ? "disabled" : ""}>⠿</button><span class="lora-label"><button type="button" class="lora-title" aria-describedby="${escape(tooltipId)}">${escape(label)}</button><span id="${escape(tooltipId)}" class="activity-tooltip lora-tooltip" role="tooltip" popover="manual">${escape(usage)}</span></span><input type="range" ${shared} aria-label="${escape(label)} strength slider" /><input type="number" ${shared} aria-label="${escape(label)} strength" /></li>`;
  }).join("")}</ol><span class="visually-hidden" data-lora-status role="status" aria-live="polite"></span></div>`;
}

// Delegation survives panel rerenders. Configuration stays in application state;
// DOM moves keep focus, each row's exact entry, and the slider paired by public ID.
export function installLoraControls(root, { read, write }) {
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
  const move = (ctx, index) => {
    const values = read(ctx.id);
    const next = moveLora(values, ctx.item, index);
    if (index < 0 || index >= values.length) return;
    write(ctx.id, next);
    const list = ctx.stack.querySelector("ol");
    const nodes = new Map([...list.children].map((row) => [row.dataset.loraId, row]));
    next.forEach((entry) => list.append(nodes.get(entry.id)));
    ctx.row.querySelector("[data-lora-handle]").focus();
    ctx.stack.querySelector("[data-lora-status]").textContent = `${ctx.row.querySelector(".lora-title").textContent} moved to position ${index + 1} of ${next.length}.`;
  };
  for (const type of ["input", "change"]) root.addEventListener(type, (event) => {
    if (!event.target.matches("[data-lora-strength]")) return;
    const ctx = context(event.target);
    if (!ctx || event.target.disabled) return;
    event.stopPropagation();
    const strength = event.target.value === "" ? null : Number(event.target.value);
    write(ctx.id, read(ctx.id).map((entry) => entry.id === ctx.item ? { ...entry, strength } : entry));
    const sibling = ctx.row.querySelector(event.target.type === "range" ? 'input[type="number"]' : 'input[type="range"]');
    if (event.target.type === "range" || event.target.validity.valid && strength !== null) sibling.value = event.target.value;
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
