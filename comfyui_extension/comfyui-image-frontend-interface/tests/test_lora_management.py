from __future__ import annotations

import asyncio
import importlib.util
import json
import struct
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = types.ModuleType("cif_management_under_test")
PACKAGE.__path__ = [str(ROOT)]
sys.modules[PACKAGE.__name__] = PACKAGE
SPEC = importlib.util.spec_from_file_location(
    PACKAGE.__name__ + ".lora_management", ROOT / "lora_management.py"
)
management = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(management)

SOURCE = "workflows/team/test.json"


def safetensors() -> bytes:
    header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    return struct.pack("<Q", len(header)) + header + b"\x00\x00\x80?"


class Stream:
    def __init__(self, value: bytes):
        self.value = value

    async def read(self, size: int) -> bytes:
        value, self.value = self.value[:size], self.value[size:]
        return value


class EmptyQueue:
    def get_current_queue(self):
        return ([], [])


def publication(userdata: Path, catalog: list[dict], *, node_id: int = 42) -> None:
    names = management.WIDGETS
    defaults = [{"id": item["id"], "strength": 0} for item in catalog]
    public = [
        {key: item[key] for key in ("id", "label", "description", "trigger_word") if key in item}
        for item in catalog
    ]
    widgets = [
        json.dumps(catalog),
        json.dumps(defaults),
        0.0,
        2.0,
        0.05,
        "loras",
        str(uuid.uuid4()),
        "LoRAs",
        "A stack",
        "lora",
        False,
        False,
        "LoRAs",
        100,
    ]
    workflow = {
        "nodes": [
            {
                "id": node_id,
                "type": "CIFLoraStack",
                "inputs": [{"name": name, "widget": {"name": name}} for name in names],
                "widgets_values": widgets,
            }
        ]
    }
    api = {
        str(node_id): {
            "class_type": "CIFLoraStack",
            "inputs": dict(zip(names, widgets, strict=False)),
        }
    }
    declaration = {
        "id": "loras",
        "type": "lora_stack",
        "semantic_role": "lora",
        "required": False,
        "bindings": [{"node_id": str(node_id), "input": "value"}],
        "items": public,
        "default": defaults,
        "minimum": 0.0,
        "maximum": 2.0,
        "step": 0.05,
    }
    manifest = {
        "source_id": SOURCE,
        "publication_id": str(uuid.uuid4()),
        "workflow": {"path": SOURCE},
        "api": {"path": "workflows/team/test.api.json"},
        "interface": {"inputs": [declaration]},
        "technical_inventory": {
            "loras": [
                {
                    "usage": "public_stack",
                    "parameter_id": "loras",
                    "items": public,
                    "default": defaults,
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "step": 0.05,
                }
            ]
        },
    }
    folder = userdata / "workflows" / "team"
    folder.mkdir(parents=True, exist_ok=True)
    w = management._json_bytes(workflow)
    a = management._json_bytes(api)
    manifest["workflow"]["sha256"] = management._sha(w)
    manifest["api"]["sha256"] = management._sha(a)
    (folder / "test.json").write_bytes(w)
    (folder / "test.api.json").write_bytes(a)
    (folder / "test.interface.json").write_bytes(management._json_bytes(manifest))


class ManagementTests(unittest.TestCase):
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
        publication(self.primary_user, [])
        for source in ("test.json", "test.api.json", "test.interface.json"):
            origin = self.primary_user / "workflows" / "team" / source
            target = self.mirror_user / "workflows" / "team" / source
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(origin.read_bytes())
        self.primary = management.LoraManagement(
            self.primary_user, self.models, model_writer=True, queue=EmptyQueue()
        )
        self.mirror = management.LoraManagement(
            self.mirror_user, self.models, model_writer=False, queue=EmptyQueue()
        )

    def _request(self, service, change, operation_id, publication_id=None):
        return {
            "source_path": SOURCE,
            "expected_revision": service.bundle(SOURCE)["revision"],
            "change": change,
            "publication_id": publication_id or str(uuid.uuid4()),
            "published_at": "2026-09-26T12:00:00Z",
        }

    def _publish_catalog(self, catalog):
        publication(self.primary_user, catalog)
        for name in ("test.json", "test.api.json", "test.interface.json"):
            path = Path("workflows/team") / name
            (self.mirror_user / path).write_bytes((self.primary_user / path).read_bytes())

    def test_install_then_remove_across_shared_read_only_replica(self):
        upload = safetensors()
        install_id = str(uuid.uuid4())
        staged = asyncio.run(
            self.primary.stage(
                install_id,
                Stream(upload),
                content_length=len(upload),
                filename="example.safetensors",
            )
        )
        change = {
            "action": "install",
            "id": "example",
            "label": "Example",
            "trigger_word": "example trigger",
            "filename": staged["filename"],
            "sha256": staged["sha256"],
        }
        payload = self._request(self.primary, change, install_id)
        first = self.primary.prepare(install_id, payload)
        second = self.mirror.prepare(install_id, payload)
        with self.assertRaisesRegex(management.ManagementError, "administration is active"):
            self.primary.acquire_publisher_lock(SOURCE)
        self.assertEqual(first["candidate_hashes"], second["candidate_hashes"])
        self.assertEqual(
            self.primary.candidate(install_id)["manifest_b64"],
            self.mirror.candidate(install_id)["manifest_b64"],
        )
        self.primary.commit(install_id)
        self.mirror.commit(install_id)
        installed = self.models / staged["filename"]
        self.assertEqual(installed.read_bytes(), upload)
        self.assertEqual(self.mirror.bundle(SOURCE)["loras"][0]["trigger_word"], "example trigger")
        self.primary.finalize(install_id)
        self.mirror.finalize(install_id)

        remove_id = str(uuid.uuid4())
        change = {"action": "remove", "id": "example"}
        payload = self._request(self.primary, change, remove_id)
        self.assertEqual(
            self.primary.prepare(remove_id, payload)["candidate_hashes"],
            self.mirror.prepare(remove_id, payload)["candidate_hashes"],
        )
        self.primary.commit(remove_id)
        self.mirror.commit(remove_id)
        self.primary.quarantine(remove_id)
        self.assertFalse(installed.exists())
        self.primary.finalize(remove_id)
        self.mirror.finalize(remove_id)
        self.assertEqual(self.primary.bundle(SOURCE)["loras"], [])

    def test_failed_commit_rolls_back_exact_bytes_and_weight(self):
        upload = safetensors()
        operation_id = str(uuid.uuid4())
        staged = asyncio.run(
            self.primary.stage(
                operation_id, Stream(upload), content_length=len(upload), filename="x.safetensors"
            )
        )
        change = {
            "action": "install",
            "id": "x",
            "label": "X",
            "trigger_word": "x",
            **{"filename": staged["filename"], "sha256": staged["sha256"]},
        }
        before = self.primary._bundle(SOURCE)
        self.primary.prepare(operation_id, self._request(self.primary, change, operation_id))
        self.primary.commit(operation_id)
        self.primary.rollback(operation_id)
        self.assertEqual(self.primary._bundle(SOURCE), before)
        self.assertFalse((self.models / staged["filename"]).exists())

    def test_edit_publishes_metadata_on_both_replicas_without_changing_model(self):
        original = {
            "id": "existing",
            "label": "Old title",
            "description": "Keep this description",
            "trigger_word": "old trigger",
            "filename": "existing.safetensors",
        }
        self._publish_catalog([original])
        model = self.models / original["filename"]
        model.write_bytes(safetensors())
        before_model = (model.stat().st_ino, model.read_bytes())

        operation_id = str(uuid.uuid4())
        change = {
            "action": "edit",
            "id": "existing",
            "label": "New title",
            "trigger_word": "new trigger",
        }
        request = self._request(self.primary, change, operation_id)
        first = self.primary.prepare(operation_id, request)
        second = self.mirror.prepare(operation_id, request)
        self.assertEqual(first["candidate_hashes"], second["candidate_hashes"])
        for service in (self.primary, self.mirror):
            self.assertEqual(service.commit(operation_id)["state"], "committed")
            result = service.bundle(SOURCE)
            self.assertEqual(result["loras"][0]["label"], "New title")
            self.assertEqual(result["loras"][0]["trigger_word"], "new trigger")
            self.assertEqual(result["loras"][0]["description"], original["description"])
            self.assertEqual(
                result["files"], [{"id": "existing", "filename": original["filename"]}]
            )
            workflow, api, manifest = (
                json.loads(service._bundle(SOURCE)[key]) for key in ("workflow", "api", "manifest")
            )
            catalog = json.loads(workflow["nodes"][0]["widgets_values"][0])
            self.assertEqual(catalog[0]["id"], original["id"])
            self.assertEqual(catalog[0]["filename"], original["filename"])
            self.assertEqual(catalog[0]["description"], original["description"])
            self.assertEqual(json.loads(api["42"]["inputs"]["catalog_json"]), catalog)
            self.assertEqual(
                json.loads(api["42"]["inputs"]["value"]), [{"id": "existing", "strength": 0}]
            )
            self.assertEqual(manifest["interface"]["inputs"][0]["items"], result["loras"])
            self.assertEqual(manifest["technical_inventory"]["loras"][0]["items"], result["loras"])
            service.finalize(operation_id)
        self.assertEqual((model.stat().st_ino, model.read_bytes()), before_model)

        clear_id = str(uuid.uuid4())
        clear = {"action": "edit", "id": "existing", "label": "New title", "trigger_word": ""}
        request = self._request(self.primary, clear, clear_id)
        self.primary.prepare(clear_id, request)
        self.mirror.prepare(clear_id, request)
        for service in (self.primary, self.mirror):
            service.commit(clear_id)
            self.assertNotIn("trigger_word", service.bundle(SOURCE)["loras"][0])
            service.finalize(clear_id)
        self.assertEqual((model.stat().st_ino, model.read_bytes()), before_model)

        rename_id = str(uuid.uuid4())
        rename = {"action": "edit", "id": "existing", "label": "Renamed only", "trigger_word": ""}
        request = self._request(self.primary, rename, rename_id)
        self.primary.prepare(rename_id, request)
        self.mirror.prepare(rename_id, request)
        for service in (self.primary, self.mirror):
            service.commit(rename_id)
            self.assertEqual(service.bundle(SOURCE)["loras"][0]["label"], "Renamed only")
            self.assertNotIn("trigger_word", service.bundle(SOURCE)["loras"][0])
            service.finalize(rename_id)
        self.assertEqual((model.stat().st_ino, model.read_bytes()), before_model)

    def test_edit_rejects_invalid_values_duplicate_ids_and_noop(self):
        original = {"id": "existing", "label": "Existing", "filename": "existing.safetensors"}
        self._publish_catalog([original])
        (self.models / original["filename"]).write_bytes(safetensors())
        cases = [
            ({"id": "../bad", "label": "Updated", "trigger_word": "word"}, "Invalid LoRA ID"),
            ({"id": "missing", "label": "Updated", "trigger_word": "word"}, "not uniquely present"),
            ({"id": "existing", "label": " ", "trigger_word": "word"}, "Invalid LoRA title"),
            ({"id": "existing", "label": "x" * 121, "trigger_word": "word"}, "Invalid LoRA title"),
            ({"id": "existing", "label": "Updated", "trigger_word": None}, "Invalid LoRA title"),
            (
                {"id": "existing", "label": "Updated", "trigger_word": "x" * 121},
                "Invalid LoRA title",
            ),
            ({"id": "existing", "label": " Existing ", "trigger_word": " "}, "unchanged"),
        ]
        for fields, error in cases:
            with self.subTest(fields=fields):
                operation_id = str(uuid.uuid4())
                with self.assertRaisesRegex(management.ManagementError, error):
                    self.primary.prepare(
                        operation_id,
                        self._request(self.primary, {"action": "edit", **fields}, operation_id),
                    )
        self._publish_catalog([original, dict(original)])
        operation_id = str(uuid.uuid4())
        with self.assertRaisesRegex(management.ManagementError, "not uniquely present"):
            self.primary.prepare(
                operation_id,
                self._request(
                    self.primary,
                    {"action": "edit", "id": "existing", "label": "Updated", "trigger_word": ""},
                    operation_id,
                ),
            )

    def test_edit_cannot_reuse_staged_upload_operation_id(self):
        original = {"id": "existing", "label": "Existing", "filename": "existing.safetensors"}
        self._publish_catalog([original])
        (self.models / original["filename"]).write_bytes(safetensors())
        operation_id = str(uuid.uuid4())
        upload = safetensors()
        asyncio.run(
            self.primary.stage(
                operation_id,
                Stream(upload),
                content_length=len(upload),
                filename="unused.safetensors",
            )
        )
        with self.assertRaisesRegex(management.ManagementError, "Staged upload belongs"):
            self.primary.prepare(
                operation_id,
                self._request(
                    self.primary,
                    {"action": "edit", "id": "existing", "label": "New", "trigger_word": ""},
                    operation_id,
                ),
            )
        self.primary.rollback(operation_id)

    def test_edit_rollback_restores_exact_publication_without_touching_model(self):
        original = {"id": "existing", "label": "Existing", "filename": "existing.safetensors"}
        self._publish_catalog([original])
        model = self.models / original["filename"]
        model.write_bytes(safetensors())
        before_model = (model.stat().st_ino, model.read_bytes())
        before = self.primary._bundle(SOURCE)
        operation_id = str(uuid.uuid4())
        self.primary.prepare(
            operation_id,
            self._request(
                self.primary,
                {"action": "edit", "id": "existing", "label": "Updated", "trigger_word": "word"},
                operation_id,
            ),
        )
        self.primary.commit(operation_id)
        self.assertEqual(self.primary.rollback(operation_id), {"state": "rolled_back"})
        self.assertEqual(self.primary._bundle(SOURCE), before)
        self.assertEqual((model.stat().st_ino, model.read_bytes()), before_model)

    def test_rejects_bad_upload_stale_revision_and_unsafe_filename(self):
        operation_id = str(uuid.uuid4())
        with self.assertRaisesRegex(management.ManagementError, "safetensors"):
            asyncio.run(
                self.primary.stage(
                    operation_id, Stream(b"bad"), content_length=3, filename="bad.safetensors"
                )
            )
        with self.assertRaisesRegex(management.ManagementError, "filename"):
            management._safe_model_name("../outside.safetensors")
        old = self.primary.bundle(SOURCE)["revision"]
        old["manifest_sha256"] = "0" * 64
        payload = self._request(self.primary, {"action": "remove", "id": "x"}, operation_id)
        payload["expected_revision"] = old
        with self.assertRaisesRegex(management.ManagementError, "revision changed"):
            self.primary.prepare(operation_id, payload)

    def test_rejects_symlinked_workflow_and_operation_parents(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.primary_user / "workflows" / "linked"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(management.ManagementError, "symlink"):
            self.primary._bundle("workflows/linked/graph.json")
        operation_root = self.primary_user / ".cif-lora-operations"
        operation_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(management.ManagementError, "symlink"):
            self.primary._opdir(str(uuid.uuid4()))

    def test_remove_blocks_native_loader_in_selected_publication(self):
        original = {"id": "old", "label": "Old", "filename": "old.safetensors"}
        for folder in (self.primary_user, self.mirror_user):
            graph = folder / "workflows" / "team" / "test.api.json"
            editable = folder / "workflows" / "team" / "test.json"
            manifest = folder / "workflows" / "team" / "test.interface.json"
            api = json.loads(graph.read_bytes())
            workflow = json.loads(editable.read_bytes())
            public = ["id", "label"]
            api["42"]["inputs"]["catalog_json"] = json.dumps([original])
            api["42"]["inputs"]["value"] = json.dumps([{"id": "old", "strength": 0}])
            workflow["nodes"][0]["widgets_values"][:2] = [
                api["42"]["inputs"]["catalog_json"],
                api["42"]["inputs"]["value"],
            ]
            api["77"] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"lora_name": "old.safetensors"},
            }
            w, a = management._json_bytes(workflow), management._json_bytes(api)
            m = json.loads(manifest.read_bytes())
            item = {key: original[key] for key in public}
            m["interface"]["inputs"][0]["items"] = [item]
            m["interface"]["inputs"][0]["default"] = [{"id": "old", "strength": 0}]
            m["technical_inventory"]["loras"][0]["items"] = [item]
            m["technical_inventory"]["loras"][0]["default"] = [{"id": "old", "strength": 0}]
            m["workflow"]["sha256"] = management._sha(w)
            m["api"]["sha256"] = management._sha(a)
            editable.write_bytes(w)
            graph.write_bytes(a)
            manifest.write_bytes(management._json_bytes(m))
        (self.models / "old.safetensors").write_bytes(safetensors())
        operation_id = str(uuid.uuid4())
        payload = self._request(self.primary, {"action": "remove", "id": "old"}, operation_id)
        with self.assertRaisesRegex(management.ManagementError, "another active loader"):
            self.primary.prepare(operation_id, payload)

    def test_production_shape_has_822_and_fifteen_catalog_items(self):
        # Read-only production inspection (2026-09-26) confirmed one root node
        # 822, this widget layout, 15 catalog items, and one public_stack entry.
        user = Path(self.temp.name) / "v31" / "default"
        user.mkdir(parents=True)
        catalog = [
            {
                "id": f"lora_{index}",
                "label": f"LoRA {index}",
                "filename": f"models/old_{index}.safetensors",
            }
            for index in range(15)
        ]
        publication(user, catalog, node_id=822)
        service = management.LoraManagement(user, self.models, model_writer=False)
        result = service.bundle(SOURCE)
        self.assertEqual(len(result["loras"]), 15)
        self.assertEqual(len(result["files"]), 15)
        self.assertEqual(result["files"][0]["id"], "lora_0")

    def test_publisher_lease_blocks_management_prepare(self):
        lock = self.primary.acquire_publisher_lock(SOURCE)
        operation_id = str(uuid.uuid4())
        payload = self._request(self.primary, {"action": "remove", "id": "old"}, operation_id)
        with self.assertRaisesRegex(management.ManagementError, "Save & Publish"):
            self.primary.prepare(operation_id, payload)
        self.assertEqual(
            self.primary.release_publisher_lock(SOURCE, lock["token"]), {"state": "released"}
        )

    def test_upload_retry_requires_identical_bytes_and_staged_state(self):
        upload = safetensors()
        operation_id = str(uuid.uuid4())
        first = asyncio.run(
            self.primary.stage(
                operation_id, Stream(upload), content_length=len(upload), filename="x.safetensors"
            )
        )
        second = asyncio.run(
            self.primary.stage(
                operation_id, Stream(upload), content_length=len(upload), filename="x.safetensors"
            )
        )
        self.assertEqual(first, second)
        changed = upload[:-1] + bytes([upload[-1] ^ 1])
        with self.assertRaisesRegex(management.ManagementError, "another file"):
            asyncio.run(
                self.primary.stage(
                    operation_id,
                    Stream(changed),
                    content_length=len(changed),
                    filename="x.safetensors",
                )
            )
        incoming = self.primary._stage_path(operation_id).with_name(operation_id + ".part.orphan")
        incoming.write_bytes(b"partial upload")
        self.assertEqual(self.primary.rollback(operation_id), {"state": "rolled_back"})
        self.assertFalse(self.primary._stage_path(operation_id).exists())
        self.assertFalse(incoming.exists())

    def test_rollback_clears_upload_link_left_before_first_journal(self):
        operation_id = str(uuid.uuid4())
        stage = self.primary._stage_path(operation_id)
        stage.parent.mkdir(parents=True)
        stage.write_bytes(safetensors())
        incoming = stage.with_name(stage.name + "." + uuid.uuid4().hex)
        incoming.write_bytes(b"partial upload")
        self.assertFalse((self.primary._opdir(operation_id) / "journal.json").exists())

        self.assertEqual(self.primary.rollback(operation_id), {"state": "rolled_back"})
        self.assertFalse(stage.exists())
        self.assertFalse(incoming.exists())

    def test_nonempty_native_queue_blocks_removal(self):
        old = {"id": "old", "label": "Old", "filename": "old.safetensors"}
        user = Path(self.temp.name) / "queued" / "default"
        user.mkdir(parents=True)
        publication(user, [old])
        (self.models / "old.safetensors").write_bytes(safetensors())

        class Queue:
            def get_current_queue(self):
                return ([{"any": "job"}], [])

        service = management.LoraManagement(user, self.models, model_writer=True, queue=Queue())
        operation_id = str(uuid.uuid4())
        payload = self._request(service, {"action": "remove", "id": "old"}, operation_id)
        with self.assertRaisesRegex(management.ManagementError, "queue must be empty"):
            service.prepare(operation_id, payload)

    def test_remove_blocks_duplicate_filename_in_same_catalog(self):
        user = Path(self.temp.name) / "duplicates" / "default"
        user.mkdir(parents=True)
        publication(
            user,
            [
                {"id": "one", "label": "One", "filename": "shared.safetensors"},
                {"id": "two", "label": "Two", "filename": "shared.safetensors"},
            ],
        )
        (self.models / "shared.safetensors").write_bytes(safetensors())
        service = management.LoraManagement(
            user, self.models, model_writer=True, queue=EmptyQueue()
        )
        operation_id = str(uuid.uuid4())
        payload = self._request(service, {"action": "remove", "id": "one"}, operation_id)
        with self.assertRaisesRegex(management.ManagementError, "shared by another catalog"):
            service.prepare(operation_id, payload)

    def test_authoring_subgraph_loader_is_a_dependency(self):
        workflow = {
            "nodes": [],
            "definitions": {
                "subgraphs": [
                    {
                        "nodes": [
                            {"type": "LoraLoaderModelOnly", "widgets_values": ["used.safetensors"]}
                        ]
                    }
                ]
            },
        }
        self.assertTrue(management._active_editable_reference(workflow, "used.safetensors"))
        self.assertFalse(management._active_editable_reference(workflow, "other.safetensors"))


if __name__ == "__main__":
    unittest.main()
