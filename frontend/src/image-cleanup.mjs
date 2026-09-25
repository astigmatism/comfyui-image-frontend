export function releaseImage(image) {
  if (!image) return;
  image.removeAttribute("src");
  // Never-mounted Images can retain Chromium viewport listeners after src is
  // cleared. A detached fragment runs removal cleanup without showing the image.
  const fragment = image.ownerDocument?.createDocumentFragment();
  if (fragment) { fragment.append(image); fragment.removeChild(image); }
}
