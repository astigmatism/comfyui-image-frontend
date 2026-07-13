export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function scaleToLayout(value) {
  const normalized = Math.max(0, Math.min(100, Number(value) || 0));
  if (normalized >= 96) return { full: true, cardWidth: 1200 };
  const cardWidth = Math.round(170 + Math.pow(normalized / 100, 1.6) * 850);
  return { full: false, cardWidth };
}

export function statusLabel(status) {
  const labels = {
    queued: "Queued",
    dispatching: "Preparing",
    running: "Running",
    cancel_requested: "Stopping…",
    succeeded: "Complete",
    cancelled_with_artifacts: "Cancelled generation · best available",
    cancelled_without_artifacts: "Cancelled generation",
    failed_with_artifacts: "Failed · best available",
    failed_without_artifacts: "Failed",
    interrupted: "Interrupted",
  };
  return labels[status] || status;
}

export function isTerminal(status) {
  return [
    "succeeded",
    "cancelled_with_artifacts",
    "cancelled_without_artifacts",
    "failed_with_artifacts",
    "failed_without_artifacts",
    "interrupted",
  ].includes(status);
}

export function formatLocalDate(value, locale = undefined) {
  const date = new Date(value);
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

export function footerText(source, date, locale = undefined) {
  return `${source} · ${formatLocalDate(date, locale)}`;
}

export function evaluateWhen(when, values) {
  const current = values?.[when?.control];
  const expected = when?.value;
  switch (when?.operator || "equals") {
    case "equals":
    case "eq":
      return current === expected;
    case "not_equals":
    case "ne":
      return current !== expected;
    case "in":
      return Array.isArray(expected) && expected.includes(current);
    case "not_in":
      return Array.isArray(expected) && !expected.includes(current);
    case "truthy":
      return Boolean(current);
    case "falsy":
      return !current;
    case "gt":
      return current > expected;
    case "gte":
      return current >= expected;
    case "lt":
      return current < expected;
    case "lte":
      return current <= expected;
    default:
      return false;
  }
}

export function controlPresentation(control, values, capabilityStates = {}) {
  let visible = true;
  let enabled = true;
  let required = Boolean(control.required);
  let forbidden = false;
  if (control.available === false) enabled = false;
  if (control.capability && capabilityStates[control.capability]?.available === false) {
    enabled = false;
  }
  for (const condition of control.conditions || []) {
    if (!evaluateWhen(condition.when, values)) continue;
    switch (condition.effect) {
      case "visible":
        visible = true;
        break;
      case "hidden":
        visible = false;
        break;
      case "enabled":
        enabled = true;
        break;
      case "disabled":
        enabled = false;
        break;
      case "required":
        required = true;
        break;
      case "forbidden":
        forbidden = true;
        enabled = false;
        break;
    }
  }
  return {
    visible,
    enabled,
    required,
    forbidden,
    reason:
      control.available === false
        ? control.unavailable_reason || "This control is unavailable."
        : control.capability && capabilityStates[control.capability]?.available === false
          ? capabilityStates[control.capability]?.reason || "Capability unavailable."
          : null,
  };
}

export function defaultsForContract(contract) {
  const values = {};
  for (const control of contract?.controls || []) {
    if (Object.hasOwn(control, "default")) values[control.id] = structuredClone(control.default);
  }
  return values;
}

export function overwriteWithRecall(current, recall) {
  return {
    ...current,
    activeProfileId: recall.profile_id,
    controls: structuredClone(recall.controls || {}),
    recallIdentity: structuredClone(recall.identity || null),
    compositionId: null,
    promptAssistant: {
      ...(current.promptAssistant || {}),
      mode: recall.prompt_assistant?.mode || "refine",
      creativeDirection: recall.prompt_assistant?.creative_direction || "",
      historicalModel: recall.prompt_assistant?.model || null,
    },
    fieldErrors: {},
    formError: null,
  };
}

export function normalizeInputValue(control, raw) {
  switch (control.type) {
    case "integer":
      return raw === "" ? null : Number.parseInt(raw, 10);
    case "number":
      return raw === "" ? null : Number.parseFloat(raw);
    case "boolean":
      return Boolean(raw);
    default:
      return raw;
  }
}

export function resolutionConstraints(control) {
  const constraints = control?.constraints || {};
  const multiple = Number(constraints.multiple || constraints.step || 1);
  return {
    minimumWidth: constraints.width?.minimum ?? constraints.minimum_width ?? constraints.minimum ?? null,
    maximumWidth: constraints.width?.maximum ?? constraints.maximum_width ?? constraints.maximum ?? null,
    minimumHeight: constraints.height?.minimum ?? constraints.minimum_height ?? constraints.minimum ?? null,
    maximumHeight: constraints.height?.maximum ?? constraints.maximum_height ?? constraints.maximum ?? null,
    widthStep: constraints.width?.step ?? multiple,
    heightStep: constraints.height?.step ?? multiple,
    multiple: Number.isFinite(multiple) && multiple > 0 ? multiple : 1,
    maximumPixels: constraints.maximum_pixels ?? null,
  };
}

export function resolutionGridConstraints(control) {
  const limits = resolutionConstraints(control);
  const maximumWidth = Number(limits.maximumWidth);
  const maximumHeight = Number(limits.maximumHeight);
  return {
    minimumWidth: 0,
    maximumWidth: Number.isFinite(maximumWidth) && maximumWidth > 0 ? maximumWidth : 2048,
    minimumHeight: 0,
    maximumHeight: Number.isFinite(maximumHeight) && maximumHeight > 0 ? maximumHeight : 2048,
    widthStep: 64,
    heightStep: 64,
  };
}

export function snapResolutionValue(value, minimum, maximum, step = 64) {
  const safeMinimum = Number.isFinite(Number(minimum)) ? Number(minimum) : 0;
  const safeMaximum = Number.isFinite(Number(maximum)) ? Number(maximum) : 2048;
  const safeStep = Number.isFinite(Number(step)) && Number(step) > 0 ? Number(step) : 64;
  const numeric = Number.isFinite(Number(value)) ? Number(value) : safeMinimum;
  const snapped = safeMinimum + Math.round((numeric - safeMinimum) / safeStep) * safeStep;
  return Math.max(safeMinimum, Math.min(safeMaximum, snapped));
}

export function resolutionSummary(width, height) {
  const safeWidth = Math.max(0, Math.round(Number(width) || 0));
  const safeHeight = Math.max(0, Math.round(Number(height) || 0));
  const megapixels = ((safeWidth * safeHeight) / 1_000_000).toFixed(2);
  const divisor = greatestCommonDivisor(safeWidth, safeHeight);
  const aspectRatio = safeWidth > 0 && safeHeight > 0 ? `${safeWidth / divisor}:${safeHeight / divisor}` : "—";
  return {
    width: safeWidth,
    height: safeHeight,
    megapixels,
    aspectRatio,
    text: `${safeWidth} × ${safeHeight} · ${megapixels} MP · ${aspectRatio}`,
  };
}

function greatestCommonDivisor(first, second) {
  let a = Math.abs(Math.round(Number(first))) || 1;
  let b = Math.abs(Math.round(Number(second))) || 1;
  while (b !== 0) {
    const remainder = a % b;
    a = b;
    b = remainder;
  }
  return a;
}

export function clientValidate(contract, values) {
  const errors = {};
  const capabilities = contract?.capability_states || {};
  for (const control of contract?.controls || []) {
    const presentation = controlPresentation(control, values, capabilities);
    if (!presentation.visible || !presentation.enabled || presentation.forbidden) continue;
    const value = values[control.id];
    if (presentation.required && (value === undefined || value === null || value === "")) {
      errors[control.id] = "Required.";
      continue;
    }
    if (value === undefined || value === null || value === "") continue;
    const constraints = control.constraints || {};
    if (["integer", "number"].includes(control.type)) {
      if (!Number.isFinite(value)) errors[control.id] = "Enter a valid number.";
      if (constraints.minimum !== undefined && value < constraints.minimum)
        errors[control.id] = `Minimum ${constraints.minimum}.`;
      if (constraints.maximum !== undefined && value > constraints.maximum)
        errors[control.id] = `Maximum ${constraints.maximum}.`;
    }
    if (["string", "multiline_string"].includes(control.type)) {
      if (constraints.minimum_length && value.length < constraints.minimum_length)
        errors[control.id] = `Use at least ${constraints.minimum_length} characters.`;
      if (constraints.maximum_length && value.length > constraints.maximum_length)
        errors[control.id] = `Use at most ${constraints.maximum_length} characters.`;
    }
    if (control.type === "resolution") {
      const width = Number(value?.width);
      const height = Number(value?.height);
      if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
        errors[control.id] = "Width and height are required.";
        continue;
      }
      const limits = resolutionConstraints(control);
      if (limits.minimumWidth !== null && width < limits.minimumWidth)
        errors[control.id] = `Width must be at least ${limits.minimumWidth}.`;
      else if (limits.maximumWidth !== null && width > limits.maximumWidth)
        errors[control.id] = `Width must be at most ${limits.maximumWidth}.`;
      else if (limits.minimumHeight !== null && height < limits.minimumHeight)
        errors[control.id] = `Height must be at least ${limits.minimumHeight}.`;
      else if (limits.maximumHeight !== null && height > limits.maximumHeight)
        errors[control.id] = `Height must be at most ${limits.maximumHeight}.`;
      else if (width % limits.multiple || height % limits.multiple)
        errors[control.id] = `Width and height must be multiples of ${limits.multiple}.`;
      else if (limits.maximumPixels !== null && width * height > limits.maximumPixels)
        errors[control.id] = `Resolution exceeds ${limits.maximumPixels.toLocaleString()} pixels.`;
    }
  }
  return errors;
}
