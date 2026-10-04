"""One LoRA operation can change every publication of a shared library."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from test_lora_management import EmptyQueue, Stream, management, publication, safetensors

ADVANCED = "workflows/frontend/advanced.json"
MINIMAL = "workflows/frontend/minimal.json"
OUTSIDE = "workflows/frontend/outside.json"


def _files(source: str) -> list[str]:
    stem = source[:-5]
    return [source, stem + ".api.json", stem + ".interface.json"]


class LibraryOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.models = root / "loras"
        self.models.mkdir()
        self.primary_user = root / "primary" / "default"
        self.mirror_user = root / "mirror" / "default"
        self.primary_user.mkdir(parents=True)
        self.mirror_user.mkdir(parents=True)
        self.shared = {"id": "shared", "label": "Shared", "filename": "shared.safetensors"}
        (self.models / "shared.safetensors").write_bytes(safetensors())
        for source in (ADVANCED, MINIMAL):
            self._publish(source, [self.shared], node_id=906)
        self.primary = management.LoraManagement(
            self.primary_user, self.models, model_writer=True, queue=EmptyQueue()
        )
        self.mirror = management.LoraManagement(
            self.mirror_user, self.models, model_writer=False, queue=EmptyQueue()
        )

    def _publish(self, source: str, catalog: list[dict], *, node_id: int = 906) -> None:
        publication(self.primary_user, catalog, node_id=node_id, source=source)
        for name in _files(source):
            target = self.mirror_user / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.primary_user / name).read_bytes())

    def _request(self, change: dict, sources=(ADVANCED, MINIMAL)) -> dict:
        return {
            "targets": [
                {
                    "source_path": source,
                    "expected_revision": self.primary.bundle(source)["revision"],
                    "publication_id": str(uuid.uuid4()),
                }
                for source in sources
            ],
            "change": change,
            "published_at": "2026-10-04T12:00:00Z",
        }

    def _run(self, operation_id: str, payload: dict, *, remove: bool = False) -> None:
        first = self.primary.prepare(operation_id, payload)
        second = self.mirror.prepare(operation_id, payload)
        self.assertEqual(first["targets"], second["targets"])
        for target in first["targets"]:
            self.assertEqual(
                self.primary.candidate(operation_id, target["source_path"]),
                self.mirror.candidate(operation_id, target["source_path"]),
            )
        self.primary.commit(operation_id)
        self.mirror.commit(operation_id)
        if remove:
            self.primary.quarantine(operation_id)
            self.mirror.quarantine(operation_id)
        self.mirror.finalize(operation_id)
        self.primary.finalize(operation_id)

    def _stage(self, operation_id: str) -> dict:
        upload = safetensors()
        return asyncio.run(
            self.primary.stage(
                operation_id, Stream(upload), content_length=len(upload), filename="new.safetensors"
            )
        )

    def test_shared_lora_removal_was_blocked_one_workflow_at_a_time(self):
        payload = self._request({"action": "remove", "id": "shared"}, sources=(ADVANCED,))
        with self.assertRaisesRegex(management.ManagementError, "another published workflow"):
            self.primary.prepare(str(uuid.uuid4()), payload)

    def test_install_edit_remove_apply_to_every_library_publication(self):
        install_id = str(uuid.uuid4())
        staged = self._stage(install_id)
        change = {
            "action": "install",
            "id": "added",
            "label": "Added",
            "trigger_word": "added trigger",
            "filename": staged["filename"],
            "sha256": staged["sha256"],
        }
        payload = self._request(change)
        self.primary.prepare(install_id, payload)
        with self.assertRaisesRegex(management.ManagementError, "administration is active"):
            self.primary.acquire_publisher_lock(MINIMAL)
        self._run(install_id, payload)  # prepare is idempotent for the same request
        for service in (self.primary, self.mirror):
            for source in (ADVANCED, MINIMAL):
                self.assertEqual(
                    [item["id"] for item in service.bundle(source)["loras"]], ["shared", "added"]
                )
        self.assertTrue((self.models / staged["filename"]).is_file())

        edit_id = str(uuid.uuid4())
        self._run(
            edit_id,
            self._request(
                {"action": "edit", "id": "shared", "label": "Renamed", "trigger_word": "word"}
            ),
        )
        for source in (ADVANCED, MINIMAL):
            item = self.mirror.bundle(source)["loras"][0]
            self.assertEqual((item["label"], item["trigger_word"]), ("Renamed", "word"))

        remove_id = str(uuid.uuid4())
        self._run(remove_id, self._request({"action": "remove", "id": "shared"}), remove=True)
        self.assertFalse((self.models / "shared.safetensors").exists())
        for service in (self.primary, self.mirror):
            for source in (ADVANCED, MINIMAL):
                self.assertEqual(
                    [item["id"] for item in service.bundle(source)["loras"]], ["added"]
                )

    def test_removal_still_blocked_by_a_publication_outside_the_operation(self):
        self._publish(OUTSIDE, [self.shared], node_id=7)
        payload = self._request({"action": "remove", "id": "shared"})
        with self.assertRaisesRegex(management.ManagementError, "another published workflow"):
            self.primary.prepare(str(uuid.uuid4()), payload)

    def test_removal_still_blocked_by_an_authoring_workflow(self):
        authoring = self.primary_user / "workflows" / "Moody" / "draft.json"
        authoring.parent.mkdir(parents=True)
        authoring.write_text(
            json.dumps(
                {
                    "nodes": [
                        {
                            "id": 3,
                            "type": "LoraLoaderModelOnly",
                            "widgets_values": ["shared.safetensors", 1.0],
                        }
                    ]
                }
            )
        )
        payload = self._request({"action": "remove", "id": "shared"})
        with self.assertRaisesRegex(management.ManagementError, "authoring workflow"):
            self.primary.prepare(str(uuid.uuid4()), payload)

    def test_quarantine_rechecks_outside_references_after_commit(self):
        operation_id = str(uuid.uuid4())
        original = {source: self.primary._bundle(source) for source in (ADVANCED, MINIMAL)}
        payload = self._request({"action": "remove", "id": "shared"})
        self.primary.prepare(operation_id, payload)
        self.primary.commit(operation_id)
        self._publish(OUTSIDE, [self.shared], node_id=7)
        with self.assertRaisesRegex(management.ManagementError, "another published workflow"):
            self.primary.quarantine(operation_id)
        self.assertTrue((self.models / "shared.safetensors").is_file())
        self.primary.rollback(operation_id)
        for source in (ADVANCED, MINIMAL):
            self.assertEqual(self.primary._bundle(source), original[source])

    def test_failure_while_committing_restores_every_target(self):
        operation_id = str(uuid.uuid4())
        before = {source: self.primary._bundle(source) for source in (ADVANCED, MINIMAL)}
        payload = self._request(
            {"action": "edit", "id": "shared", "label": "Renamed", "trigger_word": ""}
        )
        self.primary.prepare(operation_id, payload)
        journal = self.primary._journal(operation_id)
        second = self.primary._target_dir(operation_id, journal["targets"][1])
        (second / "new.manifest").write_bytes(b"{}")  # the second target cannot verify
        with self.assertRaises(management.ManagementError):
            self.primary.commit(operation_id)
        self.assertEqual(self.primary._journal(operation_id)["state"], "rolled_back")
        for source in (ADVANCED, MINIMAL):
            self.assertEqual(self.primary._bundle(source), before[source])

    def test_install_rollback_restores_all_targets_and_deletes_the_weight(self):
        operation_id = str(uuid.uuid4())
        staged = self._stage(operation_id)
        before = {source: self.primary._bundle(source) for source in (ADVANCED, MINIMAL)}
        change = {
            "action": "install",
            "id": "added",
            "label": "Added",
            "trigger_word": "t",
            "filename": staged["filename"],
            "sha256": staged["sha256"],
        }
        self.primary.prepare(operation_id, self._request(change))
        self.primary.commit(operation_id)
        self.primary.rollback(operation_id)
        for source in (ADVANCED, MINIMAL):
            self.assertEqual(self.primary._bundle(source), before[source])
        self.assertFalse((self.models / staged["filename"]).exists())

    def test_sync_adds_missing_library_loras_without_dropping_any(self):
        (self.models / "extra.safetensors").write_bytes(safetensors())
        extra = {"id": "extra", "label": "Extra", "filename": "extra.safetensors"}
        self._publish(MINIMAL, [], node_id=906)
        library = [self.shared, {**extra, "trigger_word": "extra word"}]
        operation_id = str(uuid.uuid4())
        self._run(operation_id, self._request({"action": "set_catalog", "items": library}))
        for source in (ADVANCED, MINIMAL):
            self.assertEqual(
                [item["id"] for item in self.mirror.bundle(source)["loras"]], ["shared", "extra"]
            )
        dropping = self._request({"action": "set_catalog", "items": [extra]})
        with self.assertRaisesRegex(management.ManagementError, "would drop"):
            self.primary.prepare(str(uuid.uuid4()), dropping)
        unchanged = self._request({"action": "set_catalog", "items": library})
        with self.assertRaisesRegex(management.ManagementError, "already current"):
            self.primary.prepare(str(uuid.uuid4()), unchanged)

    def test_sync_requires_installed_model_files(self):
        missing = [self.shared, {"id": "ghost", "label": "Ghost", "filename": "ghost.safetensors"}]
        with self.assertRaisesRegex(management.ManagementError, "missing"):
            self.primary.prepare(
                str(uuid.uuid4()), self._request({"action": "set_catalog", "items": missing})
            )

    def test_targets_must_agree_on_the_removed_file(self):
        (self.models / "other.safetensors").write_bytes(safetensors())
        self._publish(
            MINIMAL, [{"id": "shared", "label": "Shared", "filename": "other.safetensors"}]
        )
        with self.assertRaisesRegex(management.ManagementError, "differs between workflows"):
            self.primary.prepare(
                str(uuid.uuid4()), self._request({"action": "remove", "id": "shared"})
            )

    def test_publisher_lease_on_any_target_blocks_the_operation(self):
        self.primary.acquire_publisher_lock(MINIMAL)
        with self.assertRaisesRegex(management.ManagementError, "Save & Publish is active"):
            self.primary.prepare(
                str(uuid.uuid4()), self._request({"action": "remove", "id": "shared"})
            )

    def test_request_validation(self):
        base = self._request({"action": "remove", "id": "shared"})
        duplicate = {**base, "targets": [base["targets"][0], dict(base["targets"][0])]}
        mixed = {**base, "source_path": ADVANCED}
        empty = {**base, "targets": []}
        for payload in (duplicate, mixed, empty):
            with self.subTest(payload=list(payload)), self.assertRaises(management.ManagementError):
                self.primary.prepare(str(uuid.uuid4()), payload)

    def test_status_reports_every_target(self):
        operation_id = str(uuid.uuid4())
        self.primary.prepare(operation_id, self._request({"action": "remove", "id": "shared"}))
        status = self.primary.status(operation_id)
        self.assertEqual(status["state"], "prepared")
        self.assertEqual([t["source_path"] for t in status["targets"]], [ADVANCED, MINIMAL])
        with self.assertRaisesRegex(management.ManagementError, "Choose a candidate"):
            self.primary.candidate(operation_id)


if __name__ == "__main__":
    unittest.main()
