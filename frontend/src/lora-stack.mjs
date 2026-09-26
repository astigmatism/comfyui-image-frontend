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
  return { entry, item, triggerWord: item?.trigger_word || null };
}

export function loraStackMarkup(control, value, images = {}) {
  const rows = Array.isArray(value) ? value : control.default;
  const items = new Map(control.items.map((item) => [item.id, item]));
  const active = rows.filter((entry) => entry.strength > 0);
  const summary = active.length
    ? `<ul class="lm-summary-list">${active.map((entry) => {
      const label = items.get(entry.id)?.label || entry.id;
      const image = images[entry.id];
      const url = typeof image === "string" ? image : image?.image_url;
      return `<li class="lm-summary-item"><span class="lm-summary-thumb">${url ? `<img src="${escape(url)}" alt="" />` : '<span aria-hidden="true">✧</span>'}</span><span class="lm-summary-name">${escape(label)}</span><span class="lm-summary-strength">${Number(entry.strength).toFixed(2)}</span></li>`;
    }).join("")}</ul>`
    : '<p class="lm-empty-summary">No LoRAs enabled.</p>';
  return `<div class="lora-stack" data-lora-control="${escape(control.id)}" aria-live="polite">${summary}</div>`;
}
