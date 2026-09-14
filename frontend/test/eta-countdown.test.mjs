import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import {
  activeGenerationEta, clearGenerationEtaAnchors,
  formatNextInCountdown, generationProgressMarkup,
} from "../src/render.mjs";
import { observeServerDate, resetServerClock, serverClockOffset } from "../src/server-clock.mjs";

const start = Date.parse("2026-09-14T12:00:00Z");
const snapshot = (updated, completion, id = "countdown") => ({
  id, status: "running", progress: {
    kind: "node", label: "Sampling", value: 5, maximum: 10,
    updated_at: new Date(start + updated * 1000).toISOString(),
    eta: {
      updated_at: new Date(start + updated * 1000).toISOString(),
      completion_at: new Date(start + completion * 1000).toISOString(),
      remaining_seconds: Math.max(0, completion - updated),
    },
  },
});

beforeEach(() => {
  clearGenerationEtaAnchors();
  resetServerClock();
});

test("delayed and replayed updates cannot move an unchanged server deadline", () => {
  const initial = activeGenerationEta(snapshot(0, 40), start);
  const delayed = activeGenerationEta(snapshot(10, 40), start + 20_000);
  assert.equal(delayed.text, "About 20s left");
  assert.equal(delayed.completionTimestamp, initial.completionTimestamp);
  const current = activeGenerationEta(snapshot(21, 40), start + 21_000);
  assert.equal(current.text, "About 19s left");
  assert.equal(activeGenerationEta(snapshot(5, 90), start + 22_000).text, "About 18s left");
  assert.equal(activeGenerationEta(snapshot(22, 60), start + 23_000).text, "About 37s left");
});

test("HTTP calibration ages an old first snapshot despite an hour of clock skew", () => {
  const skew = 3_600_000;
  observeServerDate(new Date(start).toUTCString(), start + skew + 400, start + skew + 600);
  assert.equal(serverClockOffset(), skew);
  const eta = activeGenerationEta(snapshot(-20, 40), start + skew + 20_000);
  assert.equal(eta.text, "About 20s left");
  assert.equal(eta.completionTimestamp, start + skew + 40_000);
  // Later calibration samples are for new attempts; existing deadlines stay fixed.
  observeServerDate(new Date(start + 20_000).toUTCString(), start + skew + 20_400, start + skew + 20_400);
  assert.equal(activeGenerationEta(snapshot(21, 40), start + skew + 21_000).completionTimestamp, eta.completionTimestamp);
});

test("cards and slideshow share countdown rounding, overdue state, and evidence revisions", () => {
  const generation = snapshot(0, 20);
  for (const [seconds, cardText, slideText] of [
    [0, "About 20s left", "Next in 20s"],
    [10, "About 10s left", "Next in 10s"],
    [20, "Taking longer than expected", "Taking longer than expected"],
    [35, "Taking longer than expected", "Taking longer than expected"],
  ]) {
    const now = start + seconds * 1000;
    const eta = activeGenerationEta(generation, now);
    assert.equal(eta.text, cardText);
    assert.ok(generationProgressMarkup(generation, { now }).includes(cardText));
    assert.equal(formatNextInCountdown((eta.completionTimestamp - now) / 1000), slideText);
  }
  const revised = activeGenerationEta(snapshot(35, 55), start + 35_000);
  assert.equal(revised.text, "About 20s left");
  assert.equal(formatNextInCountdown((revised.completionTimestamp - start - 35_000) / 1000), "Next in 20s");
});

test("requeue and terminal states discard the old attempt's anchor", () => {
  activeGenerationEta(snapshot(100, 150), start + 100_000);
  assert.equal(activeGenerationEta({ ...snapshot(100, 150), status: "queued" }, start), null);
  assert.equal(activeGenerationEta(snapshot(0, 40), start).text, "About 40s left");
  assert.equal(activeGenerationEta({ ...snapshot(0, 40), status: "succeeded" }, start), null);
  assert.equal(activeGenerationEta(snapshot(0, 60), start).text, "About 1m 0s left");
});

test("invalid HTTP timestamps do not change clock calibration", () => {
  observeServerDate(null, start, start + 20);
  observeServerDate("invalid", start, start + 20);
  observeServerDate(new Date(start).toUTCString(), start + 20, start);
  assert.equal(serverClockOffset(), null);
});

test("slideshow selects the earliest active estimate and drops completed candidates", async () => {
  const { nextGenerationCompletionTimestamp } = await import("../src/generation-countdown.mjs");
  const fast = snapshot(0, 20, "fast");
  const slow = snapshot(0, 40, "slow");
  const queued = { ...snapshot(0, 5, "queued"), status: "queued" };
  assert.equal(nextGenerationCompletionTimestamp([slow, fast, queued], start), start + 20_000);
  assert.equal(nextGenerationCompletionTimestamp([slow, fast], start + 25_000), start + 20_000);
  fast.status = "succeeded";
  assert.equal(nextGenerationCompletionTimestamp([slow, fast], start + 25_000), start + 40_000);
  slow.status = "cancel_requested";
  assert.equal(nextGenerationCompletionTimestamp([slow, fast], start + 25_000), null);
});
