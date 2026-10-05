// Interactive design preview for Creative Direction expectations (vision check).
// The panel, shell and gallery cards come from the real render functions; the
// proposed Expectations block, status line, and verification dialog are layered
// on top with sample data. Nothing here talks to a server.
import {
  galleryCardMarkup,
  generationButtonContentMarkup,
  generationPanelMarkup,
  promptPipelineMarkup,
  shellMarkup,
} from "../src/render.mjs";

const IMAGES = {
  botanist: { src: "./assets/night-botanist.webp", tone: "" },
  botanistWarm: { src: "./assets/night-botanist.webp", tone: "xc-tone-warm" },
  cartographer: { src: "./assets/cartographer.webp", tone: "" },
  cartographerCool: { src: "./assets/cartographer.webp", tone: "xc-tone-cool" },
  cartographerMuted: { src: "./assets/cartographer.webp", tone: "xc-tone-muted" },
  orchid: { src: "./assets/glass-orchid.webp", tone: "" },
};

const SOURCE = { source_key: "moody", display_name: "Moody Krea2 Simple v31", available: true };
const CONTRACT = {
  inputs: [
    { id: "prompt", type: "string", label: "Prompt", semantic_role: "positive_prompt", default: "", advanced: false, group: "Basic", order: 10 },
    { id: "width", type: "integer", label: "Width", semantic_role: "width", default: 1024, minimum: 256, maximum: 2048, step: 64, advanced: false, group: "Basic", order: 20 },
    { id: "height", type: "integer", label: "Height", semantic_role: "height", default: 1536, minimum: 256, maximum: 2048, step: 64, advanced: false, group: "Basic", order: 21 },
    { id: "seed", type: "seed", label: "Seed", default: "random", advanced: false, group: "Basic", order: 30 },
  ],
};

const STARTING_PROMPT = "Portrait of a woman explorer";
const DIRECTION = "Make it a warm, painterly character portrait.";
const EXPECTATIONS = [
  "Head-and-shoulders portrait of one woman",
  "Curly red hair",
  "Warm golden-hour lighting",
  "Maps, a satchel, or instruments show she is a cartographer",
];
const MAX_EXPECTATIONS = 12;

const LIBRARY = {
  first: {
    image: "botanist",
    prompt: "Painterly portrait of a woman explorer at twilight in a glass conservatory, cropped auburn hair, round spectacles, embroidered velvet coat, glowing night-blooming flowers, cinematic detail",
    results: [
      [95, "One woman, framed head-and-shoulders."],
      [20, "Short, dark brown hair; not curly or red."],
      [15, "Cool violet night light dominates."],
      [10, "Greenhouse plants; no maps, satchel, or instruments."],
    ],
    summary: "A strong portrait, but the hair, light, and setting read as a night-time botanist.",
  },
  second: {
    image: "botanistWarm",
    prompt: "Warm painterly portrait of a woman cartographer, loose curly copper-red hair, golden-hour sunlight, rolled maps in a leather satchel, compass pendant, rich oil-painting texture",
    results: [
      [94, "Clear head-and-shoulders framing."],
      [35, "Hair reads dark auburn, still short and straight."],
      [74, "Warmer amber cast, but the light is flat."],
      [30, "No maps visible; the background is still botanical."],
    ],
    summary: "Warmer, but the hair and props still don't match.",
  },
  third: {
    image: "cartographer",
    prompt: "Head-and-shoulders portrait of a smiling woman cartographer with voluminous curly bright-red hair and freckles, warm golden backlight, worn teal jacket, leather satchel strap with rolled parchment maps over her shoulder, brass compass pendant, painterly style",
    results: [
      [96, "Clear head-and-shoulders portrait."],
      [94, "Voluminous curly red hair."],
      [88, "Warm golden glow behind the subject."],
      [86, "Satchel strap, rolled maps, and a compass pendant."],
    ],
    summary: "Every expectation is clearly visible.",
  },
  cool: {
    image: "cartographerCool",
    prompt: "Head-and-shoulders portrait of a woman cartographer with curly red hair, teal coastal light, satchel of rolled maps, compass pendant, painterly style",
    results: [
      [95, "Head-and-shoulders portrait."],
      [90, "Curly red hair."],
      [40, "Cool teal cast; no golden-hour warmth."],
      [84, "Satchel strap and rolled maps."],
    ],
    summary: "The cool grade fights the golden-hour expectation.",
  },
  muted: {
    image: "cartographerMuted",
    prompt: "Head-and-shoulders portrait of a woman cartographer, curly red hair, soft late-afternoon light, satchel with rolled maps, muted painterly palette",
    results: [
      [95, "Head-and-shoulders portrait."],
      [88, "Curly red hair, slightly desaturated."],
      [70, "Soft light, only faintly golden."],
      [80, "Satchel strap and rolled maps."],
    ],
    summary: "The muted palette weakens the warm light.",
  },
};

const ICONS = {
  check: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m3.5 8.5 3 3 6-7" /></svg>',
  cross: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4.5 4.5 7 7m0-7-7 7" /></svg>',
  alert: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 2.5 14 13H2L8 2.5Z" /><path d="M8 6.5v3M8 11.4v.1" /></svg>',
  notMet: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="6" /><path d="M8 4.8v3.8M8 11v.1" /></svg>',
  stop: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="4" y="4" width="8" height="8" rx="1.5" /></svg>',
  dot: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="1.5" /></svg>',
};

let view;
let simulation = null;
let confirmDelete = false;
let statusDismissed = false;

const app = document.querySelector("#app");
const dialog = document.querySelector("#expectation-check-dialog");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function attempt(number, key, threshold, status) {
  const entry = LIBRARY[key];
  const scored = status === "passed" || status === "not_met";
  const results = scored
    ? entry.results.map(([score, observation], index) => ({ expectation: EXPECTATIONS[index], score, observation, met: score >= threshold }))
    : null;
  return {
    number,
    status,
    prompt: status === "composing" ? null : entry.prompt,
    image: ["composing", "generating"].includes(status) ? null : entry.image,
    results,
    score: results ? Math.min(...results.map((item) => item.score)) : null,
    summary: scored ? entry.summary : null,
  };
}

function panelDefaults(overrides = {}) {
  return {
    expectationsEnabled: true,
    visionAvailable: true,
    autoOn: false,
    text: EXPECTATIONS.join("\n"),
    threshold: 80,
    maxAttempts: 5,
    prompt: STARTING_PROMPT,
    quantity: 1,
    expectationsOpen: true,
    ...overrides,
  };
}

function check(status, purpose, attempts, extra = {}) {
  return { status, purpose, threshold: 80, maxAttempts: 5, attempts, planned: purpose === "generate" ? 3 : 1, ...extra };
}

const SCENARIOS = {
  configured: () => ({ panel: panelDefaults(), check: null }),
  running: () => ({
    panel: panelDefaults(),
    check: check("running", "apply", [attempt(1, "first", 80, "not_met"), attempt(2, "second", 80, "generating")]),
  }),
  "passed-apply": () => ({
    panel: panelDefaults({ prompt: LIBRARY.third.prompt }),
    check: check("passed", "apply", [
      attempt(1, "first", 80, "not_met"),
      attempt(2, "second", 80, "not_met"),
      attempt(3, "third", 80, "passed"),
    ]),
  }),
  "passed-generate": () => ({
    panel: panelDefaults({ prompt: LIBRARY.third.prompt, quantity: 3 }),
    check: check(
      "passed",
      "generate",
      [attempt(1, "first", 80, "not_met"), attempt(2, "second", 80, "not_met"), attempt(3, "third", 80, "passed")],
      { queuedCount: 2 },
    ),
  }),
  "not-met": () => ({
    panel: panelDefaults({ threshold: 90 }),
    check: check(
      "not_met",
      "apply",
      [
        attempt(1, "first", 90, "not_met"),
        attempt(2, "second", 90, "not_met"),
        attempt(3, "cool", 90, "not_met"),
        attempt(4, "third", 90, "not_met"),
        attempt(5, "muted", 90, "not_met"),
      ],
      { threshold: 90, best: 4 },
    ),
  }),
  failed: () => ({
    panel: panelDefaults(),
    check: check(
      "failed",
      "apply",
      [attempt(1, "first", 80, "not_met"), { ...attempt(2, "second", 80, "evaluating"), status: "failed" }],
      { error: "The vision model could not inspect the image." },
    ),
  }),
  stopped: () => ({
    panel: panelDefaults(),
    check: check("stopped", "apply", [attempt(1, "first", 80, "not_met"), { ...attempt(2, "second", 80, "generating"), status: "stopped" }]),
  }),
  "vision-unavailable": () => ({ panel: panelDefaults({ visionAvailable: false }), check: null }),
  "auto-on": () => ({ panel: panelDefaults({ autoOn: true }), check: null }),
};

const TERMINAL = new Set(["passed", "not_met", "failed", "stopped"]);
const STATUS_LABEL = {
  running: "Verifying",
  passed: "Passed",
  not_met: "Not met",
  failed: "Failed",
  stopped: "Stopped",
};
const ATTEMPT_LABEL = {
  composing: "Composing",
  generating: "Generating image",
  evaluating: "Checking with vision",
  passed: "Passed",
  not_met: "Not met",
  failed: "Error",
  stopped: "Stopped",
};
const STATUS_CLASS = { passed: "is-passed", not_met: "is-not-met", failed: "is-failed", stopped: "is-stopped" };

function expectationLines(text) {
  return String(text || "")
    .split("\n")
    .map((line) => line.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "").trim())
    .filter(Boolean);
}

function expectationsActive(panel) {
  return panel.expectationsEnabled && panel.visionAvailable && !panel.autoOn && expectationLines(panel.text).length > 0;
}

function currentAttempt(c) {
  return c?.attempts.at(-1) || null;
}

function phaseCopy(c) {
  const current = currentAttempt(c);
  if (!current) return "";
  if (current.status === "composing") {
    return current.number === 1 ? "Composing a prompt from your Creative Direction and expectations" : `Revising the prompt from attempt ${current.number - 1}'s feedback`;
  }
  if (current.status === "generating") return `Generating one image with ${SOURCE.display_name}`;
  if (current.status === "evaluating") return "Checking the image against each expectation";
  return "";
}

function latestScored(c) {
  if (!c) return null;
  if (c.best) return c.attempts.find((item) => item.number === c.best);
  return [...c.attempts].reverse().find((item) => item.results) || null;
}

function failedAttempts(c) {
  if (!c) return [];
  return c.attempts.filter((item) => item.image && item.status !== "passed" && item.number !== c.best);
}

// ---------- Panel ----------

function appState() {
  const panel = view.panel;
  return {
    session: { app_title: "ImageGen", user: { id: "preview", username: "preview", role: "user" } },
    collections: [],
    currentCollectionId: null,
    galleryScale: 50,
    galleryLayout: "classic",
    panelOpen: document.querySelector(".app-shell")?.classList.contains("panel-open") || false,
    submitting: false,
    services: [{ service: "comfyui", available: true }],
    sources: [SOURCE],
    sourceCatalogStatus: "ready",
    activeSourceKey: SOURCE.source_key,
    parameters: { prompt: panel.prompt, width: 1024, height: 1536, seed: "random" },
    fieldErrors: {},
    formError: null,
    controlSectionOpen: { "creative-direction": true, prompt: true, resolution: false, ...(view.sections || {}) },
    autoGenerateCreativeDirection: true,
    autoGenerate: panel.autoOn,
    automation: panel.autoOn ? { enabled: true, status: "generating", snapshot: { assistant: { mode: "refine" } } } : null,
    autoGenerateStatus: panel.autoOn ? "generating" : "off",
    automationLoaded: true,
    generationQuantity: panel.quantity,
    promptAssistant: { available: true, mode: "refine", think: true, creativeDirection: DIRECTION },
    defaultComfyuiInstanceId: "primary",
    comfyuiInstances: [{ id: "primary", label: "Primary", is_default: true, available: true }],
  };
}

function expectationsMarkup(panel, running) {
  const lines = expectationLines(panel.text);
  const unavailable = !panel.visionAvailable;
  const on = panel.expectationsEnabled && !unavailable;
  const lock = unavailable || running ? "disabled" : "";
  const fieldsLock = !on || running ? "disabled" : "";
  const notes = [];
  if (unavailable) notes.push("The Creative Direction model can't inspect images right now, so expectations can't be verified. Apply Creative Direction still works without the check.");
  if (panel.autoOn && on) notes.push("Not applied during Auto-generate. Each cycle runs Creative Direction without the vision check.");
  return `<details class="prompt-expectations" data-xc-expectations ${panel.expectationsOpen ? "open" : ""}>
    <summary><span>Expectations</span><span class="prompt-expectations-badge ${on ? "is-on" : ""}" data-xc-badge>${on ? `On · ${lines.length}` : unavailable ? "Unavailable" : "Off"}</span><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6" /></svg></summary>
    <div class="prompt-expectations-content">
      <label class="prompt-expectations-toggle ${unavailable ? "is-disabled" : ""}"><input id="expectations-enabled" type="checkbox" ${on ? "checked" : ""} ${lock} /> Verify with vision</label>
      ${notes.map((note) => `<p class="prompt-expectations-note" role="note">${escapeHtml(note)}</p>`).join("")}
      <textarea id="creative-direction-expectations" rows="5" maxlength="4000" aria-label="Expectations, one per line" placeholder="One expectation per line, for example:&#10;Exactly two people are visible&#10;The subject wears a red coat" ${fieldsLock}>${escapeHtml(panel.text)}</textarea>
      <p class="prompt-expectations-meta"><span data-xc-count>${lines.length} of ${MAX_EXPECTATIONS} expectations</span><span>One per line</span></p>
      <div class="prompt-expectations-limits">
        <label class="field compact"><span>Pass score</span><input id="expectations-threshold" type="number" min="1" max="100" step="1" value="${panel.threshold}" ${fieldsLock} /></label>
        <label class="field compact"><span>Max attempts</span><input id="expectations-attempts" type="number" min="1" max="10" step="1" value="${panel.maxAttempts}" ${fieldsLock} /></label>
      </div>
      <p class="prompt-expectations-hint">Each attempt makes one image with the first selected model, scores every expectation from 0 to 100 by looking at it, and revises the prompt until every expectation reaches the pass score.</p>
    </div>
  </details>`;
}

function statusLineMarkup(c) {
  if (!c || statusDismissed) return "";
  const current = currentAttempt(c);
  const best = latestScored(c);
  const final = c.attempts.find((item) => item.status === "passed");
  let icon = '<span class="activity-spinner" aria-hidden="true"></span>';
  let title = "Verifying expectations";
  let detail = `Attempt ${current.number} of ${c.maxAttempts} · ${ATTEMPT_LABEL[current.status]}`;
  if (c.status === "passed") {
    icon = ICONS.check;
    title = `Passed · ${final.score}/100`;
    detail = c.purpose === "generate" ? `${c.queuedCount} more images queued` : "Prompt applied";
  } else if (c.status === "not_met") {
    icon = ICONS.notMet;
    title = `Not met · best ${best.score}/100`;
    detail = `Pass score ${c.threshold}`;
  } else if (c.status === "failed") {
    icon = ICONS.alert;
    title = "Check failed";
    detail = c.error;
  } else if (c.status === "stopped") {
    icon = ICONS.stop;
    title = `Stopped at attempt ${current.number}`;
    detail = "Images already queued will finish";
  }
  return `<div class="expectation-status ${STATUS_CLASS[c.status] || ""}" role="status" aria-live="polite">
    <span class="expectation-status-icon" aria-hidden="true">${icon}</span>
    <span class="expectation-status-text"><span>${escapeHtml(title)}</span><small>${escapeHtml(detail)}</small></span>
    <button type="button" class="button low" data-xc-action="view-check">View</button>
    ${TERMINAL.has(c.status) ? '<button type="button" class="icon-button" data-xc-action="dismiss-status" aria-label="Dismiss expectation check status">×</button>' : ""}
  </div>`;
}

function pipelineMarkup(c) {
  const current = currentAttempt(c);
  const running = c?.status === "running";
  const active = !running ? "" : current.status === "composing" ? "creative_direction" : current.status === "generating" ? "image" : "vision";
  const stage = (id, label, extra = "") => `<span data-pipeline-stage="${id}" class="pipeline-stage${extra}${active === id ? " is-active" : ""}" role="group" aria-label="${label}${active === id ? " (active)" : ""}">${label}</span>`;
  return `${stage("creative_direction", "Refine")} → ${stage("vision", `Vision check (≤${view.panel.maxAttempts})`, " is-vision-stage")} → ${stage("image", "Image")}`;
}

function renderPanel() {
  const state = appState();
  const host = document.querySelector("#generation-panel");
  const previousScroll = host.querySelector("#panel-scroll")?.scrollTop;
  host.innerHTML = generationPanelMarkup(state, SOURCE, CONTRACT);
  const panel = view.panel;
  const c = view.check;
  const running = c?.status === "running";
  const direction = host.querySelector("#creative-direction");
  if (direction) direction.value = DIRECTION;
  const body = host.querySelector("#prompt-assistant .assistant-body");
  const compose = body?.querySelector('[data-action="compose-prompt"]');
  if (compose) {
    compose.insertAdjacentHTML("beforebegin", expectationsMarkup(panel, running));
    compose.textContent = expectationsActive(panel) ? "Apply & verify" : "Apply Creative Direction";
    compose.disabled = running;
    compose.insertAdjacentHTML("afterend", statusLineMarkup(c));
  }
  const pipeline = host.querySelector("#prompt-pipeline-flow");
  if (pipeline) pipeline.innerHTML = expectationsActive(panel) ? pipelineMarkup(c) : promptPipelineMarkup(state);
  const generate = host.querySelector("#generate-button");
  if (generate && running) {
    generate.disabled = true;
    generate.setAttribute("aria-busy", "true");
    generate.innerHTML = generationButtonContentMarkup({ label: "Verifying…", busy: true });
  }
  const scroller = host.querySelector("#panel-scroll");
  const anchor = host.querySelector(`[data-control-section="${view.anchor || "creative-direction"}"]`);
  const endAnchor = view.anchorEnd ? host.querySelector(view.anchorEnd) : null;
  if (scroller && previousScroll !== undefined) scroller.scrollTop = previousScroll;
  else if (scroller && endAnchor) scroller.scrollTop += endAnchor.getBoundingClientRect().bottom - scroller.getBoundingClientRect().bottom + 16;
  else if (scroller && anchor) scroller.scrollTop += anchor.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 8;
}

// ---------- Gallery ----------

function galleryGenerations() {
  const items = [];
  const c = view.check;
  if (c?.status === "passed" && c.purpose === "generate") {
    for (let index = 0; index < c.queuedCount; index += 1) {
      items.push({ id: `queued-${index}`, status: "queued", display_artifact: null, workflow_display_name: SOURCE.display_name });
    }
  }
  for (const item of [...(c?.attempts || [])].reverse()) {
    if (item.deleted || item.status === "composing") continue;
    if (!item.image) {
      if (item.status === "generating") items.push({ id: `probe-${item.number}`, status: "running", display_artifact: null, workflow_display_name: SOURCE.display_name });
      continue;
    }
    items.push({
      id: `probe-${item.number}`,
      status: "succeeded",
      workflow_display_name: SOURCE.display_name,
      display_artifact: { id: `artifact-${item.number}`, kind: "image", thumbnail_url: IMAGES[item.image].src, content_url: IMAGES[item.image].src, width: 320, height: 320 },
      tone: IMAGES[item.image].tone,
    });
  }
  items.push({
    id: "earlier",
    status: "succeeded",
    workflow_display_name: SOURCE.display_name,
    display_artifact: { id: "artifact-earlier", kind: "image", thumbnail_url: IMAGES.orchid.src, content_url: IMAGES.orchid.src, width: 320, height: 320 },
  });
  return items;
}

function renderGallery() {
  const gallery = document.querySelector("#gallery");
  if (!gallery) return;
  const items = galleryGenerations();
  gallery.innerHTML = items.map((item) => galleryCardMarkup(item)).join("");
  for (const item of items) {
    const img = gallery.querySelector(`[data-generation-id="${item.id}"] img[data-thumbnail-src]`);
    if (!img) continue;
    img.src = img.dataset.thumbnailSrc;
    img.dataset.thumbnailState = "ready";
    if (item.tone) img.classList.add(item.tone);
  }
  const viewport = document.querySelector("#gallery-viewport");
  if (viewport && !viewport.querySelector(".xc-scene-heading")) {
    viewport.insertAdjacentHTML("afterbegin", '<div class="xc-scene-heading"><div><h1>Gallery</h1><p>Attempt images land in the current folder like any other image.</p></div></div>');
  }
  document.querySelector("#gallery-sentinel")?.setAttribute("hidden", "");
}

// ---------- Dialog ----------

function scoreRowMarkup(result, threshold) {
  const fillClass = result.score >= threshold ? "is-met" : result.score < 50 ? "is-low" : "";
  return `<li class="expectation-score-row">
    <span class="expectation-mark ${result.met ? "is-met" : "is-unmet"}" aria-label="${result.met ? "Met" : "Not met"}">${result.met ? ICONS.check : ICONS.cross}</span>
    <span class="expectation-score-text">${escapeHtml(result.expectation)}</span>
    <span class="expectation-bar" role="img" aria-label="Score ${result.score} of 100, pass score ${threshold}"><span class="expectation-bar-fill ${fillClass}" style="width:${result.score}%"></span><span class="expectation-bar-threshold" style="left:calc(${threshold}% - 1px)"></span></span>
    <span class="expectation-score-value">${result.score}</span>
  </li>`;
}

function pendingScoreRowMarkup(expectation) {
  return `<li class="expectation-score-row"><span class="expectation-mark is-pending" aria-label="Not scored yet">${ICONS.dot}</span><span class="expectation-score-text">${escapeHtml(expectation)}</span><span class="expectation-bar" aria-hidden="true"></span><span class="expectation-score-value">—</span></li>`;
}

function stepperMarkup(c) {
  const steps = [];
  for (let number = 1; number <= c.maxAttempts; number += 1) {
    const item = c.attempts.find((entry) => entry.number === number);
    const current = c.status === "running" && item === currentAttempt(c);
    const stateClass = !item ? "" : current ? "is-current" : item.status === "passed" ? "is-passed" : c.best === number ? "is-not-met is-best" : item.status === "not_met" ? "is-not-met" : ["failed", "stopped"].includes(item.status) ? "is-failed" : "";
    const label = !item ? "not started" : current ? ATTEMPT_LABEL[item.status].toLowerCase() : ATTEMPT_LABEL[item.status].toLowerCase();
    if (number > 1) steps.push('<li class="expectation-step-connector" aria-hidden="true"></li>');
    steps.push(`<li class="expectation-step ${stateClass}" aria-label="Attempt ${number}: ${label}">${item?.status === "passed" ? ICONS.check : number}</li>`);
  }
  return `<ol class="expectation-stepper" aria-label="Attempts">${steps.join("")}</ol>`;
}

function attemptMarkup(item, c) {
  const current = c.status === "running" && item === currentAttempt(c);
  const best = c.best === item.number;
  const classes = ["expectation-attempt", current ? "is-current" : "", item.status === "passed" ? "is-passed" : "", best ? "is-best" : ""].join(" ");
  const pillClass = item.status === "passed" ? "is-passed" : item.status === "not_met" ? "is-not-met" : ["failed", "stopped"].includes(item.status) ? "is-failed" : "";
  const media = item.image
    ? `<button type="button" class="expectation-attempt-media" data-xc-action="open-image" aria-label="View attempt ${item.number} image"><img src="${IMAGES[item.image].src}" class="${IMAGES[item.image].tone}" alt="Attempt ${item.number} image" /></button>`
    : `<div class="expectation-attempt-media is-pending" aria-hidden="true">${current ? '<span class="activity-spinner"></span>' : ""}<span>${escapeHtml(item.status === "stopped" ? "Stopped" : item.status === "failed" ? "No score" : ATTEMPT_LABEL[item.status])}</span></div>`;
  const results = item.results
    ? `<ul class="expectation-results">${item.results
        .map(
          (result) => `<li class="expectation-result"><span class="expectation-mark ${result.met ? "is-met" : "is-unmet"}" aria-label="${result.met ? "Met" : "Not met"}">${result.met ? ICONS.check : ICONS.cross}</span><span class="expectation-result-copy"><strong>${escapeHtml(result.expectation)}</strong><span>${escapeHtml(result.observation)}</span></span><span class="expectation-result-score">${result.score}</span></li>`,
        )
        .join("")}</ul>`
    : "";
  const prompt = item.prompt
    ? `<details class="expectation-attempt-prompt" ${current || item.status === "passed" || best ? "open" : ""}><summary>Prompt</summary><p>${escapeHtml(item.prompt)}</p></details>`
    : "";
  const error = item.status === "failed" && c.error ? `<p class="prompt-assistant-error" role="alert">${escapeHtml(c.error)}</p>` : "";
  return `<li class="${classes}">
    ${media}
    <div class="expectation-attempt-body">
      <div class="expectation-attempt-heading">
        <h3>Attempt ${item.number}</h3>
        <span class="expectation-pill ${pillClass}">${current ? '<span class="activity-spinner" aria-hidden="true"></span>' : ""}${escapeHtml(best ? "Best" : ATTEMPT_LABEL[item.status])}</span>
        ${item.score !== null ? `<span class="expectation-attempt-score"><b>${item.score}</b> / 100</span>` : ""}
      </div>
      ${prompt}
      ${results}
      ${item.summary ? `<p class="expectation-summary">“${escapeHtml(item.summary)}”</p>` : ""}
      ${error}
    </div>
  </li>`;
}

function bannerMarkup(c) {
  const final = c.attempts.find((item) => item.status === "passed");
  const best = latestScored(c);
  const current = currentAttempt(c);
  if (c.status === "passed") {
    const tail = c.purpose === "generate"
      ? `That image is the first of your ${c.planned}; <strong>${c.queuedCount} more</strong> were queued with the qualified prompt.`
      : "The qualified prompt was applied to the Prompt field.";
    return `<p class="expectation-banner is-passed"><span class="expectation-banner-icon" aria-hidden="true">${ICONS.check}</span><span><strong>Passed on attempt ${final.number} with ${final.score}/100.</strong> ${tail}</span></p>`;
  }
  if (c.status === "not_met") {
    return `<p class="expectation-banner is-not-met"><span class="expectation-banner-icon" aria-hidden="true">${ICONS.notMet}</span><span><strong>Expectations weren't met after ${c.attempts.length} attempts.</strong> The best was attempt ${best.number} at ${best.score}/100 (pass score ${c.threshold}). Nothing else was queued.</span></p>`;
  }
  if (c.status === "failed") {
    return `<p class="expectation-banner is-failed"><span class="expectation-banner-icon" aria-hidden="true">${ICONS.alert}</span><span><strong>The check stopped with an error on attempt ${current.number}.</strong> ${escapeHtml(c.error)} Images already generated are kept.</span></p>`;
  }
  if (c.status === "stopped") {
    return `<p class="expectation-banner is-stopped"><span class="expectation-banner-icon" aria-hidden="true">${ICONS.stop}</span><span><strong>You stopped this check during attempt ${current.number}.</strong> An attempt image that was already queued will still finish.</span></p>`;
  }
  return "";
}

function footerMarkup(c) {
  if (confirmDelete) {
    const count = failedAttempts(c).length;
    return `<div class="expectation-confirm" role="alert"><span>Delete ${count} attempt ${count === 1 ? "image" : "images"} from the gallery?</span></div>
      <div class="expectation-footer-buttons"><button type="button" class="button secondary" data-xc-action="cancel-delete">Cancel</button><button type="button" class="button destructive" data-xc-action="confirm-delete">Delete</button></div>`;
  }
  if (c.status === "running") {
    return `<p class="expectation-footer-note">Runs on the server — closing this window doesn't stop it.</p>
      <div class="expectation-footer-buttons"><button type="button" class="button destructive" data-xc-action="stop">Stop</button><button type="button" class="button secondary" data-xc-action="close">Hide</button></div>`;
  }
  const failed = failedAttempts(c).filter((item) => !item.deleted);
  const deleteButton = failed.length
    ? `<button type="button" class="button low" data-xc-action="delete-failed">${c.status === "not_met" ? `Delete other attempts (${failed.length})` : `Delete failed attempts (${failed.length})`}</button>`
    : "";
  const final = c.attempts.find((item) => item.status === "passed");
  const best = latestScored(c);
  const latest = [...c.attempts].reverse().find((item) => item.prompt);
  const useButton = (item, label, tone) => {
    const applied = view.panel.prompt === item.prompt;
    return `<button type="button" class="button ${tone}" data-xc-action="use-prompt" data-attempt="${item.number}" ${applied ? "disabled" : ""}>${applied ? `${ICONS.check} Prompt applied` : label}</button>`;
  };
  let use = "";
  if (c.status === "passed") use = useButton(final, "Use this prompt", "secondary");
  else if (c.status === "not_met" && best) use = useButton(best, `Use best prompt (${best.score})`, "primary");
  else if (latest) use = useButton(latest, "Use latest prompt", "secondary");
  const note = {
    passed: "Attempt images stay in the gallery until you delete them.",
    not_met: "Adjust the expectations, pass score, or direction and try again.",
    failed: "Fix the cause, then start again with Apply & verify.",
    stopped: "Start again with Apply & verify when you're ready.",
  }[c.status];
  return `<p class="expectation-footer-note">${escapeHtml(note)}</p>
    <div class="expectation-footer-buttons">${deleteButton}${use}<button type="button" class="button ${c.status === "not_met" ? "secondary" : "primary"}" data-xc-action="close">Close</button></div>`;
}

function dialogMarkup() {
  const c = view.check;
  if (!c) return "";
  const pillClass = STATUS_CLASS[c.status] || "";
  const scored = latestScored(c);
  const scoreboardTitle = !scored ? "Expectations" : c.best ? `Best scores · attempt ${scored.number}` : `Latest scores · attempt ${scored.number}`;
  const rows = scored ? scored.results.map((result) => scoreRowMarkup(result, c.threshold)).join("") : EXPECTATIONS.map(pendingScoreRowMarkup).join("");
  const purpose = c.purpose === "generate" ? `Generate · ${c.planned} images` : "Apply";
  return `<div class="dialog-frame expectation-check-frame">
    <header class="dialog-header expectation-check-header">
      <div>
        <h2 id="expectation-check-title">Verify expectations <span class="expectation-pill ${pillClass}">${c.status === "running" ? '<span class="activity-spinner" aria-hidden="true"></span>' : ""}${escapeHtml(c.status === "running" ? ATTEMPT_LABEL[currentAttempt(c).status] : STATUS_LABEL[c.status])}</span></h2>
        <p>Creative Direction · Refine · ${purpose} · pass score ${c.threshold} · up to ${c.maxAttempts} attempts</p>
      </div>
      <button type="button" class="icon-button expectation-check-close" data-xc-action="close" aria-label="Close">×</button>
    </header>
    <div class="expectation-check-content" tabindex="-1">
      ${bannerMarkup(c)}
      <section class="expectation-progress" aria-label="Progress">
        ${stepperMarkup(c)}
        ${c.status === "running" ? `<p class="expectation-phase"><span class="activity-spinner" aria-hidden="true"></span><span><strong>Attempt ${currentAttempt(c).number} of ${c.maxAttempts}</strong> · ${escapeHtml(phaseCopy(c))}</span></p>` : ""}
      </section>
      <section aria-labelledby="xc-scoreboard-title">
        <h3 class="expectation-section-title" id="xc-scoreboard-title">${escapeHtml(scoreboardTitle)}</h3>
        <ul class="expectation-scoreboard">${rows}</ul>
      </section>
      <section aria-labelledby="xc-attempts-title">
        <h3 class="expectation-section-title" id="xc-attempts-title">Attempts · newest first</h3>
        <ol class="expectation-attempts" reversed>${[...c.attempts].reverse().map((item) => attemptMarkup(item, c)).join("")}</ol>
      </section>
    </div>
    <footer class="dialog-actions expectation-check-footer">${footerMarkup(c)}</footer>
  </div>`;
}

function renderDialog() {
  const content = dialog.querySelector(".expectation-check-content");
  const scroll = content?.scrollTop || 0;
  dialog.innerHTML = dialogMarkup();
  const next = dialog.querySelector(".expectation-check-content");
  if (next) next.scrollTop = scroll;
}

function openDialog() {
  if (!view.check) {
    toast("Start a check with Apply & verify or Generate first.");
    return;
  }
  renderDialog();
  if (!dialog.open) {
    dialog.showModal();
    // Land focus on the live content rather than the close button.
    dialog.querySelector(".expectation-check-content")?.focus({ preventScroll: true });
  }
}

// ---------- Rendering and interactions ----------

function toast(message, tone = "") {
  const region = document.querySelector("#toast-region");
  if (!region) return;
  const item = document.createElement("div");
  item.className = `toast ${tone}`.trim();
  item.textContent = message;
  region.append(item);
  setTimeout(() => item.remove(), 2800);
}

function renderAll() {
  renderPanel();
  renderGallery();
  if (dialog.open) renderDialog();
}

function renderShell() {
  const wasOpen = document.querySelector(".app-shell")?.classList.contains("panel-open");
  app.innerHTML = shellMarkup(appState());
  // The scene owns its own dialog; drop the shell's empty ones to keep focus order simple.
  app.querySelectorAll("dialog").forEach((element) => element.remove());
  if (wasOpen) document.querySelector(".app-shell")?.classList.add("panel-open");
}

function stopSimulation() {
  if (simulation) clearInterval(simulation);
  simulation = null;
}

function setScenario(name, { openModal = false } = {}) {
  stopSimulation();
  confirmDelete = false;
  statusDismissed = false;
  const factory = SCENARIOS[name] || SCENARIOS.configured;
  view = factory();
  view.name = name;
  renderShell();
  renderAll();
  if (dialog.open && !view.check) dialog.close();
  if (openModal && view.check) openDialog();
}

function simulate(purpose = "apply") {
  stopSimulation();
  confirmDelete = false;
  statusDismissed = false;
  const panel = { ...view.panel, prompt: STARTING_PROMPT, quantity: purpose === "generate" ? 3 : view.panel.quantity };
  const keys = ["first", "second", "third"];
  const frames = [];
  const done = [];
  keys.forEach((key, index) => {
    const number = index + 1;
    for (const status of ["composing", "generating", "evaluating"]) frames.push([...done, attempt(number, key, 80, status)]);
    done.push(attempt(number, key, 80, index === keys.length - 1 ? "passed" : "not_met"));
  });
  frames.push(done);
  let frame = 0;
  view = { name: "simulation", panel, check: check("running", purpose, frames[0]) };
  renderAll();
  openDialog();
  simulation = setInterval(() => {
    frame += 1;
    const finished = frame >= frames.length - 1;
    view.check = check(finished ? "passed" : "running", purpose, frames[Math.min(frame, frames.length - 1)], finished && purpose === "generate" ? { queuedCount: 2 } : {});
    if (finished) {
      stopSimulation();
      view.panel.prompt = LIBRARY.third.prompt;
      toast(purpose === "generate" ? "Expectations met. 2 more images were queued." : "Expectations met. The qualified prompt was applied.", "success");
    }
    renderAll();
  }, 1300);
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action], [data-xc-action]");
  if (!target) return;
  const action = target.dataset.xcAction || target.dataset.action;
  if (action === "toggle-control-section") {
    const section = target.closest(".control-section");
    const open = !section.classList.contains("is-expanded");
    section.classList.toggle("is-expanded", open);
    target.setAttribute("aria-expanded", String(open));
    const body = section.querySelector(".control-section-body");
    body?.setAttribute("aria-hidden", String(!open));
    body?.toggleAttribute("inert", !open);
    view.sections = { ...(view.sections || {}), [section.dataset.controlSection]: open };
  } else if (action === "toggle-panel") {
    const shell = document.querySelector(".app-shell");
    shell.classList.toggle("panel-open");
    target.setAttribute("aria-expanded", String(shell.classList.contains("panel-open")));
  } else if (action === "close-panel") {
    document.querySelector(".app-shell")?.classList.remove("panel-open");
  } else if (action === "compose-prompt" || action === "generate") {
    if (!expectationsActive(view.panel)) {
      toast(action === "generate" ? "Generate runs as usual when expectations are off." : "Creative Direction applied without a vision check.");
      return;
    }
    simulate(action === "generate" ? "generate" : "apply");
  } else if (action === "view-check") {
    openDialog();
  } else if (action === "dismiss-status") {
    statusDismissed = true;
    renderPanel();
  } else if (action === "close") {
    confirmDelete = false;
    dialog.close();
  } else if (action === "stop") {
    stopSimulation();
    const current = currentAttempt(view.check);
    view.check = { ...view.check, status: "stopped", attempts: [...view.check.attempts.slice(0, -1), { ...current, status: "stopped" }] };
    renderAll();
    toast("Check stopped.");
  } else if (action === "use-prompt") {
    const chosen = view.check.attempts.find((item) => item.number === Number(target.dataset.attempt));
    view.panel.prompt = chosen.prompt;
    renderAll();
    toast(`Attempt ${chosen.number}'s prompt was placed in the Prompt field.`, "success");
  } else if (action === "delete-failed") {
    confirmDelete = true;
    renderDialog();
  } else if (action === "cancel-delete") {
    confirmDelete = false;
    renderDialog();
  } else if (action === "confirm-delete") {
    const removed = failedAttempts(view.check).filter((item) => !item.deleted);
    removed.forEach((item) => { item.deleted = true; });
    confirmDelete = false;
    renderAll();
    toast(`${removed.length} attempt ${removed.length === 1 ? "image was" : "images were"} deleted.`, "success");
  } else if (action === "open-image") {
    toast("Opens the full image in the photo viewer.");
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "expectations-enabled") {
    view.panel.expectationsEnabled = event.target.checked;
    renderPanel();
  } else if (event.target.id === "expectations-threshold") {
    view.panel.threshold = Number(event.target.value) || 80;
  } else if (event.target.id === "expectations-attempts") {
    view.panel.maxAttempts = Number(event.target.value) || 5;
    renderPanel();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id !== "creative-direction-expectations") return;
  view.panel.text = event.target.value;
  const count = expectationLines(view.panel.text).length;
  const counter = document.querySelector("[data-xc-count]");
  if (counter) counter.textContent = `${count} of ${MAX_EXPECTATIONS} expectations`;
  const badge = document.querySelector("[data-xc-badge]");
  if (badge && view.panel.expectationsEnabled) badge.textContent = `On · ${count}`;
});

document.addEventListener(
  "toggle",
  (event) => {
    if (event.target.matches?.("[data-xc-expectations]")) view.panel.expectationsOpen = event.target.open;
  },
  true,
);

dialog.addEventListener("close", () => {
  confirmDelete = false;
});

window.previewScenarios = Object.keys(SCENARIOS);
window.previewSetScenario = (name, options) => setScenario(name, options);
window.previewOpenDialog = openDialog;
window.previewSimulate = (purpose) => simulate(purpose);
window.previewReset = () => setScenario(view?.name in SCENARIOS ? view.name : "configured");
window.previewSetViewport = (mode) => {
  document.querySelector(".app-shell")?.classList.toggle("panel-open", mode === "mobile");
};

const params = new URLSearchParams(location.hash.slice(1));
// Screenshot aid: let the page grow instead of scrolling inside the panel.
if (params.get("tall") === "1") document.documentElement.classList.add("xc-tall");
setScenario(params.get("scenario") || "configured", { openModal: params.get("modal") === "1" });
if (params.get("anchor") || params.get("end")) {
  view.anchor = params.get("anchor");
  view.anchorEnd = params.get("end") === "status" ? ".expectation-status" : params.get("end") === "apply" ? '[data-action="compose-prompt"]' : null;
  renderShell();
  renderAll();
}
if (params.get("viewport")) window.previewSetViewport(params.get("viewport"));
else window.previewSetViewport(matchMedia("(max-width: 800px)").matches ? "mobile" : "desktop");
if (params.get("simulate")) simulate(params.get("simulate"));
