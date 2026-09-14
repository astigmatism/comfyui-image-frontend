const CONTRACT_SCHEMA = "comfyui-image-frontend.interface/v1";
const PUBLICATION_SCHEMA = "comfyui-image-frontend.publication/v1";
const GENERATION_SOURCE_SCHEMA =
  "comfyui-image-frontend.generation-source/v1";
const TECHNICAL_INVENTORY_SCHEMA =
  "comfyui-image-frontend.technical-inventory/v1";

// Timeline metadata is intentionally curated and frozen into each publication.
// Publication must remain deterministic and usable offline, so it never derives
// a release date from a local file timestamp or performs a network lookup.
const ARCHITECTURE_TIMELINES = {
  sd15: {
    introduced_month: "2022-10",
    source: {
      source_type: "archived_model_card",
      publisher: "RunwayML",
      title: "Stable Diffusion v1.5 model card (archived mirror)",
      url: "https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5",
    },
  },
  sdxl: {
    introduced_month: "2023-07",
    source: {
      source_type: "official_announcement",
      publisher: "Stability AI",
      title: "Announcing SDXL 1.0",
      url: "https://stability.ai/news-updates/stable-diffusion-sdxl-1-announcement",
    },
  },
  flux1: {
    introduced_month: "2024-08",
    source: {
      source_type: "official_announcement",
      publisher: "Black Forest Labs",
      title: "Announcing Black Forest Labs and FLUX.1",
      url: "https://bfl.ai/blog/24-08-01-bfl",
    },
  },
  z_image: {
    introduced_month: "2025-11",
    source: {
      source_type: "official_model_release",
      publisher: "Tongyi-MAI",
      title: "Z-Image-Turbo",
      url: "https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
    },
  },
  flux2_klein_4b: {
    introduced_month: "2026-01",
    source: {
      source_type: "official_announcement",
      publisher: "Black Forest Labs",
      title: "FLUX.2 [klein]: Towards Interactive Visual Intelligence",
      url: "https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence",
    },
  },
  flux2_klein_9b: {
    introduced_month: "2026-01",
    source: {
      source_type: "official_announcement",
      publisher: "Black Forest Labs",
      title: "FLUX.2 [klein]: Towards Interactive Visual Intelligence",
      url: "https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence",
    },
  },
  krea2: {
    introduced_month: "2026-05",
    source: {
      source_type: "official_announcement",
      publisher: "Krea",
      title: "Introducing Krea 2",
      url: "https://www.krea.ai/blog/krea-2-image-model",
    },
  },
};

const MODEL_TIMELINES_BY_ARTIFACT = {
  "darkbeastint8convrot2_krea211int8convrot.safetensors": {
    released_month: "2026-07",
    source: {
      source_type: "creator_release",
      publisher: "AiMetatron",
      title: "KREA2 黑兽1.1 INT8 Convrot",
      url: "https://civitai.com/models/2242173?modelVersionId=3091496",
    },
  },
  "moodydesiremix_v30.safetensors": {
    released_month: "2026-06",
    source: {
      source_type: "creator_release",
      publisher: "catlover1938",
      title: "Moody Desire Mix v3.0",
      url: "https://civitai.com/models/2519616?modelVersionId=3063794",
    },
  },
  "flux-2-klein-9b-fp8.safetensors": {
    released_month: "2026-01",
    source: ARCHITECTURE_TIMELINES.flux2_klein_9b.source,
  },
  "krea2_turbo_fp8_scaled.safetensors": {
    released_month: "2026-06",
    source: {
      source_type: "official_announcement",
      publisher: "Krea",
      title: "Introducing Krea 2 Turbo",
      url: "https://www.krea.ai/blog/krea-2-turbo",
    },
  },
  "moodykrea2mix_v70_nvfp4.safetensors": {
    released_month: "2026-08",
    source: {
      source_type: "creator_release",
      publisher: "catlover1938",
      title: "Moody Krea 2 Mix V7.0 Regular (NVFP4)",
      url: "https://civitai.red/models/2731187?modelVersionId=3209007",
    },
  },
  "moodykrea2mix_v60bf16.safetensors": {
    released_month: "2026-08",
    source: {
      source_type: "creator_release",
      publisher: "catlover1938",
      title: "Moody Krea 2 Mix V6.0 BF16",
      url: "https://civitai.red/models/2731187?modelVersionId=3193397",
    },
  },
};

const MODEL_TIMELINES_BY_PUBLIC_CHOICE = {
  "flux1:checkpoint:flux_1_dev_q8": {
    released_month: "2024-08",
    source: ARCHITECTURE_TIMELINES.flux1.source,
  },
  "z_image:checkpoint:z_image_turbo_bf16": {
    released_month: "2025-11",
    source: ARCHITECTURE_TIMELINES.z_image.source,
  },
  "krea2:checkpoint:regular": {
    released_month: "2026-08",
    source: {
      source_type: "creator_release",
      publisher: "catlover1938",
      title: "Moody Krea 2 Mix V7.0 Regular (NVFP4)",
      url: "https://civitai.red/models/2731187?modelVersionId=3209007",
    },
  },
  "krea2:checkpoint:bf16": {
    released_month: "2026-08",
    source: {
      source_type: "creator_release",
      publisher: "catlover1938",
      title: "Moody Krea 2 Mix V6.0 BF16",
      url: "https://civitai.red/models/2731187?modelVersionId=3193397",
    },
  },
};

const PUBLIC_ID_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SEMVER_PATTERN =
  /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

const PARAMETER_CLASSES = new Map([
  ["CIFTextParameter", "string"],
  ["CIFIntegerParameter", "integer"],
  ["CIFDecimalParameter", "number"],
  ["CIFBooleanParameter", "boolean"],
  ["CIFSeedParameter", "seed"],
  ["CIFChoiceParameter", "choice"],
  ["CIFImageParameter", "image"],
  ["CIFLoraStack", "lora_stack"],
]);

const PUBLISHER_CLASSES = new Map([
  ["CIFPublishImage", "image"],
  ["CIFPublishText", "text"],
]);

const SEMANTIC_ROLES = new Set([
  "custom",
  "positive_prompt",
  "negative_prompt",
  "seed",
  "width",
  "height",
  "batch_size",
  "steps",
  "cfg",
  "denoise",
  "sampler",
  "scheduler",
  "model",
  "lora",
  "upscale",
  "reference_image",
]);
const OUTPUT_ROLES = new Set(["final", "preview", "comparison", "auxiliary"]);
const IMAGE_CARDINALITIES = new Set(["one", "many"]);

const COMMON_PARAMETERS = [
  {
    input: "positive_prompt",
    id: "positive_prompt",
    label: "Prompt",
    semantic_role: "positive_prompt",
    type: "string",
    required: true,
    advanced: false,
    group: "Basic",
    order: 10,
  },
  {
    input: "negative_prompt",
    id: "negative_prompt",
    label: "Negative Prompt",
    semantic_role: "negative_prompt",
    type: "string",
    required: false,
    advanced: false,
    group: "Basic",
    order: 20,
  },
  {
    input: "seed",
    id: "seed",
    label: "Seed",
    semantic_role: "seed",
    type: "seed",
    required: false,
    advanced: false,
    group: "Basic",
    order: 30,
    minimum: 0,
    maximum: Number.MAX_SAFE_INTEGER,
    step: 1,
    default_mode: "random",
  },
  {
    input: "width",
    id: "width",
    label: "Width",
    semantic_role: "width",
    type: "integer",
    required: true,
    advanced: false,
    group: "Basic",
    order: 40,
    minimum: 16,
    maximum: 16384,
    step: 8,
  },
  {
    input: "height",
    id: "height",
    label: "Height",
    semantic_role: "height",
    type: "integer",
    required: true,
    advanced: false,
    group: "Basic",
    order: 50,
    minimum: 16,
    maximum: 16384,
    step: 8,
  },
  {
    input: "batch_size",
    id: "batch_size",
    label: "Batch Size",
    semantic_role: "batch_size",
    type: "integer",
    required: false,
    advanced: true,
    group: "Advanced",
    order: 100,
    minimum: 1,
    maximum: 4096,
    step: 1,
  },
  {
    input: "enable_upscale",
    id: "enable_upscale",
    label: "Enable Upscaling",
    semantic_role: "upscale",
    type: "boolean",
    required: false,
    advanced: false,
    group: "Basic",
    order: 60,
  },
];

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isLink(value, nodeId, slot) {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    String(value[0]) === String(nodeId) &&
    Number(value[1]) === Number(slot)
  );
}

function outputIsConnected(apiGraph, nodeId, slot = 0) {
  return Object.values(apiGraph).some((node) =>
    Object.values(node?.inputs ?? {}).some((value) =>
      isLink(value, nodeId, slot),
    ),
  );
}

function outputConnections(apiGraph, nodeId, slot = 0) {
  const connections = [];
  for (const [targetNodeId, targetNode] of Object.entries(apiGraph ?? {})) {
    for (const [inputName, value] of Object.entries(targetNode?.inputs ?? {})) {
      if (isLink(value, nodeId, slot)) {
        connections.push({
          node_id: String(targetNodeId),
          class_type: targetNode.class_type,
          input: inputName,
        });
      }
    }
  }
  return connections;
}

function requirePublicId(value, field, errors) {
  const normalized = String(value ?? "").trim();
  if (!PUBLIC_ID_PATTERN.test(normalized)) {
    errors.push(
      `${field} must start with a lowercase letter and contain only lowercase letters, digits, and underscores`,
    );
  }
  return normalized;
}

function requireUuid(value, field, errors) {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (!UUID_PATTERN.test(normalized)) {
    errors.push(`${field} must be a canonical UUID generated for that node`);
  }
  return normalized;
}

function requireString(value, field, errors) {
  const normalized = String(value ?? "").trim();
  if (!normalized) errors.push(`${field} must not be empty`);
  return normalized;
}

function validateNumericInputs(inputs, nodeId, errors) {
  const value = Number(inputs.value);
  const minimum = Number(inputs.minimum);
  const maximum = Number(inputs.maximum);
  const step = Number(inputs.step);

  if (![value, minimum, maximum, step].every(Number.isFinite)) {
    errors.push(`parameter node ${nodeId} has non-finite numeric metadata`);
  } else {
    if (minimum > maximum) {
      errors.push(`parameter node ${nodeId} has minimum greater than maximum`);
    }
    if (step <= 0) {
      errors.push(`parameter node ${nodeId} has a non-positive step`);
    }
    if (value < minimum || value > maximum) {
      errors.push(`parameter node ${nodeId} has a default outside its bounds`);
    }
  }

  return { minimum, maximum, step };
}

function validateChoiceInputs(inputs, nodeId, errors) {
  let rawChoices;
  try {
    rawChoices = JSON.parse(String(inputs.options_json ?? ""));
  } catch {
    errors.push(`options_json on choice parameter node ${nodeId} must be valid JSON`);
    return { choices: [], bindings: [] };
  }

  if (!Array.isArray(rawChoices) || !rawChoices.length) {
    errors.push(`choice parameter node ${nodeId} must declare at least one choice`);
    return { choices: [], bindings: [] };
  }
  if (rawChoices.length > 100) {
    errors.push(`choice parameter node ${nodeId} declares more than 100 choices`);
  }

  const choices = [];
  const publicIds = new Set();
  const bindings = new Set();
  for (const [index, rawChoice] of rawChoices.entries()) {
    if (!isObject(rawChoice)) {
      errors.push(`choice ${index + 1} on node ${nodeId} must be an object`);
      continue;
    }

    const value = requirePublicId(
      rawChoice.id,
      `choice ID ${index + 1} on node ${nodeId}`,
      errors,
    );
    const label = requireString(
      rawChoice.label,
      `choice label ${index + 1} on node ${nodeId}`,
      errors,
    );
    const binding = requireString(
      rawChoice.binding,
      `private choice binding ${index + 1} on node ${nodeId}`,
      errors,
    );

    if (publicIds.has(value)) {
      errors.push(`duplicate choice ID ${JSON.stringify(value)} on node ${nodeId}`);
    }
    if (bindings.has(binding)) {
      errors.push(`duplicate private choice binding on node ${nodeId}`);
    }
    publicIds.add(value);
    bindings.add(binding);

    const choice = { value, label };
    if (Object.hasOwn(rawChoice, "default_strength")) {
      const defaultStrength = Number(rawChoice.default_strength);
      if (!Number.isFinite(defaultStrength)) {
        errors.push(
          `default_strength on choice ${value || index + 1} (node ${nodeId}) must be finite`,
        );
      } else {
        choice.default_strength = defaultStrength;
      }
    }
    choices.push(choice);
  }

  if (typeof inputs.value !== "string") {
    errors.push(`default on choice parameter node ${nodeId} must be a string`);
  } else if (!choices.some((choice) => choice.value === inputs.value)) {
    errors.push(
      `default ${JSON.stringify(inputs.value)} on choice parameter node ${nodeId} is not declared`,
    );
  }
  return { choices, bindings: [...bindings] };
}

function validateChoiceDestinations(
  nodeId,
  bindings,
  apiGraph,
  nodeDefs,
  errors,
) {
  for (const connection of outputConnections(apiGraph, nodeId, 0)) {
    const nodeDef = nodeDefs?.[connection.class_type];
    const inputConfig =
      nodeDef?.input?.required?.[connection.input] ??
      nodeDef?.input?.optional?.[connection.input];
    const installedOptions = inputConfig?.[0];
    if (!Array.isArray(installedOptions)) {
      errors.push(
        `choice parameter node ${nodeId} target ${connection.node_id}.${connection.input} is not an installed COMBO input`,
      );
      continue;
    }

    const admitted = new Set(installedOptions.map((value) => String(value)));
    for (const binding of bindings) {
      if (!admitted.has(binding)) {
        errors.push(
          `private choice binding ${JSON.stringify(binding)} on node ${nodeId} is not allowed by ${connection.node_id}.${connection.input}`,
        );
      }
    }
  }
}

function validateCommonDefault(parameter, nodeId, errors) {
  const value = parameter.default;
  if (parameter.type === "string" && typeof value !== "string") {
    errors.push(`default on common parameter ${parameter.id} (node ${nodeId}) must be a string`);
    return;
  }
  if (parameter.type === "boolean" && typeof value !== "boolean") {
    errors.push(`default on common parameter ${parameter.id} (node ${nodeId}) must be Boolean`);
    return;
  }
  if (["integer", "seed"].includes(parameter.type)) {
    if (!Number.isSafeInteger(value)) {
      errors.push(
        `default on common parameter ${parameter.id} (node ${nodeId}) must be a JSON-safe integer`,
      );
      return;
    }
    if (value < parameter.minimum || value > parameter.maximum) {
      errors.push(
        `default on common parameter ${parameter.id} (node ${nodeId}) is outside its public bounds`,
      );
    }
  }
}

function validateTypedParameter(
  nodeId,
  node,
  apiGraph,
  nodeDefs,
  errors,
  warnings,
) {
  const inputs = node.inputs ?? {};
  const type = PARAMETER_CLASSES.get(node.class_type);
  const id = requirePublicId(
    inputs.parameter_id,
    `parameter_id on node ${nodeId}`,
    errors,
  );
  const instanceUuid = requireUuid(
    inputs.instance_uuid,
    `instance_uuid on node ${nodeId}`,
    errors,
  );
  const label = requireString(inputs.label, `label on node ${nodeId}`, errors);
  const group = requireString(inputs.group, `group on node ${nodeId}`, errors);
  const description = String(inputs.description ?? "").trim();
  const semanticRole = String(inputs.semantic_role ?? "custom");

  if (!description) {
    warnings.push(`parameter ${id || nodeId} has no description`);
  }
  if (!outputIsConnected(apiGraph, nodeId, 0)) {
    errors.push(`parameter ${id || nodeId} is not connected to the API graph`);
  }
  if (typeof inputs.required !== "boolean") {
    errors.push(`required on parameter ${id || nodeId} must be Boolean`);
  }
  if (typeof inputs.advanced !== "boolean") {
    errors.push(`advanced on parameter ${id || nodeId} must be Boolean`);
  }
  if (!Number.isInteger(inputs.order) || inputs.order < 0) {
    errors.push(`order on parameter ${id || nodeId} must be a non-negative integer`);
  }
  if (!SEMANTIC_ROLES.has(semanticRole)) {
    errors.push(`semantic_role on parameter ${id || nodeId} is invalid`);
  }

  const parameter = {
    id,
    type,
    instance_uuid: instanceUuid,
    label,
    description,
    semantic_role: semanticRole,
    required: inputs.required,
    advanced: inputs.advanced,
    group,
    order: inputs.order,
    bindings: [
      {
        node_id: String(nodeId),
        input: type === "image" ? "image" : "value",
      },
    ],
  };

  if (type !== "image") {
    parameter.default = inputs.value;
  }

  if (type === "lora_stack") {
    try {
      const catalog = JSON.parse(inputs.catalog_json);
      if (!Array.isArray(catalog) || !catalog.length || catalog.length > 100) throw new Error("Publish 1 to 100 LoRAs");
      const ids = new Set();
      for (const item of catalog) {
        if (!isObject(item) || Object.keys(item).some((key) => !["id", "label", "filename", "description"].includes(key)) ||
            typeof item.id !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(item.id) || ids.has(item.id) ||
            typeof item.label !== "string" || !item.label.trim() || item.label.length > 120 ||
            typeof item.filename !== "string" || !item.filename || item.filename.length > 1000 ||
            (Object.hasOwn(item, "description") && (typeof item.description !== "string" || !item.description.trim() || item.description.length > 1000))) {
          throw new Error("Invalid private LoRA catalog");
        }
        ids.add(item.id);
      }
      const installed = nodeDefs.LoraLoaderModelOnly?.input?.required?.lora_name?.[0];
      if (!Array.isArray(installed)) throw new Error("Native model-only LoRA loader inventory is unavailable");
      if (catalog.some((item) => !installed.includes(item.filename))) throw new Error("A catalog LoRA is not installed on this runtime");
      const { minimum, maximum, step } = inputs;
      if (![minimum, maximum, step].every((n) => typeof n === "number" && Number.isFinite(n)) ||
          minimum !== 0 || maximum <= 0 || step <= 0) throw new Error("Invalid LoRA strength constraints");
      const value = JSON.parse(inputs.value);
      if (!Array.isArray(value) || value.length !== catalog.length) throw new Error("Include every LoRA exactly once");
      const seen = new Set();
      for (const entry of value) {
        if (!isObject(entry) || Object.keys(entry).sort().join() !== "id,strength" ||
            !ids.has(entry.id) || seen.has(entry.id) || entry.strength !== 0) {
          throw new Error("Publish each LoRA exactly once with zero default strength");
        }
        seen.add(entry.id);
      }
      if (semanticRole !== "lora" || inputs.required !== false) throw new Error("LoRA stacks must be optional with semantic role lora");
      Object.assign(parameter, {
        items: catalog.map(({ id, label, description }) => ({ id, label, ...(description === undefined ? {} : { description }) })),
        default: value, minimum, maximum, step,
      });
    } catch (error) {
      errors.push(`LoRA stack ${id || nodeId}: ${error.message}`);
    }
  }

  if (type === "image") {
    const maxBytes = Number(inputs.max_bytes);
    const maxWidth = Number(inputs.max_width);
    const maxHeight = Number(inputs.max_height);
    if (![maxBytes, maxWidth, maxHeight].every(Number.isSafeInteger)) {
      errors.push(`media limits on image parameter ${id || nodeId} must be JSON-safe integers`);
    } else if ([maxBytes, maxWidth, maxHeight].some((value) => value < 1)) {
      errors.push(`media limits on image parameter ${id || nodeId} must be positive`);
    }
    if (inputs.required !== true) {
      errors.push(
        `image parameter ${id || nodeId} must be required in publication/v1; optional media needs an execution-safe graph branch`,
      );
    }
    parameter.media = {
      upload_route: "/upload/image",
      storage_type: "input",
      accepted_mime_types: ["image/png", "image/jpeg", "image/webp"],
      max_bytes: maxBytes,
      max_width: maxWidth,
      max_height: maxHeight,
      animated: false,
      returns_mask: true,
    };
  }

  if (["integer", "number", "seed"].includes(type)) {
    Object.assign(parameter, validateNumericInputs(inputs, nodeId, errors));
  }
  if (type === "integer" && !Number.isInteger(inputs.value)) {
    errors.push(`default on integer parameter ${id || nodeId} must be an integer`);
  }
  if (
    ["integer", "seed"].includes(type) &&
    ![inputs.value, inputs.minimum, inputs.maximum, inputs.step].every(
      Number.isSafeInteger,
    )
  ) {
    errors.push(`integer metadata on parameter ${id || nodeId} must be JSON-safe`);
  }
  if (type === "seed") {
    if (!Number.isInteger(inputs.value)) {
      errors.push(`default on seed parameter ${id || nodeId} must be an integer`);
    }
    if (!new Set(["fixed", "random"]).has(inputs.default_mode)) {
      errors.push(`default_mode on seed parameter ${id || nodeId} is invalid`);
    }
    parameter.default_mode = inputs.default_mode;
  }
  if (type === "boolean" && typeof inputs.value !== "boolean") {
    errors.push(`default on Boolean parameter ${id || nodeId} must be Boolean`);
  }
  if (type === "string" && typeof inputs.value !== "string") {
    errors.push(`default on string parameter ${id || nodeId} must be a string`);
  }
  if (type === "choice") {
    const choiceContract = validateChoiceInputs(inputs, nodeId, errors);
    parameter.choices = choiceContract.choices;
    validateChoiceDestinations(
      nodeId,
      choiceContract.bindings,
      apiGraph,
      nodeDefs,
      errors,
    );
  }

  return parameter;
}

function validateCommonInterface(nodeId, node, apiGraph, errors) {
  const inputs = node.inputs ?? {};
  const interfaceId = requirePublicId(
    inputs.interface_id,
    `interface_id on node ${nodeId}`,
    errors,
  );
  const instanceUuid = requireUuid(
    inputs.instance_uuid,
    `instance_uuid on node ${nodeId}`,
    errors,
  );
  const interfaceVersion = String(inputs.interface_version ?? "").trim();
  if (!SEMVER_PATTERN.test(interfaceVersion)) {
    errors.push(`interface_version on node ${nodeId} must be semantic versioning`);
  }

  const parameters = [];
  for (const [slot, declaration] of COMMON_PARAMETERS.entries()) {
    if (!outputIsConnected(apiGraph, nodeId, slot)) continue;
    const parameter = {
      ...declaration,
      default: inputs[declaration.input],
      instance_uuid: instanceUuid,
      interface_id: interfaceId,
      interface_version: interfaceVersion,
      description: "",
      bindings: [{ node_id: String(nodeId), input: declaration.input }],
    };
    validateCommonDefault(parameter, nodeId, errors);
    parameters.push(parameter);
  }

  if (!parameters.length) {
    errors.push(`common interface node ${nodeId} has no connected public outputs`);
  }
  return { instanceUuid, parameters };
}

function validatePublisher(nodeId, node, apiGraph, errors, warnings) {
  const inputs = node.inputs ?? {};
  const outputId = requirePublicId(
    inputs.output_id,
    `output_id on node ${nodeId}`,
    errors,
  );
  const instanceUuid = requireUuid(
    inputs.instance_uuid,
    `instance_uuid on node ${nodeId}`,
    errors,
  );
  const description = String(inputs.description ?? "").trim();
  const kind = PUBLISHER_CLASSES.get(node.class_type);
  const sourceInput = kind === "image" ? inputs.images : inputs.text;
  const role = String(inputs.role ?? "auxiliary");
  const cardinality = kind === "image" ? String(inputs.cardinality) : "one";

  if (
    !Array.isArray(sourceInput) ||
    sourceInput.length !== 2 ||
    !apiGraph[String(sourceInput[0])]
  ) {
    errors.push(`publisher ${outputId || nodeId} is not connected`);
  }
  if (!description) {
    warnings.push(`publisher ${outputId || nodeId} has no description`);
  }
  if (!OUTPUT_ROLES.has(role)) {
    errors.push(`role on publisher ${outputId || nodeId} is invalid`);
  }
  if (kind === "image" && !IMAGE_CARDINALITIES.has(cardinality)) {
    errors.push(`cardinality on publisher ${outputId || nodeId} is invalid`);
  }

  return {
    id: outputId,
    type: kind,
    role,
    description,
    cardinality,
    instance_uuid: instanceUuid,
    node_id: String(nodeId),
  };
}

function assertUnique(items, field, label, errors) {
  const seen = new Map();
  for (const item of items) {
    const value = item[field];
    if (!value) continue;
    if (seen.has(value)) {
      errors.push(
        `duplicate ${label} ${JSON.stringify(value)} on ${seen.get(value)} and ${item.node_id ?? item.id}`,
      );
    } else {
      seen.set(value, item.node_id ?? item.id);
    }
  }
}

function validateUiDeclarations(savedWorkflow, declarationNodes, errors) {
  const uiNodes = new Map(
    (savedWorkflow?.nodes ?? []).map((node) => [String(node.id), node]),
  );

  for (const { nodeId, classType } of declarationNodes) {
    if (String(nodeId).includes(":")) {
      errors.push(
        `declaration node ${nodeId} is inside a subgraph; publication/v1 requires interface declarations at the workflow root`,
      );
      continue;
    }
    const uiNode = uiNodes.get(String(nodeId));
    if (!uiNode || uiNode.type !== classType) {
      errors.push(
        `API declaration node ${nodeId} does not match the saved editable workflow`,
      );
      continue;
    }
    if (uiNode.properties?.cif_contract_schema !== CONTRACT_SCHEMA) {
      errors.push(
        `saved declaration node ${nodeId} is missing schema ${CONTRACT_SCHEMA}`,
      );
    }
  }
}

export function validatePublication(savedWorkflow, apiGraph, nodeDefs = {}) {
  const errors = [];
  const warnings = [];
  const inputs = [];
  const outputs = [];
  const declarationNodes = [];
  const declarationInstances = [];

  if (!isObject(apiGraph) || !Object.keys(apiGraph).length) {
    errors.push("compiled API graph is empty");
  }

  for (const [nodeId, node] of Object.entries(apiGraph ?? {})) {
    const classType = node?.class_type;
    if (!String(classType ?? "").startsWith("CIF")) continue;

    declarationNodes.push({ nodeId, classType });
    if (PARAMETER_CLASSES.has(classType)) {
      const parameter = validateTypedParameter(
        nodeId,
        node,
        apiGraph,
        nodeDefs,
        errors,
        warnings,
      );
      inputs.push(parameter);
      declarationInstances.push({
        id: parameter.id,
        node_id: String(nodeId),
        instance_uuid: parameter.instance_uuid,
      });
    } else if (classType === "CIFImageFrontendInterface") {
      const common = validateCommonInterface(nodeId, node, apiGraph, errors);
      inputs.push(...common.parameters);
      declarationInstances.push({
        id: `interface:${nodeId}`,
        node_id: String(nodeId),
        instance_uuid: common.instanceUuid,
      });
    } else if (PUBLISHER_CLASSES.has(classType)) {
      const output = validatePublisher(nodeId, node, apiGraph, errors, warnings);
      outputs.push(output);
      declarationInstances.push({
        id: output.id,
        node_id: String(nodeId),
        instance_uuid: output.instance_uuid,
      });
    } else {
      errors.push(`unsupported CIF declaration class ${classType} on node ${nodeId}`);
    }
  }

  if (!inputs.length) {
    errors.push("workflow has no connected Image Frontend parameters");
  }
  const prompts = inputs.filter(
    (input) => input.semantic_role === "positive_prompt",
  );
  if (prompts.length !== 1) {
    errors.push(
      `workflow must declare exactly one connected positive_prompt; found ${prompts.length}`,
    );
  }

  assertUnique(inputs, "id", "public parameter ID", errors);
  assertUnique(outputs, "id", "public output ID", errors);
  assertUnique(
    declarationInstances,
    "instance_uuid",
    "declaration instance UUID",
    errors,
  );
  validateUiDeclarations(savedWorkflow, declarationNodes, errors);

  const nativeOutputs = Object.entries(apiGraph ?? {})
    .filter(([, node]) => nodeDefs?.[node.class_type]?.output_node === true)
    .map(([nodeId, node]) => ({
      node_id: String(nodeId),
      class_type: node.class_type,
      title: String(node?._meta?.title ?? node.class_type),
      declared: PUBLISHER_CLASSES.has(node.class_type),
    }));

  if (!nativeOutputs.length) {
    errors.push("compiled API graph has no native output nodes");
  }
  if (!outputs.length) {
    warnings.push(
      "workflow has no CIF publisher; native history outputs will be returned as unmapped_outputs",
    );
  }

  inputs.sort(
    (a, b) =>
      Number(Boolean(a.advanced)) - Number(Boolean(b.advanced)) ||
      Number(a.order) - Number(b.order) ||
      String(a.group).localeCompare(String(b.group)) ||
      String(a.id).localeCompare(String(b.id)),
  );
  outputs.sort((a, b) => String(a.id).localeCompare(String(b.id)));

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    inputs,
    outputs,
    native_outputs: nativeOutputs,
  };
}

const TECHNOLOGY_RULES = [
  {
    id: "seedvr2",
    label: "SeedVR2",
    category: "upscaling",
    matches: (classType) => classType.includes("SeedVR2"),
  },
  {
    id: "ultimate_sd_upscale",
    label: "Ultimate SD Upscale",
    category: "upscaling",
    matches: (classType) => classType.includes("UltimateSDUpscale"),
  },
  {
    id: "reference_latent",
    label: "Reference Latent Conditioning",
    category: "conditioning",
    matches: (classType) => classType === "ReferenceLatent",
  },
  {
    id: "controlnet",
    label: "ControlNet",
    category: "conditioning",
    matches: (classType) => classType.toLowerCase().includes("controlnet"),
  },
  {
    id: "ipadapter",
    label: "IPAdapter",
    category: "conditioning",
    matches: (classType) => classType.toLowerCase().includes("ipadapter"),
  },
  {
    id: "detailer",
    label: "Detailer",
    category: "refinement",
    matches: (classType) =>
      classType.includes("Detailer") || classType.includes("BboxDetectorSEGS"),
  },
  {
    id: "ultralytics",
    label: "Ultralytics Detection",
    category: "detection",
    matches: (classType) => classType.includes("Ultralytics"),
  },
  {
    id: "model_upscale",
    label: "Model Upscaling",
    category: "upscaling",
    matches: (classType) => classType === "ImageUpscaleWithModel",
  },
  {
    id: "image_sharpening",
    label: "Image Sharpening",
    category: "postprocessing",
    matches: (classType) => classType.includes("ImageSharpen"),
  },
  {
    id: "color_matching",
    label: "Color Matching",
    category: "postprocessing",
    matches: (classType) => classType.includes("ColorMatch"),
  },
  {
    id: "image_blending",
    label: "Image Blending",
    category: "postprocessing",
    matches: (classType) => classType === "ImageBlend",
  },
];

function artifactBasename(value) {
  if (typeof value !== "string") return null;
  const normalized = value.trim().replace(/\\/g, "/");
  if (!normalized) return null;
  return normalized.split("/").filter(Boolean).at(-1) ?? null;
}

function uniqueSorted(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) =>
    String(a).localeCompare(String(b)),
  );
}

function stableUniqueObjects(values, key) {
  const unique = new Map();
  for (const value of values) {
    const identity = key(value);
    if (!unique.has(identity)) unique.set(identity, value);
  }
  return [...unique.values()].sort((a, b) =>
    key(a).localeCompare(key(b)),
  );
}

function reachableApiNodeIds(apiGraph, nativeOutputs) {
  const reachable = new Set();
  const pending = nativeOutputs
    .map((output) => String(output.node_id))
    .filter((nodeId) => apiGraph[nodeId]);

  while (pending.length) {
    const nodeId = pending.pop();
    if (reachable.has(nodeId)) continue;
    reachable.add(nodeId);
    const node = apiGraph[nodeId];
    for (const value of Object.values(node?.inputs ?? {})) {
      if (
        Array.isArray(value) &&
        value.length === 2 &&
        apiGraph[String(value[0])]
      ) {
        pending.push(String(value[0]));
      }
    }
  }
  return reachable;
}

function publicInputByDeclarationNode(inputs) {
  const result = new Map();
  for (const input of inputs) {
    for (const binding of input.bindings ?? []) {
      result.set(String(binding.node_id), input);
    }
  }
  return result;
}

function publicChoiceForLink(value, apiGraph, inputsByNode) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const sourceNodeId = String(value[0]);
  if (!apiGraph[sourceNodeId]) return null;
  const input = inputsByNode.get(sourceNodeId);
  return input?.type === "choice" ? input : null;
}

function samplerInventory(node) {
  const allowed = [
    "steps",
    "cfg",
    "sampler_name",
    "scheduler",
    "denoise",
    "add_noise",
  ];
  const settings = {};
  for (const key of allowed) {
    const value = node.inputs?.[key];
    if (["string", "number", "boolean"].includes(typeof value)) {
      settings[key] = value;
    }
  }
  return {
    class_type: node.class_type,
    settings,
  };
}

function parsePrivateChoiceOptions(nodeInputs) {
  if (typeof nodeInputs?.options_json !== "string") return [];
  try {
    const parsed = JSON.parse(nodeInputs.options_json);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value) => isObject(value));
  } catch {
    return [];
  }
}

function privateModelChoiceEvidence(apiGraph, reachable) {
  const evidence = [];
  for (const nodeId of reachable) {
    const node = apiGraph[nodeId];
    if (
      node?.class_type !== "CIFChoiceParameter" ||
      node?.inputs?.semantic_role !== "model"
    ) {
      continue;
    }
    evidence.push(
      node.inputs.parameter_id,
      node.inputs.value,
      node.inputs.label,
      node.inputs.description,
    );
    for (const option of parsePrivateChoiceOptions(node.inputs)) {
      evidence.push(option.id, option.label, option.binding);
    }
  }
  return evidence.filter(Boolean);
}

function inferBaseModel(models, textEncoders, apiGraph, reachable) {
  const clipTypes = [];
  for (const nodeId of reachable) {
    const node = apiGraph[nodeId];
    if (String(node?.class_type ?? "").includes("CLIPLoader")) {
      if (typeof node.inputs?.type === "string") clipTypes.push(node.inputs.type);
    }
  }
  const evidence = [
    ...models.map((value) => value.artifact),
    ...textEncoders.map((value) => value.artifact),
    ...clipTypes,
    ...privateModelChoiceEvidence(apiGraph, reachable),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  let family = "unknown";
  let familyLabel = "Unknown";
  let architecture = "unknown";
  let architectureLabel = "Unknown";

  if (evidence.includes("krea2") || clipTypes.includes("krea2")) {
    family = "krea2";
    familyLabel = "Krea 2";
    architecture = "krea2";
    architectureLabel = "Krea 2";
  } else if (
    evidence.includes("flux2") ||
    evidence.includes("flux-2") ||
    clipTypes.includes("flux2")
  ) {
    family = "flux2";
    familyLabel = "Flux 2";
    if (
      evidence.includes("klein-9b") ||
      evidence.includes("klein_9b") ||
      evidence.includes("qwen_3_8b")
    ) {
      architecture = "flux2_klein_9b";
      architectureLabel = "Flux 2 Klein 9B";
    } else if (
      evidence.includes("klein-4b") ||
      evidence.includes("klein_4b") ||
      evidence.includes("qwen_3_4b")
    ) {
      architecture = "flux2_klein_4b";
      architectureLabel = "Flux 2 Klein 4B";
    } else {
      architecture = "flux2";
      architectureLabel = "Flux 2";
    }
  } else if (
    evidence.includes("z_image") ||
    evidence.includes("z-image") ||
    evidence.includes("z image")
  ) {
    family = "z_image";
    familyLabel = "Z-Image";
    architecture = "z_image";
    architectureLabel = "Z-Image";
  } else if (
    evidence.includes("flux1") ||
    evidence.includes("flux.1") ||
    evidence.includes("flux 1")
  ) {
    family = "flux1";
    familyLabel = "FLUX.1";
    architecture = "flux1";
    architectureLabel = "FLUX.1";
  } else if (evidence.includes("noobai")) {
    family = "noobai";
    familyLabel = "NoobAI XL";
    architecture = "sdxl";
    architectureLabel = "Stable Diffusion XL";
  } else if (evidence.includes("illustrious")) {
    family = "illustrious";
    familyLabel = "Illustrious XL";
    architecture = "sdxl";
    architectureLabel = "Stable Diffusion XL";
  } else if (evidence.includes("pony")) {
    family = "pony";
    familyLabel = "Pony Diffusion";
    architecture = "sdxl";
    architectureLabel = "Stable Diffusion XL";
  } else if (
    evidence.includes("sd15") ||
    evidence.includes("sd 1.5") ||
    evidence.includes("stable diffusion 1.5")
  ) {
    family = "stable_diffusion";
    familyLabel = "Stable Diffusion";
    architecture = "sd15";
    architectureLabel = "Stable Diffusion 1.5";
  } else if (evidence.includes("sdxl")) {
    family = "sdxl";
    familyLabel = "Stable Diffusion XL";
    architecture = "sdxl";
    architectureLabel = "Stable Diffusion XL";
  } else if (evidence.includes("sd3")) {
    family = "sd3";
    familyLabel = "Stable Diffusion 3";
    architecture = "sd3";
    architectureLabel = "Stable Diffusion 3";
  } else if (evidence.includes("wan")) {
    family = "wan";
    familyLabel = "Wan";
    architecture = "wan";
    architectureLabel = "Wan";
  } else if (evidence.includes("ltx")) {
    family = "ltx_video";
    familyLabel = "LTX Video";
    architecture = "ltx_video";
    architectureLabel = "LTX Video";
  }

  return {
    family,
    family_label: familyLabel,
    architecture,
    architecture_label: architectureLabel,
    primary_artifacts: uniqueSorted(
      models
        .filter((value) => value.usage === "primary")
        .map((value) => value.artifact),
    ),
  };
}

function sourceCopy(source) {
  return source
    ? {
        source_type: source.source_type,
        publisher: source.publisher,
        title: source.title,
        url: source.url,
      }
    : null;
}

function baseModelTimeline(baseModel, models, publicInputs) {
  const timeline = {};
  const architecture = ARCHITECTURE_TIMELINES[baseModel.architecture];
  if (architecture) {
    timeline.architecture = {
      introduced_month: architecture.introduced_month,
      source: sourceCopy(architecture.source),
    };
  }

  const modelChoice = publicInputs.find(
    (input) => input.type === "choice" && input.semantic_role === "model",
  );
  if (modelChoice) {
    const variants = [];
    for (const option of modelChoice.choices ?? []) {
      const key = `${baseModel.architecture}:${modelChoice.id}:${option.value}`;
      const release = MODEL_TIMELINES_BY_PUBLIC_CHOICE[key];
      if (!release) continue;
      variants.push({
        parameter_id: modelChoice.id,
        value: option.value,
        label: option.label,
        released_month: release.released_month,
        source: sourceCopy(release.source),
      });
    }
    if (variants.length) {
      timeline.model_variants = variants;
      const defaultModel = variants.find(
        (variant) => variant.value === modelChoice.default,
      );
      if (defaultModel) {
        timeline.default_model = {
          ...defaultModel,
          release_basis: "default_checkpoint",
        };
      }
    }
  }

  if (!timeline.default_model) {
    for (const model of models) {
      const release =
        MODEL_TIMELINES_BY_ARTIFACT[String(model.artifact).toLowerCase()];
      if (!release) continue;
      timeline.default_model = {
        artifact: model.artifact,
        released_month: release.released_month,
        release_basis: "fixed_primary_model",
        source: sourceCopy(release.source),
      };
      break;
    }
  }

  return Object.keys(timeline).length ? timeline : null;
}

function deriveGenerationType(inputs, outputs, technologies, samplers) {
  const hasPrompt = inputs.some(
    (input) => input.semantic_role === "positive_prompt",
  );
  const hasImageInput = inputs.some((input) => input.type === "image");
  const hasVideoInput = inputs.some((input) => input.type === "video");
  const finalKinds = outputs
    .filter((output) => output.role === "final")
    .map((output) => output.type);
  const outputKinds = finalKinds.length
    ? uniqueSorted(finalKinds)
    : uniqueSorted(outputs.map((output) => output.type));
  const hasImageOutput = outputKinds.includes("image");
  const hasVideoOutput = outputKinds.includes("video");
  const generative = samplers.length > 0;
  const onlyUpscaling =
    !generative &&
    technologies.some((value) => value.category === "upscaling");

  let generationType = "other";
  if (hasVideoInput && hasVideoOutput) generationType = "video_to_video";
  else if (hasImageInput && hasVideoOutput) generationType = "image_to_video";
  else if (hasPrompt && hasVideoOutput) generationType = "text_to_video";
  else if (hasImageInput && hasImageOutput && onlyUpscaling) {
    generationType = "image_upscale";
  } else if (hasImageInput && hasImageOutput) generationType = "image_to_image";
  else if (hasPrompt && hasImageOutput) generationType = "text_to_image";

  return {
    generationType,
    hasPrompt,
    inputMedia: uniqueSorted([
      ...(hasPrompt ? ["text"] : []),
      ...(hasImageInput ? ["image"] : []),
      ...(hasVideoInput ? ["video"] : []),
    ]),
    outputMedia: outputKinds,
  };
}

function generationSummary(generationType, dimensionPolicy, outputs) {
  const declaredFinals = outputs.filter((output) => output.role === "final").length;
  const ending = declaredFinals
    ? ` and returns ${declaredFinals === 1 ? "a declared final result" : `${declaredFinals} declared final results`}`
    : "";
  const dimensionText =
    dimensionPolicy === "source_image"
      ? " using source-image-derived dimensions"
      : dimensionPolicy === "explicit"
        ? " using explicit dimensions"
        : " using workflow-defined dimensions";

  const templates = {
    text_to_image: `Generates images from prompt guidance${dimensionText}${ending}.`,
    image_to_image: `Transforms a required reference image according to prompt guidance${dimensionText}${ending}.`,
    text_to_video: `Generates video from prompt guidance${dimensionText}${ending}.`,
    image_to_video: `Generates video from a required reference image with prompt guidance${dimensionText}${ending}.`,
    video_to_video: `Transforms a required source video according to prompt guidance${ending}.`,
    image_upscale: `Upscales or restores a required source image${ending}.`,
    other: `Executes the published workflow interface${ending}.`,
  };
  return templates[generationType] ?? templates.other;
}

export function derivePublishedMetadata(savedWorkflow, apiGraph, validation) {
  const reachable = reachableApiNodeIds(
    apiGraph,
    validation.native_outputs ?? [],
  );
  const compiledNodeIds = new Set(Object.keys(apiGraph));
  const orphanNodeIds = [...compiledNodeIds].filter(
    (nodeId) => !reachable.has(nodeId),
  );
  const inputsByNode = publicInputByDeclarationNode(validation.inputs ?? []);

  const models = [];
  const loras = [];
  const textEncoders = [];
  const vaes = [];
  const upscalers = [];
  const detectors = [];
  const samplers = [];
  const recognizedLoaderIds = new Set();

  for (const nodeId of reachable) {
    const node = apiGraph[nodeId];
    const classType = String(node?.class_type ?? "");
    const nodeInputs = node?.inputs ?? {};

    if (classType === "UNETLoader" || classType === "UnetLoaderGGUF") {
      const artifact = artifactBasename(nodeInputs.unet_name);
      if (artifact) models.push({ kind: "diffusion_model", artifact, usage: "primary" });
      recognizedLoaderIds.add(nodeId);
    } else if (classType.includes("CheckpointLoader")) {
      const artifact = artifactBasename(nodeInputs.ckpt_name);
      if (artifact) models.push({ kind: "checkpoint", artifact, usage: "primary" });
      recognizedLoaderIds.add(nodeId);
    }

    if (/CLIP.*Loader/i.test(classType)) {
      for (const [name, value] of Object.entries(nodeInputs)) {
        if (!name.includes("clip_name")) continue;
        const artifact = artifactBasename(value);
        if (artifact) textEncoders.push({ artifact, usage: "conditioning" });
      }
      recognizedLoaderIds.add(nodeId);
    }

    if (classType === "VAELoader") {
      const artifact = artifactBasename(nodeInputs.vae_name);
      if (artifact) vaes.push({ artifact, usage: "primary" });
      recognizedLoaderIds.add(nodeId);
    } else if (classType === "SeedVR2LoadVAEModel") {
      const artifact = artifactBasename(nodeInputs.model);
      if (artifact) vaes.push({ artifact, usage: "seedvr2" });
      recognizedLoaderIds.add(nodeId);
    }

    if (classType === "UpscaleModelLoader") {
      const artifact = artifactBasename(nodeInputs.model_name);
      if (artifact) upscalers.push({ artifact, usage: "image_upscale" });
      recognizedLoaderIds.add(nodeId);
    } else if (classType === "SeedVR2LoadDiTModel") {
      const artifact = artifactBasename(nodeInputs.model);
      if (artifact) upscalers.push({ artifact, usage: "seedvr2" });
      recognizedLoaderIds.add(nodeId);
    }

    if (classType === "UltralyticsDetectorProvider") {
      const artifact = artifactBasename(nodeInputs.model_name);
      if (artifact) detectors.push({ artifact, usage: "detection" });
      recognizedLoaderIds.add(nodeId);
    }

    if (classType === "CIFLoraStack") {
      const stack = inputsByNode.get(nodeId);
      if (stack) loras.push({
        usage: "public_stack", parameter_id: stack.id,
        items: stack.items.map(({ id, label, description }) => ({ id, label, ...(description === undefined ? {} : { description }) })),
        default: stack.default.map(({ id, strength }) => ({ id, strength })),
        minimum: stack.minimum, maximum: stack.maximum, step: stack.step,
      });
      recognizedLoaderIds.add(nodeId);
    } else if (classType.includes("Power Lora Loader")) {
      for (const value of Object.values(nodeInputs)) {
        if (!isObject(value) || value.on !== true) continue;
        const artifact = artifactBasename(value.lora);
        if (!artifact) continue;
        const item = { usage: "fixed_active", artifact };
        if (Number.isFinite(Number(value.strength))) {
          item.strength = Number(value.strength);
        }
        loras.push(item);
      }
      recognizedLoaderIds.add(nodeId);
    } else if (/LoraLoader/i.test(classType)) {
      const choice = publicChoiceForLink(
        nodeInputs.lora_name,
        apiGraph,
        inputsByNode,
      );
      if (choice) {
        loras.push({
          usage: "public_choice",
          parameter_id: choice.id,
          default: choice.default,
          options: (choice.choices ?? []).map((option) => ({
            value: option.value,
            label: option.label,
            ...(Object.hasOwn(option, "default_strength")
              ? { default_strength: option.default_strength }
              : {}),
          })),
        });
      } else {
        const artifact = artifactBasename(nodeInputs.lora_name);
        if (artifact) {
          const item = { usage: "fixed_active", artifact };
          const strength = nodeInputs.strength_model ?? nodeInputs.strength;
          if (Number.isFinite(Number(strength))) item.strength = Number(strength);
          loras.push(item);
        }
      }
      recognizedLoaderIds.add(nodeId);
    }

    if (
      classType === "KSampler" ||
      classType === "KSamplerAdvanced" ||
      classType.includes("KSampler_") ||
      classType === "SamplerCustomAdvanced"
    ) {
      samplers.push(samplerInventory(node));
    }
  }

  const reachableClassTypes = uniqueSorted(
    [...reachable].map((nodeId) => apiGraph[nodeId]?.class_type),
  );
  const orphanClassTypes = uniqueSorted(
    orphanNodeIds.map((nodeId) => apiGraph[nodeId]?.class_type),
  );
  const technologies = TECHNOLOGY_RULES.filter((rule) =>
    reachableClassTypes.some((classType) => rule.matches(String(classType))),
  ).map(({ id, label, category }) => ({ id, label, category }));

  const unclassifiedLoaders = stableUniqueObjects(
    [...reachable]
      .filter((nodeId) => {
        const classType = String(apiGraph[nodeId]?.class_type ?? "");
        return (
          !recognizedLoaderIds.has(nodeId) &&
          /(loader|provider)/i.test(classType) &&
          !classType.startsWith("CIF")
        );
      })
      .map((nodeId) => ({ class_type: apiGraph[nodeId].class_type })),
    (value) => value.class_type,
  );

  const normalizedModels = stableUniqueObjects(
    models,
    (value) => `${value.kind}:${value.artifact}:${value.usage}`,
  );
  const normalizedTextEncoders = stableUniqueObjects(
    textEncoders,
    (value) => `${value.artifact}:${value.usage}`,
  );
  const normalizedVaes = stableUniqueObjects(
    vaes,
    (value) => `${value.artifact}:${value.usage}`,
  );
  const normalizedUpscalers = stableUniqueObjects(
    upscalers,
    (value) => `${value.artifact}:${value.usage}`,
  );
  const normalizedDetectors = stableUniqueObjects(
    detectors,
    (value) => `${value.artifact}:${value.usage}`,
  );
  const normalizedLoras = stableUniqueObjects(
    loras,
    (value) =>
      `${value.usage}:${value.parameter_id ?? ""}:${value.artifact ?? ""}`,
  );
  const normalizedSamplers = stableUniqueObjects(
    samplers,
    (value) => `${value.class_type}:${JSON.stringify(value.settings)}`,
  );

  const baseModel = inferBaseModel(
    normalizedModels,
    normalizedTextEncoders,
    apiGraph,
    reachable,
  );
  const timeline = baseModelTimeline(
    baseModel,
    normalizedModels,
    validation.inputs ?? [],
  );
  if (timeline) baseModel.timeline = timeline;
  const generation = deriveGenerationType(
    validation.inputs ?? [],
    validation.outputs ?? [],
    technologies,
    normalizedSamplers,
  );
  const hasWidth = validation.inputs.some(
    (input) => input.semantic_role === "width",
  );
  const hasHeight = validation.inputs.some(
    (input) => input.semantic_role === "height",
  );
  const dimensionPolicy =
    hasWidth && hasHeight
      ? "explicit"
      : generation.inputMedia.includes("image")
        ? "source_image"
        : "workflow_defined";
  const subgraphs = savedWorkflow?.definitions?.subgraphs ?? [];
  const editableSubgraphNodes = subgraphs.reduce(
    (total, subgraph) => total + (subgraph?.nodes?.length ?? 0),
    0,
  );

  const inventoryWarnings = [];
  if (baseModel.family === "unknown") {
    inventoryWarnings.push("base_model_architecture_unresolved");
  } else if (!baseModel.timeline?.architecture) {
    inventoryWarnings.push("base_model_architecture_timeline_unresolved");
  }
  if (unclassifiedLoaders.length) {
    inventoryWarnings.push("unclassified_loaders_present");
  }

  const summary = generationSummary(
    generation.generationType,
    dimensionPolicy,
    validation.outputs ?? [],
  );
  const tags = uniqueSorted([
    generation.generationType,
    ...(generation.hasPrompt ? ["prompt_guided"] : []),
    baseModel.family !== "unknown" ? baseModel.family : null,
    baseModel.architecture !== "unknown" ? baseModel.architecture : null,
    ...technologies.map((value) => value.id),
  ]);

  return {
    generation_source: {
      schema_version: GENERATION_SOURCE_SCHEMA,
      inference_method: "deterministic_graph_analysis",
      generation_type: generation.generationType,
      prompt_guided: generation.hasPrompt,
      input_media: generation.inputMedia,
      output_media: generation.outputMedia,
      dimension_policy: dimensionPolicy,
      summary,
      base_model: baseModel,
      technologies,
      tags,
    },
    technical_inventory: {
      schema_version: TECHNICAL_INVENTORY_SCHEMA,
      node_counts: {
        editable_root: savedWorkflow?.nodes?.length ?? 0,
        subgraph_definitions: subgraphs.length,
        editable_subgraph_nodes: editableSubgraphNodes,
        compiled_api: compiledNodeIds.size,
        output_reachable: reachable.size,
        compiled_orphans: orphanNodeIds.length,
      },
      models: normalizedModels,
      loras: normalizedLoras,
      text_encoders: normalizedTextEncoders,
      vaes: normalizedVaes,
      upscalers: normalizedUpscalers,
      detectors: normalizedDetectors,
      samplers: normalizedSamplers,
      technologies,
      reachable_class_types: reachableClassTypes,
      orphan_class_types: orphanClassTypes,
      unclassified_loaders: unclassifiedLoaders,
      warnings: inventoryWarnings,
    },
  };
}

const SHA256_ROUND_CONSTANTS = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
  0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
  0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
  0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
  0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

function rotateRight(value, bits) {
  return (value >>> bits) | (value << (32 - bits));
}

function portableSha256(bytes) {
  const bitLength = bytes.length * 8;
  const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
  const padded = new Uint8Array(paddedLength);
  padded.set(bytes);
  padded[bytes.length] = 0x80;

  const paddedView = new DataView(padded.buffer);
  paddedView.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000));
  paddedView.setUint32(paddedLength - 4, bitLength >>> 0);

  const hash = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];
  const words = new Uint32Array(64);

  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      words[index] = paddedView.getUint32(offset + index * 4);
    }
    for (let index = 16; index < 64; index += 1) {
      const previous15 = words[index - 15];
      const previous2 = words[index - 2];
      const sigma0 =
        rotateRight(previous15, 7) ^
        rotateRight(previous15, 18) ^
        (previous15 >>> 3);
      const sigma1 =
        rotateRight(previous2, 17) ^
        rotateRight(previous2, 19) ^
        (previous2 >>> 10);
      words[index] =
        (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0;
    }

    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index += 1) {
      const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const choice = (e & f) ^ (~e & g);
      const temporary1 =
        (h + sum1 + choice + SHA256_ROUND_CONSTANTS[index] + words[index]) >>> 0;
      const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temporary2 = (sum0 + majority) >>> 0;

      h = g;
      g = f;
      f = e;
      e = (d + temporary1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temporary1 + temporary2) >>> 0;
    }

    hash[0] = (hash[0] + a) >>> 0;
    hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0;
    hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0;
    hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0;
    hash[7] = (hash[7] + h) >>> 0;
  }

  return hash.map((word) => word.toString(16).padStart(8, "0")).join("");
}

export async function sha256Text(value) {
  const bytes = new TextEncoder().encode(value);
  const subtle = globalThis.crypto?.subtle;
  if (subtle) {
    try {
      const digest = await subtle.digest("SHA-256", bytes);
      return Array.from(new Uint8Array(digest), (byte) =>
        byte.toString(16).padStart(2, "0"),
      ).join("");
    } catch (error) {
      console.warn(
        "[comfyui-image-frontend] Web Crypto SHA-256 unavailable; using portable fallback",
        error,
      );
    }
  }
  return portableSha256(bytes);
}

export function createUuid() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

export function publicationPaths(workflowPath) {
  const normalized = String(workflowPath ?? "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  const pathParts = normalized.split("/");
  if (
    !normalized.startsWith("workflows/") ||
    pathParts.some((part) => !part || part === "." || part === "..")
  ) {
    throw new Error("Publish requires a saved workflow in the ComfyUI workflows directory");
  }
  if (!normalized.toLowerCase().endsWith(".json")) {
    throw new Error("Publish requires a JSON workflow filename");
  }
  if (
    normalized.toLowerCase().endsWith(".api.json") ||
    normalized.toLowerCase().endsWith(".interface.json")
  ) {
    throw new Error("An API or interface artifact cannot be published as a workflow");
  }

  const workflowSuffix = ".workflow.json";
  const stem = normalized.toLowerCase().endsWith(workflowSuffix)
    ? normalized.slice(0, -workflowSuffix.length)
    : normalized.slice(0, -".json".length);
  return {
    workflow: normalized,
    api: `${stem}.api.json`,
    manifest: `${stem}.interface.json`,
  };
}

function activeWorkflow(app) {
  const workflows = app.extensionManager?.workflow;
  return (
    workflows?.activeWorkflow ?? workflows?.getActiveWorkflow?.() ?? null
  );
}

async function saveWithCoreCommand(app) {
  const command = app.extensionManager?.command?.commands?.find(
    (candidate) => candidate.id === "Comfy.SaveWorkflow",
  );
  if (!command?.function) {
    throw new Error("The installed frontend does not expose its normal Save command");
  }
  await command.function();
}

function notify(app, severity, summary, detail, life) {
  const toast = app.extensionManager?.toast;
  if (toast?.add) {
    toast.add({
      severity,
      summary,
      detail,
      life: life ?? (severity === "error" ? 10000 : 6000),
    });
  } else {
    console[severity === "error" ? "error" : "info"](`${summary}: ${detail}`);
  }
}

export async function publishCurrentWorkflow(app) {
  const hasDeclaration = (app.rootGraph?._nodes ?? []).some((node) =>
    String(node.type ?? "").startsWith("CIF"),
  );
  if (!hasDeclaration) {
    throw new Error("The active workflow has no Image Frontend declaration nodes");
  }

  await saveWithCoreCommand(app);
  const workflowRecord = activeWorkflow(app);
  if (!workflowRecord?.path || workflowRecord.isTemporary) {
    throw new Error("Save the workflow to ComfyUI userdata before publishing it");
  }

  const paths = publicationPaths(workflowRecord.path);
  const compiled = await app.graphToPrompt();
  const savedResponse = await app.api.getUserData(paths.workflow, {
    cache: "no-store",
  });
  if (!savedResponse.ok) {
    throw new Error(
      `Could not read the saved workflow (${savedResponse.status} ${savedResponse.statusText})`,
    );
  }

  const savedWorkflowText = await savedResponse.text();
  let savedWorkflow;
  try {
    savedWorkflow = JSON.parse(savedWorkflowText);
  } catch {
    throw new Error("The saved workflow is not valid JSON");
  }

  const nodeDefs = await app.api.getNodeDefs();
  const validation = validatePublication(
    savedWorkflow,
    compiled.output,
    nodeDefs,
  );
  if (!validation.valid) {
    throw new Error(`Publication validation failed:\n- ${validation.errors.join("\n- ")}`);
  }

  const apiText = JSON.stringify(compiled.output, null, 2);
  const compiledWorkflowText = JSON.stringify(compiled.workflow);
  const [workflowHash, compiledWorkflowHash, apiHash] = await Promise.all([
    sha256Text(savedWorkflowText),
    sha256Text(compiledWorkflowText),
    sha256Text(apiText),
  ]);
  const publishedMetadata = derivePublishedMetadata(
    savedWorkflow,
    compiled.output,
    validation,
  );

  const manifest = {
    schema_version: PUBLICATION_SCHEMA,
    contract_schema: CONTRACT_SCHEMA,
    publication_id: createUuid(),
    published_at: new Date().toISOString(),
    source_id: paths.workflow,
    workflow: {
      path: paths.workflow,
      sha256: workflowHash,
      compiled_sha256: compiledWorkflowHash,
      frontend_version: compiled.workflow?.extra?.frontendVersion ?? null,
    },
    api: {
      path: paths.api,
      sha256: apiHash,
      node_count: Object.keys(compiled.output).length,
    },
    interface: {
      inputs: validation.inputs,
      outputs: validation.outputs,
      native_outputs: validation.native_outputs,
      unmapped_outputs_policy: "collect",
    },
    runtime: {
      attach_workflow_as_extra_pnginfo: true,
      seed_values_must_be_concrete: true,
    },
    dependencies: {
      class_types: Array.from(
        new Set(Object.values(compiled.output).map((node) => node.class_type)),
      ).sort(),
    },
    generation_source: publishedMetadata.generation_source,
    technical_inventory: publishedMetadata.technical_inventory,
    warnings: validation.warnings,
  };
  const manifestText = JSON.stringify(manifest, null, 2);

  // ComfyUI's userdata writer replaces each file atomically. The manifest is
  // written last and indicates that the publisher completed the adjacent pair.
  // Recorded hashes identify the revision for diagnostics; local discovery
  // must not reject an otherwise valid bundle solely because a hash differs.
  await app.api.storeUserData(paths.api, apiText, {
    overwrite: true,
    stringify: false,
    throwOnError: true,
  });
  await app.api.storeUserData(paths.manifest, manifestText, {
    overwrite: true,
    stringify: false,
    throwOnError: true,
  });

  return { paths, manifest, validation };
}

export async function runPublicationCommand(app) {
  try {
    const result = await publishCurrentWorkflow(app);
    const warningSuffix = result.validation.warnings.length
      ? ` Published with ${result.validation.warnings.length} warning(s).`
      : "";
    notify(
      app,
      "success",
      "Image Frontend publication complete",
      `${result.paths.api} and ${result.paths.manifest}.${warningSuffix}`,
    );
    return result;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    notify(app, "error", "Image Frontend publication failed", detail, 30000);
    console.error("[comfyui-image-frontend] publication failed", error);
    return null;
  }
}

export {
  CONTRACT_SCHEMA,
  PUBLICATION_SCHEMA,
  GENERATION_SOURCE_SCHEMA,
  TECHNICAL_INVENTORY_SCHEMA,
};
