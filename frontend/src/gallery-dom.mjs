// Reconcile gallery markup without discarding loaded images or card identity.
// Card keys are global to this update: prompt metadata can move a card to a
// different group, including replacing a provisional group ID with its real ID.
const cardKey = (node) => node?.nodeType === 1 && node.dataset.galleryCard
  ? `${node.dataset.galleryCard}:${node.dataset.generationId || node.dataset.collectionId}` : null;
const runtimeNode = (node) => node.nodeType === 1 && node.hasAttribute("data-thumbnail-retry");

function localKey(node) {
  if (node.nodeType !== 1) return `node:${node.nodeType}`;
  if (cardKey(node)) return `card:${cardKey(node)}`;
  if (node.hasAttribute("data-prompt-group")) return `group:${node.dataset.promptGroup}`;
  if (node.matches("img[data-thumbnail-src]")) return `image:${node.dataset.galleryArtifactId || ""}:${node.dataset.thumbnailSrc}`;
  if (node.dataset.action) return `action:${node.dataset.action}:${node.dataset.direction || node.dataset.photoViewMode || node.dataset.photoPlaybackMode || ""}`;
  for (const attribute of ["data-group-toggle", "data-group-changes", "data-prompt-group-select", "data-group-more"]) {
    if (node.hasAttribute(attribute)) return attribute;
  }
  return `${node.tagName}:${node.getAttribute("class")?.split(/\s+/)[0] || node.id || ""}`;
}

function patchAttributes(current, desired) {
  const thumbnail = current.matches("img[data-thumbnail-src]");
  const selected = current.matches('[data-action="select-gallery-card"], [data-prompt-group-select]');
  const owned = (name) => (thumbnail && ["src", "data-thumbnail-state", "draggable"].includes(name))
    || (selected && name === "aria-checked")
    || (current.dataset.action === "select-gallery-card" && name === "title");
  for (const { name } of [...current.attributes]) {
    if (!owned(name) && !desired.hasAttribute(name)) current.removeAttribute(name);
  }
  for (const { name, value } of desired.attributes) {
    if (owned(name)) continue;
    let next = value;
    if (name === "class" && cardKey(current)) {
      const classes = new Set(value.split(/\s+/));
      for (const runtime of ["is-selected", "card-controls-visible", "is-dragging"]) {
        if (current.classList.contains(runtime)) classes.add(runtime);
      }
      next = [...classes].join(" ");
    }
    if (current.getAttribute(name) !== next) current.setAttribute(name, next);
  }
}

function reconcile(parent, desiredParent, context) {
  const previous = [...parent.childNodes].filter((node) => !runtimeNode(node));
  const kept = new Set();
  let position = parent.firstChild;
  for (const desired of [...desiredParent.childNodes]) {
    let current;
    const key = cardKey(desired);
    if (key) current = context.cards.get(key);
    else if (desired.nodeType === 1 && desired.hasAttribute("data-prompt-group")) {
      current = context.groups.get(desired.dataset.promptGroup);
      if (!current || context.used.has(current)) {
        // Match the old section by a surviving member when the server resolves
        // its ID, or pagination joins a run spanning the previous page boundary.
        current = [...desired.querySelectorAll("[data-gallery-card]")]
          .map((card) => context.cards.get(cardKey(card))?.closest("[data-prompt-group]"))
          .find((group) => group && !context.used.has(group));
      }
    } else if (desired.nodeType === 1 && desired.matches("img[data-thumbnail-src]")) {
      const owner = cardKey(desired.closest("[data-gallery-card]"));
      current = context.images.get(owner)?.find((node) => !context.used.has(node) && localKey(node) === localKey(desired));
    } else current = previous.find((node) => !kept.has(node) && localKey(node) === localKey(desired));
    if (current && (context.used.has(current) || current.nodeType !== desired.nodeType || current.nodeName !== desired.nodeName)) current = null;
    if (!current) current = desired.cloneNode(false);
    context.used.add(current);
    kept.add(current);
    if (current !== position) parent.insertBefore(current, position);
    if (current.nodeType === 1) {
      patchAttributes(current, desired);
      if (!(context.preserveMedia && current.matches(".photo-viewer-media"))) reconcile(current, desired, context);
    } else if (current.nodeValue !== desired.nodeValue) current.nodeValue = desired.nodeValue;
    position = current.nextSibling;
  }
  for (const node of previous) if (!kept.has(node) && node.parentNode === parent) node.remove();
}

function contextFor(root) {
  const cards = [...root.querySelectorAll("[data-gallery-card]")];
  if (cardKey(root)) cards.unshift(root);
  return {
    cards: new Map(cards.map((node) => [cardKey(node), node])),
    images: new Map(cards.map((node) => [cardKey(node), [...node.querySelectorAll("img[data-thumbnail-src]")]])),
    groups: new Map([...root.querySelectorAll("[data-prompt-group]")].map((node) => [node.dataset.promptGroup, node])),
    used: new Set(),
  };
}

export function reconcileGallery(root, markup) {
  const template = root.ownerDocument.createElement("template");
  template.innerHTML = markup;
  reconcile(root, template.content, contextFor(root));
}

export function reconcileGalleryCard(card, markup) {
  const template = card.ownerDocument.createElement("template");
  template.innerHTML = markup;
  const desired = template.content.firstElementChild;
  if (cardKey(card) !== cardKey(desired)) throw new Error("Cannot reconcile different gallery cards.");
  patchAttributes(card, desired);
  reconcile(card, desired, contextFor(card));
}

export function reconcilePhotoViewer(root, markup, image) {
  const template = root.ownerDocument.createElement("template");
  template.innerHTML = markup;
  reconcile(root, template.content, { ...contextFor(root), preserveMedia: true });
  const media = root.querySelector(".photo-viewer-media");
  if (image && media.firstChild !== image) media.replaceChildren(image);
  const description = template.content.querySelector(".photo-viewer-media img");
  if (image && description) image.alt = description.alt;
}
