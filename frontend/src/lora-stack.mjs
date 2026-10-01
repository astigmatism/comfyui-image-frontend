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

function allowsStrength(control, strength) {
  if (!Number.isFinite(strength) || strength < control.minimum || strength > control.maximum) return false;
  const [numerator, denominator] = decimalParts(strength);
  const [stepNumerator, stepDenominator] = decimalParts(control.step);
  return stepNumerator > 0n && (numerator * stepDenominator) % (denominator * stepNumerator) === 0n;
}

export function loraDefaultPositiveStrength(control) {
  if (allowsStrength(control, 1)) return 1;
  const step = Number(control.step);
  if (!(step > 0)) return null;
  const value = Number((Math.ceil(Math.max(step, control.minimum) / step) * step).toPrecision(12));
  return value > 0 && allowsStrength(control, value) ? value : null;
}

export function strongestLoraTrigger(control, value) {
  const entry = (value || []).reduce((best, candidate) =>
    candidate.strength > 0 && (!best || candidate.strength > best.strength) ? candidate : best, null);
  const item = control.items.find((candidate) => candidate.id === entry?.id);
  const verified = typeof item?.trigger_word === "string" ? item.trigger_word.trim() : "";
  const title = typeof item?.label === "string" ? item.label.trim() : "";
  return { entry, item, triggerWord: verified || title || null, triggerSource: verified ? "verified" : title ? "title" : null };
}

export function formatLoraStrength(value) {
  const [mantissa, exponent = "0"] = String(value).split(/[eE]/);
  const decimals = Math.max(2, (mantissa.split(".")[1]?.length || 0) - Number(exponent));
  return Number(value).toFixed(Math.min(20, decimals));
}

export function steppedLoraStrength(control, value, direction) {
  const [half, halfScale] = decimalParts(0.5);
  const [step, stepScale] = decimalParts(control.step);
  const increment = (half * stepScale) % (halfScale * step) === 0n ? 0.5 : Number(control.step);
  const minimum = Math.ceil(control.minimum / control.step) * control.step;
  const maximum = Math.floor((control.maximum + Number.EPSILON * 8) / control.step) * control.step;
  return Number(Math.max(minimum, Math.min(maximum, value + direction * increment)).toPrecision(12));
}

export function loraStackMarkup(control, value, images = {}, { editable = false, disabled = false } = {}) {
  const rows = Array.isArray(value) ? value : control.default;
  const items = new Map(control.items.map((item) => [item.id, item]));
  const active = rows.filter((entry) => entry.strength > 0);
  const summary = active.length
    ? `<ul class="lm-summary-list">${active.map((entry) => {
      const label = items.get(entry.id)?.label || entry.id;
      const image = images[entry.id];
      const url = typeof image === "string" ? image : image?.image_url;
      const strength = formatLoraStrength(entry.strength);
      const inputId = `lora-strength-${encodeURIComponent(JSON.stringify([control.id, entry.id]))}`;
      const controls = editable ? `<div class="generation-quantity lm-summary-stepper"><input id="${inputId}" class="quantity-value" type="number" data-lora-strength="${escape(entry.id)}" aria-label="${escape(label)} strength" min="${control.minimum}" max="${control.maximum}" step="${control.step}" value="${strength}" ${disabled ? "disabled" : ""} /><span class="quantity-spinner"><button type="button" class="quantity-arrow" data-lora-step="1" aria-label="Increase ${escape(label)} strength" ${disabled || steppedLoraStrength(control, entry.strength, 1) <= entry.strength ? "disabled" : ""}>▲</button><button type="button" class="quantity-arrow" data-lora-step="-1" aria-label="Decrease ${escape(label)} strength" ${disabled || steppedLoraStrength(control, entry.strength, -1) >= entry.strength ? "disabled" : ""}>▼</button></span></div><button type="button" class="icon-button lm-summary-remove" data-lora-disable aria-label="Disable ${escape(label)}" title="Disable ${escape(label)}" ${disabled ? "disabled" : ""}>×</button><span id="${inputId}-error" class="lm-summary-error" role="alert" hidden></span>` : `<span class="lm-summary-strength">${strength}</span>`;
      return `<li class="lm-summary-item${editable ? " is-editable" : ""}" data-lora-summary-id="${escape(entry.id)}"><span class="lm-summary-thumb">${url ? `<img src="${escape(url)}" alt="" />` : '<span aria-hidden="true">✧</span>'}</span><span class="lm-summary-name" title="${escape(label)}">${escape(label)}</span>${controls}</li>`;
    }).join("")}</ul>`
    : '<p class="lm-empty-summary">No LoRAs enabled.</p>';
  return `<div class="lora-stack" data-lora-control="${escape(control.id)}" ${editable ? "data-lora-editable" : ""} aria-live="polite">${summary}</div>`;
}

export function installLoraStackControls(root, { context, apply }) {
  let committing = false;
  const details = (element) => {
    const stack = element.closest("[data-lora-editable]");
    const row = element.closest("[data-lora-summary-id]");
    if (!stack || !row) return null;
    const ctx = context(stack.dataset.loraControl);
    return ctx ? { ...ctx, id: row.dataset.loraSummaryId, row, input: row.querySelector("[data-lora-strength]") } : null;
  };
  function updateArrows(ctx, strength) {
    const valid = allowsStrength(ctx.control, strength);
    for (const arrow of ctx.row.querySelectorAll("[data-lora-step]")) {
      arrow.disabled = !valid || steppedLoraStrength(ctx.control, strength, Number(arrow.dataset.loraStep)) === strength;
      if (arrow.disabled && document.activeElement === arrow) ctx.input.focus({ preventScroll: true });
    }
  }
  function clearError(input) {
    input.setCustomValidity("");
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
    const error = input.closest(".lm-summary-item").querySelector(".lm-summary-error");
    error.hidden = true;
    error.textContent = "";
  }
  function commit(element, action = "type", nextFocus = null) {
    if (committing || element.disabled) return;
    const ctx = details(element);
    if (!ctx) return;
    const { control, values, memory, id, input, row } = ctx;
    const previous = values.find((entry) => entry.id === id)?.strength;
    if (!(previous > 0)) return;
    clearError(input);
    let strength = input.value === "" ? NaN : Number(input.value);
    if (action === "disable") strength = 0;
    const candidate = (value) => values.map((entry) => entry.id === id ? { id, strength: value } : { ...entry });
    let error = loraStackError(control, candidate(strength));
    if (!error && (action === "1" || action === "-1")) strength = steppedLoraStrength(control, strength, Number(action));
    error ||= loraStackError(control, candidate(strength));
    if (error) {
      input.setCustomValidity(error);
      input.setAttribute("aria-invalid", "true");
      const message = row.querySelector(".lm-summary-error");
      input.setAttribute("aria-describedby", message.id);
      message.textContent = error;
      message.hidden = false;
      return;
    }
    if (strength === previous) { input.value = formatLoraStrength(strength); return; }
    const focused = row.contains(document.activeElement);
    const leavingForRemovedControl = nextFocus && row.contains(nextFocus);
    const position = [...row.parentElement.children].indexOf(row);
    committing = true;
    try {
      apply(control.id, candidate(strength), { ...memory, [id]: strength > 0 ? strength : previous }, ctx.sourceKey);
      // Keep the existing controls in place so blur/Tab and pointer clicks can
      // finish on their original targets. Only disabling removes a row.
      const stack = row.closest("[data-lora-editable]");
      if (strength > 0) {
        input.value = formatLoraStrength(strength);
        updateArrows(ctx, strength);
      } else {
        row.remove();
        const remaining = stack.querySelectorAll("[data-lora-strength]");
        if (!remaining.length) stack.innerHTML = '<p class="lm-empty-summary">No LoRAs enabled.</p>';
        const focusNext = () => (remaining[Math.min(position, remaining.length - 1)] || root.querySelector(`[data-lora-open][data-lora-control-id="${CSS.escape(control.id)}"]`))?.focus({ preventScroll: true });
        if (focused) focusNext();
        else if (leavingForRemovedControl) queueMicrotask(focusNext);
      }
      const status = stack.closest(".control-section")?.querySelector(".control-section-status");
      if (status) status.textContent = `${candidate(strength).filter((entry) => entry.strength > 0).length} active`;
    } finally { committing = false; }
  }
  root.addEventListener("pointerdown", (event) => {
    // A same-row action consumes the typed value directly, including zero.
    const button = event.target.closest("[data-lora-editable] [data-lora-step], [data-lora-editable] [data-lora-disable]");
    if (button && button.closest(".lm-summary-item").contains(document.activeElement)) event.preventDefault();
  });
  root.addEventListener("click", (event) => {
    const button = event.target.closest("[data-lora-editable] [data-lora-step], [data-lora-editable] [data-lora-disable]");
    if (button) commit(button, button.hasAttribute("data-lora-disable") ? "disable" : button.dataset.loraStep);
  });
  root.addEventListener("focusout", (event) => {
    if (event.target.matches("[data-lora-editable] [data-lora-strength]")) commit(event.target, "type", event.relatedTarget);
  });
  root.addEventListener("input", (event) => {
    if (event.target.matches("[data-lora-editable] [data-lora-strength]")) {
      clearError(event.target);
      const ctx = details(event.target);
      if (ctx) updateArrows(ctx, event.target.value === "" ? NaN : Number(event.target.value));
    }
  });
  root.addEventListener("keydown", (event) => {
    const input = event.target.closest("[data-lora-editable] [data-lora-strength]");
    if (!input || !["ArrowUp", "ArrowDown", "Enter", "Escape"].includes(event.key)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.key === "Escape") {
      const ctx = details(input);
      if (ctx) {
        const strength = ctx.values.find((entry) => entry.id === ctx.id).strength;
        input.value = formatLoraStrength(strength);
        updateArrows(ctx, strength);
      }
      clearError(input);
    } else commit(input, event.key === "ArrowUp" ? "1" : event.key === "ArrowDown" ? "-1" : "type");
  });
}
