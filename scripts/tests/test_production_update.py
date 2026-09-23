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


class DeploymentTests(unittest.TestCase):
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
        raise AssertionError(args)

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

    def archive(self, _app, data, archive, _timeout, _health, report, *, keep_stopped, log):
        self.assertTrue(keep_stopped)
        self.assertEqual((self.root / "compose.yaml").read_bytes(), self.original)
        self.events.append("backup")
        if self.failure_kind == "backup":
            raise RuntimeError("backup unavailable; original restarted")
        with tarfile.open(archive, "w") as stream:
            stream.add(data, arcname=".")
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
        return json.loads(next((self.root / "releases").glob("*/receipt.json")).read_text())

    def assert_restored(self):
        self.assertEqual((self.root / "compose.yaml").read_bytes(), self.original)
        self.assertFalse((self.root / ".deployment-update.lock").exists())
        self.assertTrue(all(not p.exists() for p in self.scratch))
        self.assertEqual((self.data / "assets/preserved").read_text(), "precious asset")

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
