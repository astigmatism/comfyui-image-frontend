import test from "node:test";
import assert from "node:assert/strict";
import { checkpointRank, moveCheckpointRank, normalizeCheckpointRanks, normalizeCheckpointTierLayout } from "../src/lib.mjs";
import { galleryCardMarkup, photoViewerMarkup } from "../src/render.mjs";
import { createSettingsSync, mergeSettings } from "../src/user-settings.mjs";

const first = "cp1_" + "a".repeat(64), second = "cp1_" + "b".repeat(64);

test("checkpoint identity shares grades across aliases without joining identical labels", () => {
  const ranks = moveCheckpointRank({}, first, "A");
  for (const alias of ["first-workflow-alias", "another-alias"]) {
    assert.deepEqual(normalizeCheckpointTierLayout({ choices: [
      { value: alias, label: "Same label", checkpoint_id: first },
      { value: "other-model", label: "Same label", checkpoint_id: second },
    ] }, ranks), { A: [alias], B: [], C: ["other-model"], D: [], F: [] });
  }
  assert.equal(checkpointRank(second, ranks), "C");
});

test("moving and ordering checkpoints preserves other ranks and defaults legacy caches to C", () => {
  const ranks = { A: [first], B: [second], C: [], D: [], F: [] };
  const next = moveCheckpointRank(ranks, second, "A", first);
  assert.deepEqual(next.A, [second, first]);
  assert.deepEqual(next.B, []);
  assert.deepEqual(ranks.A, [first]);
  assert.deepEqual(moveCheckpointRank(next, first, "A", first), next);
  const reset = normalizeCheckpointRanks({ oldSource: { checkpoint: { top_picks: ["alias"] } } });
  assert.equal(checkpointRank(first, reset), "C");
  assert.deepEqual(normalizeCheckpointRanks({ A: [first], B: [first, second] }).B, [second]);
});

test("letter badges show current rank on historical images and enforce fullscreen limits", () => {
  const generation = { id: "historical-image", checkpoint_id: first, checkpoint_label: "Model <long>", status: "succeeded" };
  for (const grade of ["A", "B", "C", "D", "F"]) {
    const checkpointTiers = { [grade]: [first] };
    const gallery = galleryCardMarkup(generation, { checkpointTiers });
    const viewer = photoViewerMarkup(generation, {}, "fit", "slideshow", { checkpointTiers });
    assert.match(gallery, new RegExp(`aria-label="Tier ${grade}"`));
    assert.match(viewer, new RegExp(`aria-label="Tier ${grade}"`));
    assert.doesNotMatch(viewer, /checkpoint-rank-face|🤩|🙂|😐|🙁|😖/);
    assert.match(viewer, /Model &lt;long&gt;/);
    const up = viewer.match(/<button[^>]+data-rank-step="-1"[^>]*>/)[0];
    const down = viewer.match(/<button[^>]+data-rank-step="1"[^>]*>/)[0];
    assert.equal(up.includes("disabled"), grade === "A");
    assert.equal(down.includes("disabled"), grade === "F");
    assert.match(viewer, /data-photo-view-mode="fit"/);
    assert.match(viewer, /data-photo-toggle-state="slideshow"/);
  }
});

test("concurrent checkpoint moves conflict as one board instead of duplicating identities", () => {
  const base = { checkpoint_tiers: normalizeCheckpointRanks({}, [{ choices: [{ checkpoint_id: first }] }]) };
  const local = { checkpoint_tiers: moveCheckpointRank(base.checkpoint_tiers, first, "B") };
  const remote = { checkpoint_tiers: moveCheckpointRank(base.checkpoint_tiers, first, "D") };
  const merged = mergeSettings(base, local, remote);
  assert.deepEqual(merged.conflicts, ["checkpoint_tiers"]);
  assert.deepEqual(merged.value, local);
});

test("immediate save reports failures and retries an unrelated revision change", async () => {
  let local, fail = false, conflict = false;
  let server = { revision: 1, settings_initialized: true, settings: { quantity: 1 }, gallery_scale: 45, checkpoint_tiers: {} };
  const controller = new AbortController();
  const sync = createSettingsSync({ signal: controller.signal, read: () => structuredClone(local),
    apply: value => { local = value; }, status: () => {},
    api: async (_path, options) => {
      if (options.method === "PUT") {
        if (fail) throw new Error("Save failed");
        if (conflict) {
          conflict = false; server.revision++; server.settings.quantity = 2;
          throw Object.assign(new Error("Conflict"), { status: 409 });
        }
        const sent = JSON.parse(options.body);
        assert.equal(sent.expected_revision, server.revision);
        server = { ...server, ...sent, revision: server.revision + 1 };
      }
      return structuredClone(server);
    },
  });
  try {
    await sync.load();
    local.checkpoint_tiers = { B: [first] };
    fail = true;
    assert.equal(await sync.save(), false);
    assert.deepEqual(server.checkpoint_tiers, {});
    fail = false; conflict = true;
    assert.equal(await sync.save(), true);
    assert.deepEqual(server.checkpoint_tiers, { B: [first] });
    assert.equal(server.settings.quantity, 2);
  } finally { controller.abort(); }
});
