"""Failure-injection coverage for the restored-layout release transaction."""
# ruff: noqa: S603, S607

import copy
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "deploy", Path(__file__).resolve().parents[1] / "production-update.py"
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
OLD, NEW, RUNNER = "a" * 40, "b" * 40, "f" * 40
REAL_REMOVE_IMAGE_TAG = deploy.remove_image_tag


class DeploymentHarness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "deployments" / deploy.APP
        self.root.mkdir(parents=True)
        self.data = self.root / "data"
        (self.data / "assets").mkdir(parents=True)
        (self.data / "assets/preserved").write_text("precious asset")
        with closing(sqlite3.connect(self.data / "app.db")) as db, db:
            db.execute("CREATE TABLE example (value TEXT)")
            db.execute("INSERT INTO example VALUES ('before')")
        self.before = {
            "services": {
                deploy.APP: {
                    "image": "local/comfyui-image-frontend:samus-restored-20260921",
                    "labels": {deploy.REVISION: OLD, "io.service-portal.update.enabled": "false"},
                    "user": "1000:1000",
                    "pull_policy": "never",
                    "env_file": ["/private/runtime.env"],
                    "read_only": True,
                    "networks": ["comfyui_default"],
                },
                deploy.EDGE: {
                    "image": "local/edge:restored",
                    "labels": {deploy.REVISION: OLD, "io.service-portal.update.enabled": "false"},
                },
            },
            "networks": {"comfyui_default": {"external": True}},
        }
        (self.root / "compose.yaml").write_text(json.dumps(self.before))
        (self.root / "release-config.json").write_text('{"runner_revision":"' + RUNNER + '"}')
        self.app = {
            "Id": "old-app",
            "Image": "old-image",
            "Config": {
                "Image": self.before["services"][deploy.APP]["image"],
                "User": "1000:1000",
                "Labels": {deploy.REVISION: OLD},
            },
            "State": {"Running": True, "StartedAt": "old"},
            "Mounts": [{"Destination": "/data", "Source": str(self.data)}],
        }
        self.edge = {"Id": "edge", "Image": "edge-image"}
        self.original = (self.root / "compose.yaml").read_bytes()
        self.events, self.scratch = [], []
        # (container id, image id, configured image) and (image ref, image id).
        self.containers, self.images = [], []
        self.removed_images, self.refused_images = [], set()
        self.seeded = set()
        self.failure_kind = None
        self.stopped = False
        self.live = copy.deepcopy(self.app)
        self.stdout = io.StringIO()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(self.stdout))
        self.preflight = self.stack.enter_context(
            patch.object(
                deploy, "structural_preflight", return_value=(self.before, self.app, self.edge, OLD)
            )
        )
        self.stack.enter_context(patch.object(deploy.backup, "preflight", return_value=self.app))
        self.stack.enter_context(
            patch.object(deploy, "fingerprints", return_value={"runtime": "same"})
        )
        self.ready = self.stack.enter_context(
            patch.object(deploy, "application_ready", return_value=True)
        )
        self.stack.enter_context(patch.object(deploy, "output", side_effect=self.output))
        self.stack.enter_context(patch.object(deploy, "inspect", side_effect=lambda _: self.live))
        self.stack.enter_context(patch.object(deploy, "fetch_source", side_effect=self.fetch))
        self.stack.enter_context(patch.object(deploy, "build_image", side_effect=self.build))
        self.stack.enter_context(patch.object(deploy, "smoke_image", side_effect=self.smoke))
        self.stack.enter_context(patch.object(deploy, "reconcile", side_effect=self.reconcile))
        self.stack.enter_context(patch.object(deploy, "run", side_effect=self.fake_run))
        self.stack.enter_context(
            patch.object(deploy.backup, "stopped_archive", side_effect=self.archive)
        )
        self.stack.enter_context(patch.object(deploy.backup, "restart", side_effect=self.start))
        self.stack.enter_context(
            patch.object(deploy, "remove_image_tag", side_effect=self.remove_image)
        )
        self.stack.enter_context(
            patch.object(deploy, "restart_application", side_effect=self.restart)
        )
        self.verify = self.stack.enter_context(
            patch.object(deploy, "verify_service", side_effect=self.verify_service)
        )
        self.stack.enter_context(
            patch.object(
                deploy.shutil,
                "disk_usage",
                return_value=type("Disk", (), {"free": 100 * 1024**3})(),
            )
        )
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)

    def output(self, *args):
        if args[0] == "du":
            return "1 data"
        if "ps" in args:
            return "edge" if args[-1] == deploy.EDGE else self.live["Id"]
        if args[:3] == ("docker", "image", "inspect"):
            return json.dumps([{"Config": {"Labels": {deploy.REVISION: RUNNER}}}])
        if args[:3] == ("docker", "container", "ls"):
            return "\n".join(c[0] for c in self.containers)
        if args[:3] == ("docker", "container", "inspect"):
            by_id = {c[0]: c for c in self.containers}
            return "".join(f"{by_id[i][1]} {by_id[i][2]}\n" for i in args[5:])
        if args[:3] == ("docker", "image", "ls"):
            self.assertEqual(args[-1], "local/comfyui-image-frontend")
            return "".join(f"{ref} {image_id}\n" for ref, image_id in self.images)
        raise AssertionError(args)

    def remove_image(self, ref):
        self.assertTrue((self.root / ".deployment-update.lock").is_dir())
        self.removed_images.append(ref)
        return ref not in self.refused_images

    def fetch(self, source, sha, deployed, _log):
        self.scratch.append(source.parent)
        source.mkdir()
        (source / ".git").mkdir()
        self.events.append("fetch")
        return sha or NEW

    def build(self, _source, _sha, _directory, _log):
        self.events.append("build")
        if self.failure_kind == "build":
            raise subprocess.CalledProcessError(1, ["docker", "secret-value"])
        return "new-image"

    def smoke(self, _image, user, _log):
        self.assertEqual(user, "1000:1000")
        self.events.append("smoke")
        if self.failure_kind == "smoke":
            raise RuntimeError("candidate unavailable")

    def archive(
        self, _app, data, archive, _timeout, _health, report, *, keep_stopped, log, exclude
    ):
        self.assertTrue(keep_stopped)
        self.assertEqual(tuple(exclude), ("backups", "tmp"))
        self.assertEqual((self.root / "compose.yaml").read_bytes(), self.original)
        self.events.append("backup")
        if self.failure_kind == "backup":
            raise RuntimeError("backup unavailable; original restarted")
        subprocess.run(deploy.backup.archive_command(data, archive, exclude), check=True)
        self.stopped = True
        self.live["State"]["Running"] = False
        report("archiving")

    def reconcile(self, command, _log):
        config = json.loads((self.root / "compose.yaml").read_text())
        candidate = config["services"][deploy.APP]["labels"][deploy.REVISION] == NEW
        self.events.append("up-new" if candidate else "up-old")
        if candidate:
            self.assertTrue(self.stopped, "old app must not restart between backup and cutover")
            with closing(sqlite3.connect(self.data / "app.db")) as db, db:
                db.execute("UPDATE example SET value='migrated'")
        self.live = copy.deepcopy(self.app)
        self.live["Image"] = "new-image" if candidate else "old-image"
        self.live["Config"]["Labels"] = config["services"][deploy.APP]["labels"]
        self.live["State"]["Running"] = True
        if (candidate and self.failure_kind in ("up", "rollback")) or (
            not candidate and self.failure_kind == "rollback"
        ):
            raise RuntimeError("reconcile unavailable")

    def fake_run(self, command, _log, **_kwargs):
        if command[-2:] == ["config", "--quiet"]:
            return
        self.assertIn("stop", command)
        self.assertEqual(command[-1], deploy.APP)
        self.events.append("stop-candidate")
        self.live["State"]["Running"] = False
        if self.failure_kind == "stop-rollback":
            raise RuntimeError("cannot stop")

    def verify_service(self, _compose, _config, _root, image, _edge):
        if image == "new-image" and self.failure_kind in ("verify", "stop-rollback"):
            raise RuntimeError("HTTPS unavailable")
        return {"https": "verified", "app_id": self.live["Id"]}

    def start(self, _app, _timeout):
        self.events.append("start-old")
        self.live["State"]["Running"] = True

    def restart(self, _app, _log):
        self.assertTrue((self.root / ".deployment-update.lock").is_dir())
        self.events.append("restart")
        self.live["State"]["StartedAt"] = "restarted"

    def receipt(self):
        return json.loads(self.run_directory().joinpath("receipt.json").read_text())

    def run_directory(self):
        (directory,) = [
            p
            for p in (self.root / "releases").iterdir()
            if p.name not in self.seeded and (p / "receipt.json").is_file()
        ]
        return directory

    def assert_restored(self):
        self.assertEqual((self.root / "compose.yaml").read_bytes(), self.original)
        self.assertFalse((self.root / ".deployment-update.lock").exists())
        self.assertTrue(all(not p.exists() for p in self.scratch))
        self.assertEqual((self.data / "assets/preserved").read_text(), "precious asset")


class DeploymentTests(DeploymentHarness):
    def test_success_builds_and_smokes_before_stopped_backup(self):
        deploy.deploy(self.root, NEW)
        self.assertEqual(self.events, ["fetch", "build", "smoke", "backup", "up-new"])
        state = json.loads((self.root / "release-state.json").read_text())
        self.assertEqual(
            (state["candidate"], state["image_id"], state["status"]), (NEW, "new-image", "healthy")
        )
        after = json.loads((self.root / "compose.yaml").read_text())
        self.assertEqual(after, deploy.candidate_config(self.before, NEW))
        self.assertEqual(self.receipt()["outcome"], "updated")
        self.assertTrue(all(not p.exists() for p in self.scratch))

    def test_pre_cutover_failures_leave_original_running_and_one_sanitized_error(self):
        for phase in ("build", "smoke", "backup"):
            with self.subTest(phase=phase):
                self.failure_kind = phase
                self.stdout.seek(0)
                self.stdout.truncate()
                with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                    deploy.deploy(self.root, NEW)
                self.assert_restored()
                self.assertNotIn("up-new", self.events)
                errors = [s for s in self.stdout.getvalue().splitlines() if s.startswith("Error:")]
                self.assertEqual(len(errors), 1)
                self.assertNotIn("secret-value", errors[0])
                self.assertIn("recovery original-preserved", errors[0])

    def test_failed_cutover_restores_previous_database_config_and_image(self):
        self.failure_kind = "up"
        inode = self.data.stat().st_ino
        with self.assertRaises(RuntimeError):
            deploy.deploy(self.root, NEW)
        self.assert_restored()
        self.assertEqual(self.data.stat().st_ino, inode)
        self.assertEqual(self.events[-2:], ["stop-candidate", "up-old"])
        with closing(sqlite3.connect(self.data / "app.db")) as db, db:
            self.assertEqual(db.execute("SELECT value FROM example").fetchone()[0], "before")
        failed_db = next((self.root / "releases").glob("*/database.after-cutover/app.db"))
        with closing(sqlite3.connect(failed_db)) as db:
            self.assertEqual(db.execute("SELECT value FROM example").fetchone()[0], "migrated")
        self.assertTrue(self.receipt()["data_restored"])
        self.assertEqual(self.receipt()["recovery"], "rolled-back")
        self.assertEqual(self.receipt()["exit_code"], 1)

    def test_verification_failure_also_rolls_back(self):
        self.failure_kind = "verify"
        with self.assertRaises(RuntimeError):
            deploy.deploy(self.root, NEW)
        self.assert_restored()
        self.assertEqual(self.receipt()["failed_phase"], "final-verification")

    def test_rollback_failure_reports_operator_required_without_retry_loop(self):
        self.failure_kind = "rollback"
        with self.assertRaises(RuntimeError):
            deploy.deploy(self.root, NEW)
        self.assertEqual(self.events.count("up-old"), 1)
        self.assertEqual(self.receipt()["recovery"], "operator-required")

    def test_never_restores_database_while_candidate_stop_is_uncertain(self):
        self.failure_kind = "stop-rollback"
        with self.assertRaises(RuntimeError):
            deploy.deploy(self.root, NEW)
        self.assertEqual(self.receipt()["recovery"], "operator-required")
        self.assertNotIn("up-old", self.events)
        self.assertFalse(list((self.root / "releases").glob("*/database.after-cutover")))

    def test_archive_verification_failure_restarts_original_before_any_cutover(self):
        with (
            patch.object(deploy.backup, "verify_archive", side_effect=RuntimeError("bad backup")),
            self.assertRaises(RuntimeError),
        ):
            deploy.deploy(self.root, NEW)
        self.assertEqual(self.events[-1], "start-old")
        self.assert_restored()

    def test_current_release_restart_does_not_build_or_back_up(self):
        deploy.deploy(self.root, OLD, restart=True)
        self.assertEqual(self.events, ["fetch", "restart"])
        self.assert_restored()
        self.assertEqual(self.receipt()["outcome"], "restarted")

    def test_recovery_restart_counts_toward_requested_restart(self):
        self.ready.return_value = False
        with patch.object(deploy, "wait_for_application", return_value=False):
            deploy.deploy(self.root, OLD, restart=True)
        self.assertEqual(self.events.count("restart"), 1)

    def test_structural_failure_never_restarts_or_fetches(self):
        self.preflight.side_effect = RuntimeError("wrong mount")
        with self.assertRaises(RuntimeError):
            deploy.deploy(self.root, NEW, restart=True)
        self.ready.assert_not_called()
        self.assertEqual(self.events, [])
        self.assertEqual(self.receipt()["failed_phase"], "structural-preflight")

    def test_healthy_app_broken_tls_does_not_restart(self):
        self.verify.side_effect = RuntimeError("TLS unavailable")
        with self.assertRaises(RuntimeError):
            deploy.deploy(self.root, OLD, restart=True)
        self.assertEqual(self.events, ["fetch"])

    def test_check_only_verifies_without_recovery_or_fetch(self):
        deploy.deploy(self.root, check_only=True)
        self.assertEqual(self.events, [])
        self.assert_restored()
        self.assertEqual(self.receipt()["outcome"], "check-passed")

    def test_concurrent_update_leaves_other_job_lock_intact(self):
        lock = self.root / ".deployment-update.lock"
        lock.mkdir()
        (lock / "owner.json").write_text('{"job":"other"}')
        with self.assertRaises(RuntimeError):
            deploy.deploy(self.root, NEW)
        self.assertEqual(json.loads((lock / "owner.json").read_text())["job"], "other")
        self.assertEqual(self.events, [])

    def test_install_changes_only_four_labels_with_current_image(self):
        real_read = Path.read_bytes

        def read(path):
            if path.parent == Path(deploy.__file__).parent and path.name.startswith(
                "update_production"
            ):
                return b"#!/bin/sh\nexit 0\n"
            return real_read(path)

        with patch.object(Path, "read_bytes", read):
            deploy.deploy(self.root, install=RUNNER)
        self.assertEqual(self.events, ["up-old"])
        expected = copy.deepcopy(self.before)
        expected["services"][deploy.APP]["labels"].update(
            deploy.portal_labels(RUNNER, f"{os.getuid()}:{os.getgid()}")
        )
        self.assertEqual(json.loads((self.root / "compose.yaml").read_text()), expected)
        self.assertEqual(self.receipt()["outcome"], "installed")
        self.assertEqual((self.root / "update_production_portal").stat().st_mode & 0o777, 0o755)
        self.assertFalse(list((self.root / "releases").glob("*/data.tar")))


PRIOR_SHAS = ("1" * 40, "2" * 40, "3" * 40)
METADATA = (
    "receipt.json",
    "deployment.log",
    "image.json",
    "source.tar.gz",
    "compose.yaml.previous",
    "compose.previous.json",
    "compose.candidate.json",
)


class RetentionTests(DeploymentHarness):
    """Exactly one rollback survives a successful update; nothing else ever prunes."""

    def setUp(self):
        super().setUp()
        releases = self.root / "releases"
        releases.mkdir(mode=0o700)
        self.archive_sizes = {}
        for index, sha in enumerate(PRIOR_SHAS):
            directory = releases / f"2026092{index}T120000123456Z-{sha[:12]}"
            directory.mkdir(mode=0o700)
            for name in METADATA:
                (directory / name).write_text("provenance " + name)
            (directory / "data.tar").write_bytes(b"x" * (1000 * (index + 1)))
            (directory / "data.tar.sha256").write_text("0" * 64 + "  data.tar\n")
            self.seeded.add(directory.name)
            self.archive_sizes[directory.name] = 1000 * (index + 1) + 75
        partial = releases / "20260923T120000Z-preflight"
        partial.mkdir(mode=0o700)
        (partial / "data.tar.partial").write_bytes(b"p" * 500)
        (partial / "deployment.log").write_text("preflight")
        self.seeded.add(partial.name)
        self.archive_sizes[partial.name] = 500
        self.initial = set(self.seeded)
        self.fingerprint = self.snapshot()
        # Installed runner and a current release state with a recorded previous tag.
        self.runner_image = "local/comfyui-image-frontend-release:" + RUNNER
        (self.root / "release-config.json").write_text(
            json.dumps({"runner_revision": RUNNER, "runner_image": self.runner_image})
        )
        self.before["services"][deploy.APP]["labels"]["io.service-portal.update.image"] = (
            self.runner_image
        )
        (self.root / "compose.yaml").write_text(json.dumps(self.before))
        self.original = (self.root / "compose.yaml").read_bytes()
        (self.root / "release-state.json").write_text(
            json.dumps(
                {
                    "previous": PRIOR_SHAS[2],
                    "candidate": OLD,
                    "image": self.before["services"][deploy.APP]["image"],
                    "image_id": "old-image",
                    "status": "healthy",
                }
            )
        )

    def snapshot(self):
        releases = self.root / "releases"
        return {
            str(p.relative_to(releases)): p.read_bytes()
            for p in releases.rglob("*")
            if p.parent.name in self.initial and p.is_file()
        }

    def assert_untouched(self):
        self.assertEqual(self.snapshot(), self.fingerprint)
        self.assertEqual(self.removed_images, [])

    @staticmethod
    def app_tag(sha):
        return "local/comfyui-image-frontend:" + sha

    def test_success_keeps_only_the_new_rollback_archive(self):
        deploy.deploy(self.root, NEW)
        releases = self.root / "releases"
        current = self.run_directory()
        self.assertTrue((current / "data.tar").is_file())
        self.assertTrue((current / "data.tar.sha256").is_file())
        self.assertEqual(
            sorted(str(p.relative_to(releases)) for p in releases.glob("*/data.tar*")),
            [f"{current.name}/data.tar", f"{current.name}/data.tar.sha256"],
        )
        for name in self.initial:
            directory = releases / name
            if name.endswith("-preflight"):
                self.assertEqual((directory / "deployment.log").read_text(), "preflight")
                continue
            for metadata in METADATA:
                self.assertEqual((directory / metadata).read_text(), "provenance " + metadata)
        receipt = self.receipt()
        self.assertEqual((receipt["phase"], receipt["outcome"]), ("complete", "updated"))
        self.assertEqual(receipt["retention"]["archives_removed"], 7)
        self.assertEqual(receipt["retention"]["bytes_freed"], sum(self.archive_sizes.values()))
        self.assertNotIn("error", receipt["retention"])
        log = (current / "deployment.log").read_text()
        for name in self.initial:
            self.assertIn(f"removed releases/{name}/", log)
        self.assertIn("data.tar (1000 bytes)", log)

    def test_new_archive_excludes_operator_backups_and_scratch_and_verifies(self):
        (self.data / "backups").mkdir()
        (self.data / "backups/app.db.manual").write_text("old copy")
        (self.data / "tmp").mkdir()
        (self.data / "tmp/staged.zip").write_text("scratch")
        (self.data / "assets/backups").mkdir()
        (self.data / "assets/backups/kept").write_text("nested asset")
        (self.data / "uploads").mkdir()
        (self.data / "uploads/kept").write_text("upload")
        deploy.deploy(self.root, NEW)
        archive = self.run_directory() / "data.tar"
        with tarfile.open(archive) as stream:
            names = {m.name.removeprefix("./") for m in stream.getmembers()}
        self.assertFalse({n for n in names if n.split("/")[0] in ("backups", "tmp")})
        self.assertTrue({"app.db", "assets", "assets/backups/kept", "uploads/kept"} <= names)
        self.assertEqual(len(deploy.backup.verify_archive(archive)), 64)
        # Excluded directories stay in the live data directory.
        self.assertTrue((self.data / "backups/app.db.manual").is_file())
        self.assertTrue((self.data / "tmp/staged.zip").is_file())

    def test_failed_cutover_with_rollback_prunes_nothing(self):
        self.images = [(self.app_tag(PRIOR_SHAS[0]), "sha256:stale")]
        for kind in ("up", "verify"):
            with self.subTest(kind=kind):
                self.failure_kind = kind
                with self.assertRaises(RuntimeError):
                    deploy.deploy(self.root, NEW)
                self.assertEqual(self.receipt()["recovery"], "rolled-back")
                self.assertNotIn("retention", self.receipt())
                self.assert_untouched()
                self.seeded.add(self.run_directory().name)

    def test_unverified_rollback_prunes_nothing(self):
        for kind in ("rollback", "stop-rollback"):
            with self.subTest(kind=kind):
                self.failure_kind = kind
                with self.assertRaises(RuntimeError):
                    deploy.deploy(self.root, NEW)
                self.assertEqual(self.receipt()["recovery"], "operator-required")
                self.assert_untouched()
                self.seeded.add(self.run_directory().name)

    def test_failure_before_cutover_prunes_nothing(self):
        self.images = [(self.app_tag(PRIOR_SHAS[0]), "sha256:stale")]
        for kind in ("build", "smoke", "backup"):
            with self.subTest(kind=kind):
                self.failure_kind = kind
                with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                    deploy.deploy(self.root, NEW)
                self.assertEqual(self.receipt()["recovery"], "original-preserved")
                self.assert_untouched()
                self.seeded.add(self.run_directory().name)

    def test_archive_verification_failure_prunes_nothing(self):
        with (
            patch.object(deploy.backup, "verify_archive", side_effect=RuntimeError("bad")),
            self.assertRaises(RuntimeError),
        ):
            deploy.deploy(self.root, NEW)
        self.assertEqual(self.receipt()["recovery"], "original-preserved")
        self.assert_untouched()

    def test_non_update_modes_prune_nothing(self):
        self.images = [(self.app_tag(PRIOR_SHAS[0]), "sha256:stale")]
        real_read = Path.read_bytes

        def read(path):
            if path.parent == Path(deploy.__file__).parent and path.name.startswith(
                "update_production"
            ):
                return b"#!/bin/sh\nexit 0\n"
            return real_read(path)

        runs = {
            "check-only": lambda: deploy.deploy(self.root, check_only=True),
            "restart": lambda: deploy.deploy(self.root, OLD, restart=True),
            "same-sha": lambda: deploy.deploy(self.root, OLD),
            "install": lambda: deploy.deploy(self.root, install=RUNNER),
        }
        with (
            patch.object(Path, "read_bytes", read),
            patch.object(deploy, "enforce_retention", wraps=deploy.enforce_retention) as retention,
        ):
            for mode, run in runs.items():
                with self.subTest(mode=mode):
                    run()
                    self.assertNotIn("retention", self.receipt())
                    self.assert_untouched()
                    self.seeded.add(self.run_directory().name)
        retention.assert_not_called()

    def test_image_pruning_keeps_every_protected_reference(self):
        # After save_state, the recorded previous is the release just replaced (OLD).
        previous = self.app_tag(OLD)
        stopped = self.app_tag("4" * 40)
        running_by_id = self.app_tag("5" * 40)
        compose_ref = self.app_tag("6" * 40)
        label_ref = self.app_tag("7" * 40)
        runner_ref = self.app_tag("8" * 40)
        stale = [self.app_tag(PRIOR_SHAS[2]), self.app_tag("9" * 40), self.app_tag("c" * 40)]
        self.before["services"][deploy.EDGE]["image"] = compose_ref
        self.before["services"][deploy.EDGE]["labels"]["io.service-portal.update.image"] = label_ref
        (self.root / "compose.yaml").write_text(json.dumps(self.before))
        self.original = (self.root / "compose.yaml").read_bytes()
        config = json.loads((self.root / "release-config.json").read_text())
        config["runner_image"] = runner_ref
        (self.root / "release-config.json").write_text(json.dumps(config))
        self.containers = [
            ("c-stopped", "sha256:stopped", stopped),
            ("c-running", "sha256:running", "some-other-name:latest"),
        ]
        self.images = [
            (self.app_tag(NEW), "sha256:new-by-ref"),
            (previous, "sha256:previous"),
            (stopped, "sha256:stopped"),
            (running_by_id, "sha256:running"),
            (compose_ref, "sha256:compose"),
            (label_ref, "sha256:label"),
            (runner_ref, "sha256:runner"),
            ("local/comfyui-image-frontend:samus-restored-20260921", "old-image"),
            ("local/comfyui-image-frontend:latest", "sha256:unpinned"),
            ("local/comfyui-image-frontend:" + "d" * 12, "sha256:short"),
            (stale[0], "sha256:stale0"),
            (stale[1], "sha256:stale1"),
            (stale[2], "sha256:stale2"),
        ]
        self.refused_images = {stale[2]}
        deploy.deploy(self.root, NEW)
        self.assertEqual(self.removed_images, stale)
        retention = self.receipt()["retention"]
        self.assertEqual(retention["images_removed"], stale[:2])
        self.assertNotIn("error", retention)
        self.assertEqual(self.receipt()["outcome"], "updated")
        log = (self.run_directory() / "deployment.log").read_text()
        self.assertIn(f"kept image tag {stale[2]}", log)

    def test_candidate_and_live_image_ids_are_protected_under_any_tag(self):
        self.images = [
            (self.app_tag("9" * 40), "new-image"),
            (self.app_tag("c" * 40), "old-image"),
        ]
        deploy.deploy(self.root, NEW)
        self.assertEqual(self.removed_images, [])

    def test_non_fingerprinted_repositories_are_never_removed(self):
        self.images = [
            ("local/comfyui-image-frontend-edge:" + "9" * 40, "sha256:edge"),
            ("local/comfyui-image-frontend-release:" + "9" * 40, "sha256:release"),
            ("other/comfyui-image-frontend:" + "9" * 40, "sha256:other"),
            ("local/comfyui-image-frontend:" + "9" * 40 + "-dirty", "sha256:suffixed"),
        ]
        deploy.deploy(self.root, NEW)
        self.assertEqual(self.removed_images, [])

    def test_blanket_prune_is_never_invoked(self):
        commands = []
        real = deploy.subprocess.run

        def record(command, *args, **kwargs):
            if command[0] != "docker":
                return real(command, *args, **kwargs)
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0)

        self.stack.enter_context(
            patch.object(deploy, "remove_image_tag", side_effect=REAL_REMOVE_IMAGE_TAG)
        )
        self.images = [(self.app_tag("9" * 40), "sha256:stale")]
        with patch.object(deploy.subprocess, "run", side_effect=record):
            deploy.deploy(self.root, NEW)
        self.assertIn(["docker", "image", "rm", self.app_tag("9" * 40)], commands)
        self.assertFalse([c for c in commands if "prune" in c or "--force" in c or "-f" in c[:4]])

    def test_symlinked_and_foreign_entries_are_skipped(self):
        releases = self.root / "releases"
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "data.tar").write_text("not ours")
        linked = releases / "20260924T120000Z-eeeeeeeeeeee"
        linked.symlink_to(outside, target_is_directory=True)
        foreign = releases / ("runner-" + RUNNER)
        foreign.mkdir()
        (foreign / "data.tar").write_text("runner artifact")
        manual = releases / "manual-backup"
        manual.mkdir()
        (manual / "data.tar").write_text("operator backup")
        uppercase = releases / "20260924T120000Z-EEEEEEEEEEEE"
        uppercase.mkdir()
        (uppercase / "data.tar").write_text("foreign name")
        real = releases / "20260925T120000Z-ffffffffffff"
        real.mkdir()
        target = Path(self.temp.name) / "target.tar"
        target.write_text("symlinked archive target")
        (real / "data.tar").symlink_to(target)
        (real / "data.tar.sha256").mkdir()
        deploy.deploy(self.root, NEW)
        self.assertEqual((outside / "data.tar").read_text(), "not ours")
        self.assertEqual((foreign / "data.tar").read_text(), "runner artifact")
        self.assertEqual((manual / "data.tar").read_text(), "operator backup")
        self.assertEqual((uppercase / "data.tar").read_text(), "foreign name")
        self.assertTrue((real / "data.tar").is_symlink())
        self.assertTrue((real / "data.tar.sha256").is_dir())
        self.assertEqual(target.read_text(), "symlinked archive target")
        self.assertTrue(linked.is_symlink())
        receipt = self.receipt()
        self.assertEqual(receipt["retention"]["archives_removed"], 7)
        self.assertNotIn("error", receipt["retention"])

    def test_pruning_exception_still_reports_updated(self):
        real_unlink = os.unlink

        def unlink(path, *args, **kwargs):
            if path == "data.tar" and "dir_fd" in kwargs:
                raise PermissionError("unlinkable")
            return real_unlink(path, *args, **kwargs)

        self.images = [(self.app_tag("9" * 40), "sha256:stale")]
        with patch.object(deploy.os, "unlink", side_effect=unlink):
            deploy.deploy(self.root, NEW)
        receipt = self.receipt()
        self.assertEqual((receipt["phase"], receipt["outcome"]), ("complete", "updated"))
        self.assertEqual(receipt["exit_code"], 0)
        self.assertNotIn(receipt["recovery"], ("rolled-back", "operator-required"))
        self.assertEqual(receipt["retention"]["error"], "PermissionError")
        # Remaining archive files and image tags are still processed.
        self.assertEqual(receipt["retention"]["archives_removed"], 4)
        self.assertEqual(receipt["retention"]["images_removed"], [self.app_tag("9" * 40)])
        self.assertNotIn("up-old", self.events)
        state = json.loads((self.root / "release-state.json").read_text())
        self.assertEqual(state["candidate"], NEW)
        self.assertFalse((self.root / ".deployment-update.lock").exists())

    def test_unexpected_retention_failure_still_reports_updated(self):
        with patch.object(deploy, "prune_images", side_effect=ValueError("bad docker output")):
            deploy.deploy(self.root, NEW)
        receipt = self.receipt()
        self.assertEqual((receipt["outcome"], receipt["exit_code"]), ("updated", 0))
        self.assertEqual(receipt["retention"]["error"], "ValueError")
        self.assertEqual(receipt["retention"]["archives_removed"], 7)
        self.assertNotIn("up-old", self.events)

    def test_pruner_refuses_without_this_runs_lock(self):
        summary = {"archives_removed": 0, "bytes_freed": 0, "images_removed": []}
        keep = self.root / "releases" / "20260930T000000Z-bbbbbbbbbbbb"
        with self.assertRaises((RuntimeError, OSError)):
            deploy.prune_archives(self.root, keep, io.StringIO(), summary)
        lock = self.root / ".deployment-update.lock"
        lock.mkdir()
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid() + 1}))
        with self.assertRaises(RuntimeError):
            deploy.prune_archives(self.root, keep, io.StringIO(), summary)
        with self.assertRaises(RuntimeError):
            deploy.prune_images(self.root, set(), set(), io.StringIO(), summary)
        self.assert_untouched()


class ReleaseSourceTests(unittest.TestCase):
    def test_real_fetch_pins_ancestor_without_leaving_checkout_on_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote"
            subprocess.run(["git", "init", "-q", "-b", "main", str(remote)], check=True)
            for value in ("old", "new"):
                (remote / "app.py").write_text(value)
                subprocess.run(["git", "-C", str(remote), "add", "."], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(remote),
                        "-c",
                        "user.name=Test",
                        "-c",
                        "user.email=test@example.invalid",
                        "commit",
                        "-qm",
                        value,
                    ],
                    check=True,
                )
            old = subprocess.check_output(
                ["git", "-C", str(remote), "rev-parse", "HEAD~1"], text=True
            ).strip()
            new = subprocess.check_output(
                ["git", "-C", str(remote), "rev-parse", "HEAD"], text=True
            ).strip()
            with (
                patch.object(deploy, "REPOSITORY", str(remote)),
                (root / "private.log").open("w") as log,
            ):
                source = root / "scratch" / "source"
                sha = deploy.fetch_source(source, old, old, log)
                self.assertEqual(sha, old)  # Older 3501bab-style acceptance target still supported.
                self.assertEqual((source / "app.py").read_text(), "old")
                self.assertEqual((source / "app.py").stat().st_mode & 0o777, 0o644)
                with self.assertRaises(RuntimeError):
                    deploy.fetch_source(root / "divergent", old, new, log)

    def test_existing_unrecorded_image_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "release"
            directory.mkdir()
            with (
                patch.object(deploy, "run") as run,
                patch.object(
                    deploy.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout=b'[{"Id":"existing"}]'),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "provenance"):
                    deploy.build_image(Path("/scratch"), NEW, directory, io.StringIO())
                self.assertEqual(run.call_count, 1)  # archive only, no build

    def test_reconcile_targets_only_app_without_build_or_pull(self):
        with patch.object(deploy, "run") as run:
            deploy.reconcile(deploy.compose_command(Path("/deployment")), io.StringIO())
        command = run.call_args.args[0]
        self.assertEqual(command[-1], deploy.APP)
        for flag in ("--no-deps", "--no-build", "--pull"):
            self.assertIn(flag, command)
        self.assertEqual(command.count("-f"), 1)


class VerificationTests(unittest.TestCase):
    def test_tls_uses_external_ca_and_origin_without_edge_environment(self):
        root = Path("/home/owner/deployments/comfyui-image-frontend")
        calls = []
        manifest = {
            "asset_version": "fixture",
            "assets": {"app": "/assets/app.js", "styles": "/assets/styles.css"},
        }

        def output(*args):
            calls.append(args)
            if "ps" in args:
                return "edge" if args[-1] == deploy.EDGE else "app"
            if args[:2] == ("docker", "exec"):
                return json.dumps(manifest)
            if args[0] == "curl":
                if args[-1].endswith("/api/health"):
                    return json.dumps(
                        {"status": "ok", "database": True, "worker": {"state": "running"}}
                    )
                if args[-1].endswith("/build.json"):
                    return json.dumps(manifest)
                return "/assets/app.js /assets/styles.css"
            raise AssertionError(args)

        with (
            patch.object(deploy, "output", side_effect=output),
            patch.object(
                deploy,
                "inspect",
                return_value={
                    "Image": "image",
                    "State": {"Health": {"Status": "healthy"}, "StartedAt": "now"},
                },
            ),
            patch.object(deploy, "wait_for_application", return_value=True),
        ):
            result = deploy.verify_service(
                deploy.compose_command(root), {"services": {deploy.EDGE: {}}}, root, "image", "edge"
            )
        self.assertEqual(result["https"], "verified")
        curls = [c for c in calls if c[0] == "curl"]
        self.assertTrue(curls)
        for command in curls:
            self.assertEqual(
                command[command.index("--cacert") + 1],
                str(deploy.credentials_dir(root) / "tls/ca.crt"),
            )
            self.assertTrue(command[-1].startswith("https://192.168.1.5:8443/"))
            self.assertNotIn("--insecure", command)

    def test_restored_image_and_no_edge_environment_pass_structural_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "Caddyfile").write_text(
                "tls /etc/caddy/certificates/leaf.crt /etc/caddy/certificates/leaf.key\n"
            )
            app = {
                "Image": "app-id",
                "Config": {
                    "Image": "local/frontend:restored",
                    "User": "1000:1000",
                    "Labels": {deploy.REVISION: OLD},
                },
            }
            edge = {"Image": "edge-id", "Config": {"Image": "local/edge:restored"}}
            config = {
                "services": {
                    deploy.APP: {"image": app["Config"]["Image"], "labels": {deploy.REVISION: OLD}},
                    deploy.EDGE: {
                        "image": edge["Config"]["Image"],
                        "labels": {"io.service-portal.update.enabled": "false"},
                    },
                }
            }
            (root / "compose.yaml").write_text(json.dumps(config))

            def output(*args):
                if args[:2] == ("docker", "info"):
                    return "samus"
                if args[:3] == ("docker", "image", "inspect"):
                    return json.dumps(
                        [{"Id": "app-id" if args[-1] == app["Config"]["Image"] else "edge-id"}]
                    )
                if args[0] == "openssl":
                    return "public-key"
                return "edge"

            with (
                patch.object(deploy.backup, "preflight", return_value=app),
                patch.object(deploy, "output", side_effect=output),
                patch.object(deploy, "inspect", return_value=edge),
            ):
                self.assertEqual(deploy.structural_preflight(root)[-1], OLD)
                (root / "release-state.json").write_text('{"candidate":"stale"}')
                with self.assertRaisesRegex(RuntimeError, "Release state differs"):
                    deploy.structural_preflight(root)


if __name__ == "__main__":
    unittest.main()
