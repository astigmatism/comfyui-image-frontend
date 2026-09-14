import { app } from "../../scripts/app.js";
import { createUuid, runPublicationCommand } from "./publication.js";

const CONTRACT_SCHEMA = "comfyui-image-frontend.interface/v1";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SEED_CONTROL_MODES = new Set([
  "fixed",
  "increment",
  "decrement",
  "randomize",
]);

function migrateSeedControlWidget(graphData) {
  const graphs = [graphData, ...(graphData?.definitions?.subgraphs ?? [])];
  for (const graph of graphs) {
    for (const node of graph?.nodes ?? []) {
      if (node.type !== "CIFSeedParameter") continue;

      const values = node.widgets_values;
      if (!Array.isArray(values) || SEED_CONTROL_MODES.has(values[1])) {
        continue;
      }

      // publication/v1 seed nodes originally serialized as:
      // value, minimum, maximum, step, default_mode, ...metadata.
      // ComfyUI's native seed control is serialized directly after value.
      const defaultMode = values[4];
      if (defaultMode !== "random" && defaultMode !== "fixed") continue;
      values.splice(1, 0, defaultMode === "random" ? "randomize" : "fixed");
    }
  }
}

function randomUnitInterval() {
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    const words = new Uint32Array(2);
    globalThis.crypto.getRandomValues(words);
    const high21Bits = words[0] & 0x001fffff;
    return (high21Bits * 0x100000000 + words[1]) / 0x20000000000000;
  }
  return Math.random();
}

function primeRandomSeedNode(node) {
  if (node.type !== "CIFSeedParameter") return;

  const widgets = new Map(
    (node.widgets ?? []).map((widget) => [widget.name, widget]),
  );
  const valueWidget = widgets.get("value");
  const minimumWidget = widgets.get("minimum");
  const maximumWidget = widgets.get("maximum");
  const stepWidget = widgets.get("step");
  const defaultModeWidget = widgets.get("default_mode");
  const localModeWidget = widgets.get("control_after_generate");

  if (
    !valueWidget ||
    defaultModeWidget?.value !== "random" ||
    localModeWidget?.value !== "randomize"
  ) {
    return;
  }

  const minimum = Number(minimumWidget?.value);
  const maximum = Number(maximumWidget?.value);
  const step = Number(stepWidget?.value);
  if (
    !Number.isSafeInteger(minimum) ||
    !Number.isSafeInteger(maximum) ||
    !Number.isSafeInteger(step) ||
    minimum > maximum ||
    step < 1
  ) {
    return;
  }

  const valueCount = Math.floor((maximum - minimum) / step) + 1;
  const value = minimum + Math.floor(randomUnitInterval() * valueCount) * step;
  valueWidget.value = value;
  valueWidget.callback?.(value);
  node.graph?.setDirtyCanvas?.(true, true);
}

function parseChoiceOptions(serialized) {
  try {
    const value = JSON.parse(String(serialized ?? ""));
    if (!Array.isArray(value)) return [];
    return value
      .filter(
        (option) =>
          option &&
          typeof option === "object" &&
          /^[a-z][a-z0-9_]{0,63}$/.test(String(option.id ?? "")),
      )
      .map((option) => ({
        value: String(option.id),
        label: String(option.label ?? option.id),
      }));
  } catch {
    return [];
  }
}

function refreshChoiceWidget(node) {
  if (node.type !== "CIFChoiceParameter") return;

  const valueWidget = node.widgets?.find((widget) => widget.name === "value");
  const optionsWidget = node.widgets?.find(
    (widget) => widget.name === "options_json",
  );
  if (!valueWidget || !optionsWidget) return;

  const choices = parseChoiceOptions(optionsWidget.value);
  const values = choices.map((choice) => choice.value);
  valueWidget.type = "combo";
  valueWidget.options ??= {};
  valueWidget.options.values = values;

  if (values.length && !values.includes(String(valueWidget.value ?? ""))) {
    valueWidget.value = values[0];
    valueWidget.callback?.(valueWidget.value);
  }
  node.graph?.setDirtyCanvas?.(true, true);
}

function initializeChoiceNode(node) {
  if (node.type !== "CIFChoiceParameter") return;

  const optionsWidget = node.widgets?.find(
    (widget) => widget.name === "options_json",
  );
  if (!optionsWidget) return;

  if (!optionsWidget.__cifChoiceCallbackInstalled) {
    const originalCallback = optionsWidget.callback;
    optionsWidget.callback = function cifChoiceOptionsChanged(value, ...args) {
      originalCallback?.call(this, value, ...args);
      refreshChoiceWidget(node);
    };
    optionsWidget.__cifChoiceCallbackInstalled = true;
  }
  refreshChoiceWidget(node);
}

function ensureInstanceUuid(node) {
  const widget = node.widgets?.find((candidate) => candidate.name === "instance_uuid");
  if (!widget || UUID_PATTERN.test(String(widget.value ?? "").trim())) {
    return;
  }

  widget.value = createUuid();
  widget.callback?.(widget.value);
  node.graph?.setDirtyCanvas?.(true, true);
}

function initializeInterfaceNode(node) {
  if (!node.widgets?.some((widget) => widget.name === "instance_uuid")) {
    return;
  }

  node.properties ??= {};
  node.properties.cif_contract_schema = CONTRACT_SCHEMA;
  ensureInstanceUuid(node);
  initializeChoiceNode(node);
}

app.registerExtension({
  name: "comfyui-image-frontend.interface",

  beforeConfigureGraph(graphData) {
    migrateSeedControlWidget(graphData);
  },

  commands: [
    {
      id: "CIF.PublishWorkflow",
      label: "Save & Publish for Image Frontend",
      menubarLabel: "Save & Publish for Image Frontend",
      icon: "pi pi-upload",
      function: () => runPublicationCommand(app),
    },
  ],

  menuCommands: [
    {
      path: ["File"],
      commands: ["CIF.PublishWorkflow"],
    },
  ],

  nodeCreated(node) {
    initializeInterfaceNode(node);

    // Depending on the active canvas renderer, nodeCreated can run just before
    // widgets are attached. Retry after widget construction without patching a
    // node prototype.
    queueMicrotask(() => {
      initializeInterfaceNode(node);
    });
    setTimeout(() => {
      initializeInterfaceNode(node);
    }, 100);
  },

  loadedGraphNode(node) {
    initializeInterfaceNode(node);
    primeRandomSeedNode(node);
  },
});
