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
  const labels = new Map(control.items.map((item) => [item.id, item.label]));
  return `<div class="lora-stack" data-lora-control="${escape(control.id)}"><p class="hint">Applied from top to bottom. Strength 0 skips a LoRA. Drag to reorder, use the arrow buttons, or press ↑/↓ on a drag handle.</p><ol class="lora-list">${rows.map((entry, index) => {
    const label = labels.get(entry.id) || entry.id;
    const shared = `${errorId ? `aria-invalid="true" aria-describedby="${escape(errorId)}"` : ""} data-lora-strength min="${control.minimum}" max="${control.maximum}" step="${control.step}" value="${escape(entry.strength)}" ${disabled ? "disabled" : ""}`;
    return `<li class="lora-row" data-lora-id="${escape(entry.id)}"><button type="button" class="icon-button lora-handle" data-lora-handle draggable="${!disabled}" aria-label="Reorder ${escape(label)}" ${disabled ? "disabled" : ""}>⠿</button><span class="lora-label">${escape(label)}</span><input type="range" ${shared} aria-label="${escape(label)} strength slider" /><input type="number" ${shared} aria-label="${escape(label)} strength" /><div class="lora-movement"><button type="button" class="icon-button" data-lora-move="-1" aria-label="Move ${escape(label)} up" ${disabled || index === 0 ? "disabled" : ""}>↑</button><button type="button" class="icon-button" data-lora-move="1" aria-label="Move ${escape(label)} down" ${disabled || index === rows.length - 1 ? "disabled" : ""}>↓</button></div></li>`;
  }).join("")}</ol><span class="visually-hidden" data-lora-status role="status" aria-live="polite"></span></div>`;
}

// Delegation survives panel rerenders. Configuration stays in application state;
// DOM moves keep focus, each row's exact entry, and the slider paired by public ID.
export function installLoraControls(root, { read, write }) {
  let dragging = null;
  const context = (target) => {
    const stack = target.closest?.("[data-lora-control]");
    const row = target.closest?.("[data-lora-id]");
    return stack && row ? { stack, row, id: stack.dataset.loraControl, item: row.dataset.loraId } : null;
  };
  const move = (ctx, index, focus = null) => {
    const values = read(ctx.id);
    const next = moveLora(values, ctx.item, index);
    if (index < 0 || index >= values.length) return;
    write(ctx.id, next);
    const list = ctx.stack.querySelector("ol");
    const nodes = new Map([...list.children].map((row) => [row.dataset.loraId, row]));
    next.forEach((entry, i) => {
      const row = nodes.get(entry.id);
      list.append(row);
      row.querySelector('[data-lora-move="-1"]').disabled = i === 0;
      row.querySelector('[data-lora-move="1"]').disabled = i === next.length - 1;
    });
    (focus && !focus.disabled ? focus : ctx.row.querySelector("[data-lora-handle]")).focus();
    ctx.stack.querySelector("[data-lora-status]").textContent = `${ctx.row.querySelector(".lora-label").textContent} moved to position ${index + 1} of ${next.length}.`;
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
  root.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-lora-move]");
    const ctx = context(button || event.target);
    if (!button || !ctx || button.disabled) return;
    event.stopPropagation();
    move(ctx, read(ctx.id).findIndex((entry) => entry.id === ctx.item) + Number(button.dataset.loraMove), button);
  }, true);
  root.addEventListener("keydown", (event) => {
    if (!event.target.matches("[data-lora-handle]") || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    const ctx = context(event.target);
    if (!ctx || event.target.disabled) return;
    event.preventDefault(); event.stopPropagation();
    move(ctx, read(ctx.id).findIndex((entry) => entry.id === ctx.item) + (event.key === "ArrowUp" ? -1 : 1));
  }, true);
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
