// 24-viewBox glyphs drawn inline, the icon convention used everywhere else in
// the app (render.mjs, prompt-rerun.mjs). Stroked with round caps so a single
// CSS rule styles all of them.
export const icons = {
  up: '<path d="M12 19V5M5 12l7-7 7 7" />',
  down: '<path d="M12 5v14M5 12l7 7 7-7" />',
  rename: '<path d="M4 20h4L20 8l-4-4L4 16v4Z" /><path d="M14 6l4 4" />',
  delete: '<path d="M4 7h16M9 7V4h6v3M6.5 7l1 13h9l1-13M10 11v6M14 11v6" />',
  reel: '<rect x="3" y="5" width="18" height="14" rx="2" /><path d="M8 5v14M16 5v14M3 10h5M3 14h5M16 10h5M16 14h5" />',
  more: '<path d="M12 5h.01M12 12h.01M12 19h.01" />',
  // Zero-length subpaths with round caps render as dots, so the grip matches
  // `more` instead of relying on a braille character the font may not carry.
  grip: '<path d="M9.5 6h.01M9.5 12h.01M9.5 18h.01M14.5 6h.01M14.5 12h.01M14.5 18h.01" />',
  plus: '<path d="M12 5v14M5 12h14" />',
  duplicate: '<rect x="9" y="9" width="11" height="11" rx="2" /><path d="M15 5.5H7A1.5 1.5 0 0 0 5.5 7v8" />',
  // Generation glyphs. `retry` and `rerun` and the folder glyph are the same paths
  // the selection toolbar and the folder tiles use, so one action keeps one picture
  // wherever it appears.
  retry: '<path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" />',
  rerun: '<path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /><path d="m10 9 5 3-5 3Z" />',
  folder: '<path d="M3 6.5h6l2 2H21v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /><path d="M3 9h18" />',
};

export function icon(name) {
  const glyph = icons[name];
  if (!glyph) return "";
  return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${glyph}</svg>`;
}
