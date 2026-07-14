export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function createLatestRequestGate() {
  let sequence = 0;
  const latest = new Map();
  return {
    issue(key) {
      const token = ++sequence;
      latest.set(key, token);
      return token;
    },
    isCurrent(key, token) {
      return latest.get(key) === token;
    },
    invalidate(key) {
      latest.set(key, ++sequence);
    },
    clear() {
      latest.clear();
    },
  };
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

export function interfaceInputs(contract) {
  const inputs = contract?.inputs || contract?.controls || [];
  return Array.isArray(inputs) ? inputs.filter((item) => item && typeof item === "object") : [];
}

export function isAdvancedInput(input) {
  return input?.advanced === true || input?.tier === "advanced";
}

export function sortInterfaceInputs(inputs) {
  return [...(inputs || [])].sort((first, second) => {
    const tier = Number(isAdvancedInput(first)) - Number(isAdvancedInput(second));
    if (tier) return tier;
    const firstOrder = Number.isFinite(Number(first?.order)) ? Number(first.order) : Number.MAX_SAFE_INTEGER;
    const secondOrder = Number.isFinite(Number(second?.order)) ? Number(second.order) : Number.MAX_SAFE_INTEGER;
    if (firstOrder !== secondOrder) return firstOrder - secondOrder;
    const group = compareText(String(first?.group || ""), String(second?.group || ""));
    if (group) return group;
    return compareText(String(first?.id || ""), String(second?.id || ""));
  });
}

function compareText(first, second) {
  if (first === second) return 0;
  return first < second ? -1 : 1;
}

export function positivePromptInput(contract) {
  return interfaceInputs(contract).find((input) => input.semantic_role === "positive_prompt") || null;
}

function inputConstraint(input, name) {
  return input?.[name] ?? input?.constraints?.[name];
}

function fixedSeedFallback(input) {
  const declaredDefault = input?.default;
  if (declaredDefault !== undefined && declaredDefault !== null && declaredDefault !== "random") {
    return String(declaredDefault);
  }
  return String(inputConstraint(input, "minimum") ?? 0);
}

export function seedAllowsRandom(input) {
  if (input?.default_mode === "random") return true;
  if (input?.default_mode === "fixed") return false;
  return input?.default === undefined || input?.default === null || input?.default === "random";
}

export function seedFormValue(input, value = undefined) {
  const fallback = fixedSeedFallback(input);
  const allowsRandom = seedAllowsRandom(input);
  if (value && typeof value === "object") {
    const requestedFixed = value.mode === "fixed";
    return {
      mode: allowsRandom && !requestedFixed ? "random" : "fixed",
      value: String(!allowsRandom && !requestedFixed ? fallback : (value.value ?? fallback)),
    };
  }
  if (!allowsRandom && (value === undefined || value === null || value === "" || value === "random")) {
    return { mode: "fixed", value: fallback };
  }
  const random =
    allowsRandom &&
    (value === "random" || value === null || value === "" || value === undefined);
  return { mode: random ? "random" : "fixed", value: random ? fallback : String(value) };
}

export function defaultsForInterface(contract) {
  const values = {};
  for (const input of interfaceInputs(contract)) {
    if (input.type === "seed") {
      values[input.id] = seedFormValue(input);
    } else if (Object.hasOwn(input, "default")) {
      values[input.id] = structuredClone(input.default);
    }
  }
  return applyChoiceStrengthDefaults(contract, values);
}

export function defaultsForContract(contract) {
  return defaultsForInterface(contract);
}

export function choiceOptions(input) {
  return Array.isArray(input?.choices)
    ? input.choices.filter((option) => option && typeof option === "object")
    : [];
}

export function choiceStrengthCompanion(contract, choice) {
  const inputs = interfaceInputs(contract);
  const choices = inputs.filter((input) => input.type === "choice");
  const numbers = inputs.filter((input) => input.type === "number");
  const exactByChoice = new Map();
  for (const candidate of choices) {
    const exact = numbers.find(
      (input) =>
        input.id === `${candidate.id}_strength` &&
        input.semantic_role === candidate.semantic_role,
    );
    if (exact) exactByChoice.set(candidate.id, exact);
  }
  if (exactByChoice.has(choice?.id)) return exactByChoice.get(choice.id);
  if (!choice?.semantic_role) return null;
  const matchedNumbers = new Set([...exactByChoice.values()].map((input) => input.id));
  const roleChoices = choices.filter(
    (input) =>
      !exactByChoice.has(input.id) && input.semantic_role === choice.semantic_role,
  );
  const roleNumbers = numbers.filter(
    (input) =>
      !matchedNumbers.has(input.id) && input.semantic_role === choice.semantic_role,
  );
  return roleChoices.length === 1 && roleChoices[0].id === choice.id && roleNumbers.length === 1
    ? roleNumbers[0]
    : null;
}

export function applyChoiceStrengthDefaults(
  contract,
  values = {},
  explicitInputIds = [],
  changedChoiceId = null,
) {
  const result = structuredClone(values || {});
  const explicit = new Set(explicitInputIds || []);
  for (const choice of interfaceInputs(contract)) {
    if (choice.type !== "choice" || (changedChoiceId && choice.id !== changedChoiceId)) continue;
    const companion = choiceStrengthCompanion(contract, choice);
    if (!companion || explicit.has(companion.id)) continue;
    const option = choiceOptions(choice).find((item) => item.value === result[choice.id]);
    const hintedStrength = option?.default_strength;
    if (typeof hintedStrength === "number" && Number.isFinite(hintedStrength)) {
      result[companion.id] = hintedStrength;
    } else if (Object.hasOwn(companion, "default")) {
      result[companion.id] = structuredClone(companion.default);
    } else {
      delete result[companion.id];
    }
  }
  return result;
}

export function reconcileInterfaceValues(
  contract,
  values = {},
  previousContract = null,
  explicitInputIds = [],
) {
  const result = defaultsForInterface(contract);
  const previousInputs = new Map(interfaceInputs(previousContract).map((input) => [input.id, input]));
  for (const input of interfaceInputs(contract)) {
    if (!Object.hasOwn(values, input.id)) continue;
    const previous = previousInputs.get(input.id);
    if (previousContract && (!previous || previous.type !== input.type)) continue;
    if (
      input.type === "choice" &&
      !choiceOptions(input).some((option) => option.value === values[input.id])
    )
      continue;
    result[input.id] =
      input.type === "seed" ? seedFormValue(input, values[input.id]) : structuredClone(values[input.id]);
  }
  return applyChoiceStrengthDefaults(contract, result, explicitInputIds);
}

export function overwriteWithRecall(current, recall) {
  const sourceKey = recall.source_key ?? recall.profile_id;
  const parameters = structuredClone(recall.parameters || recall.controls || {});
  return {
    ...current,
    activeSourceKey: sourceKey,
    activeProfileId: sourceKey,
    parameters,
    controls: structuredClone(parameters),
    selectedRevision: structuredClone(recall.revision || recall.identity || null),
    recallIdentity: structuredClone(recall.revision || recall.identity || null),
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
    case "integer": {
      if (raw === "") return null;
      const parsed = Number(raw);
      return Number.isSafeInteger(parsed) ? parsed : raw;
    }
    case "number":
      return raw === "" ? null : Number(raw);
    case "boolean":
      return Boolean(raw);
    default:
      return raw;
  }
}

function decimalInteger(value) {
  const text = String(value ?? "").trim();
  if (!/^-?\d+$/.test(text)) return null;
  try {
    return { text, number: BigInt(text) };
  } catch {
    return null;
  }
}

function seedParts(input, value) {
  const state = seedFormValue(input, value);
  return { random: state.mode === "random", value: state.value.trim() };
}

export function parametersForRequest(contract, values) {
  const parameters = {};
  for (const input of interfaceInputs(contract)) {
    if (!Object.hasOwn(values || {}, input.id)) continue;
    const value = values[input.id];
    if (input.type === "seed") {
      const seed = seedParts(input, value);
      if (seed.random) {
        if (input.required) parameters[input.id] = "random";
        continue;
      }
      if (!seed.value) continue;
      const parsed = decimalInteger(seed.value);
      parameters[input.id] = parsed ? parsed.number.toString() : seed.value;
    } else if (value !== undefined && value !== null) {
      parameters[input.id] = structuredClone(value);
    }
  }
  return parameters;
}

function numericStepMatches(value, minimum, step) {
  const numericStep = Number(step);
  if (!Number.isFinite(numericStep) || numericStep <= 0) return true;
  const base = Number.isFinite(Number(minimum)) ? Number(minimum) : 0;
  const quotient = (value - base) / numericStep;
  return Math.abs(quotient - Math.round(quotient)) <= 1e-9 * Math.max(1, Math.abs(quotient));
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
  for (const control of interfaceInputs(contract)) {
    const presentation = controlPresentation(control, values, capabilities);
    if (!presentation.visible || !presentation.enabled || presentation.forbidden) continue;
    const value = values[control.id];
    if (control.type === "choice" && value === "") {
      errors[control.id] = `Choose one of: ${choiceOptions(control)
        .map((option) => option.value)
        .join(", ")}.`;
      continue;
    }
    if (
      control.type !== "seed" &&
      presentation.required &&
      (value === undefined || value === null || value === "")
    ) {
      errors[control.id] = "Required.";
      continue;
    }
    if (value === undefined || value === null || value === "") continue;
    if (
      control.type === "choice" &&
      !choiceOptions(control).some((option) => option.value === value)
    ) {
      errors[control.id] = `Choose one of: ${choiceOptions(control)
        .map((option) => option.value)
        .join(", ")}.`;
      continue;
    }
    const minimum = inputConstraint(control, "minimum");
    const maximum = inputConstraint(control, "maximum");
    const step = inputConstraint(control, "step");
    if (control.type === "integer" && !Number.isSafeInteger(value)) {
      errors[control.id] = "Enter a safe whole number.";
      continue;
    }
    if (["integer", "number"].includes(control.type)) {
      if (!Number.isFinite(value)) {
        errors[control.id] = "Enter a valid number.";
        continue;
      }
      if (minimum !== undefined && value < Number(minimum)) errors[control.id] = `Minimum ${minimum}.`;
      else if (maximum !== undefined && value > Number(maximum)) errors[control.id] = `Maximum ${maximum}.`;
      else if (!numericStepMatches(value, minimum, step)) errors[control.id] = `Use increments of ${step}.`;
    }
    if (["string", "multiline_string"].includes(control.type)) {
      const constraints = control.constraints || {};
      if (constraints.minimum_length && value.length < constraints.minimum_length)
        errors[control.id] = `Use at least ${constraints.minimum_length} characters.`;
      if (constraints.maximum_length && value.length > constraints.maximum_length)
        errors[control.id] = `Use at most ${constraints.maximum_length} characters.`;
    }
    if (control.type === "seed") {
      const seed = seedParts(control, value);
      if (seed.random) continue;
      const parsed = decimalInteger(seed.value);
      if (!parsed) {
        errors[control.id] = "Enter a whole-number seed.";
        continue;
      }
      const minimumSeed = minimum === undefined ? null : decimalInteger(minimum);
      const maximumSeed = maximum === undefined ? null : decimalInteger(maximum);
      if (minimumSeed && parsed.number < minimumSeed.number) errors[control.id] = `Minimum ${minimum}.`;
      else if (maximumSeed && parsed.number > maximumSeed.number) errors[control.id] = `Maximum ${maximum}.`;
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
