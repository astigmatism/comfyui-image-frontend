// HTTP Date has one-second precision. Estimate its midpoint using the request's
// round trip, and retain the lowest-latency sample for new countdown anchors.
let clockSample = null;

export function observeServerDate(value, sentAt, receivedAt) {
  if (typeof value !== "string") return;
  const serverAt = Date.parse(value);
  const roundTrip = receivedAt - sentAt;
  if (!Number.isFinite(serverAt) || !Number.isFinite(roundTrip) || roundTrip < 0) return;
  if (clockSample && roundTrip >= clockSample.roundTrip) return;
  clockSample = { roundTrip, offset: (sentAt + receivedAt) / 2 - (serverAt + 500) };
}

export function serverClockOffset() {
  return clockSample?.offset ?? null;
}

export function resetServerClock() {
  clockSample = null;
}
