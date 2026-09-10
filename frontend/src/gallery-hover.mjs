const CARD_SELECTOR = "[data-gallery-card]";
const REVEAL_DELAY_MS = 450;
const EXIT_DELAY_MS = 150;
const MOVEMENT_TOLERANCE = 6;

// One delegated controller also covers cards added by pagination and live updates.
export function bindGalleryCardHover(root) {
  const document = root.ownerDocument;
  const window = document.defaultView;
  let card = null;
  let pointer = null;
  let anchor = null;
  let revealTimer = null;
  let exitTimer = null;

  const cardAt = (element) => {
    const candidate = element?.closest?.(CARD_SELECTOR);
    return candidate && root.contains(candidate) ? candidate : null;
  };
  const underPointer = () => pointer && cardAt(document.elementFromPoint(pointer.x, pointer.y));

  function reset() {
    window.clearTimeout(revealTimer);
    window.clearTimeout(exitTimer);
    card?.classList.remove("card-controls-visible");
    card = null;
    anchor = null;
    pointer = null;
  }

  function schedule() {
    window.clearTimeout(revealTimer);
    revealTimer = window.setTimeout(() => {
      if (!card?.isConnected || underPointer() !== card || document.hidden) {
        reset();
        return;
      }
      card.classList.add("card-controls-visible");
    }, REVEAL_DELAY_MS);
  }

  function track(event, fromFocus = false) {
    if (!["mouse", "pen"].includes(event.pointerType) || (event.buttons && !fromFocus)) return;
    const target = cardAt(event.target);
    if (!target) return;
    const position = { x: event.clientX, y: event.clientY };
    window.clearTimeout(exitTimer);
    if (target !== card) {
      reset();
      card = target;
      pointer = position;
      anchor = position;
      if (target.matches(":has(:focus-visible)")) card.classList.add("card-controls-visible");
      else schedule();
      return;
    }
    pointer = position;
    if (target.matches(":has(:focus-visible)")) {
      window.clearTimeout(revealTimer);
      card.classList.add("card-controls-visible");
    }
    if (card.classList.contains("card-controls-visible")) return;
    if (!anchor || Math.hypot(pointer.x - anchor.x, pointer.y - anchor.y) > MOVEMENT_TOLERANCE) {
      anchor = position;
      schedule();
    }
  }

  // A real pointer move is required: layout changes and scrolling can generate
  // pointerover events even while the mouse is stationary.
  root.addEventListener("pointermove", track);
  root.addEventListener("pointerdown", (event) => {
    // Keep already-visible keyboard controls under the pointer when mousedown
    // changes :focus-visible, including after focus returns from a dialog.
    if (cardAt(event.target)?.matches(":has(:focus-visible)")) track(event, true);
  });
  root.addEventListener("pointerout", (event) => {
    if (!card || cardAt(event.target) !== card || cardAt(event.relatedTarget) === card) return;
    window.clearTimeout(revealTimer);
    anchor = null;
    exitTimer = window.setTimeout(reset, EXIT_DELAY_MS);
  });
  root.addEventListener("pointercancel", reset);
  root.addEventListener("dragstart", reset);
  // Scrolling through cards must not count as deliberate hover.
  root.addEventListener("scroll", reset, true);
  window.addEventListener("blur", reset);
  document.addEventListener("visibilitychange", reset);

  function sameCard(left, right) {
    return Boolean(left && right &&
      left.dataset.galleryCard === right.dataset.galleryCard &&
      left.dataset.generationId === right.dataset.generationId &&
      left.dataset.collectionId === right.dataset.collectionId);
  }

  return {
    preserveDuring(update) {
      const focused = document.activeElement;
      const focusedCard = cardAt(focused);
      const visible = card?.classList.contains("card-controls-visible");
      update();
      if (card && !card.isConnected) {
        const replacement = underPointer();
        if (sameCard(card, replacement)) {
          card = replacement;
          card.classList.toggle("card-controls-visible", visible);
        } else reset();
      }
      // Preserve keyboard position when a favorite or preview toggle redraws a card.
      if (focusedCard && !focused.isConnected && document.activeElement === document.body) {
        const replacement = [...root.querySelectorAll(CARD_SELECTOR)]
          .find((candidate) => sameCard(focusedCard, candidate));
        const selector = focused.dataset.action
          ? `[data-action="${window.CSS.escape(focused.dataset.action)}"]`
          : "a.download-button";
        replacement?.querySelector(selector)?.focus({ preventScroll: true });
      }
    },
  };
}
