"""Exercise the complete deployment transaction against a simulated Docker/Git host."""

import copy
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "deploy", Path(__file__).resolve().parents[1] / "production-update.py"
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
OLD, NEW, FROZEN = "a" * 40, "b" * 40, "f" * 40


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for path in ("source", "data/certificates", "ordered-lora-" + OLD):
            (self.root / path).mkdir(parents=True)
        for name in ("ca.crt", "tls.crt", "tls.key"):
            (self.root / "data/certificates" / name).write_text("preserved certificate")
        (self.root / ".env").write_text(f"CIF_IMAGE_TAG={OLD}\nKEEP_SETTING=unchanged\n")
        (self.root / "compose.yaml").write_text("preserved base compose")
        (self.root / "compose.ordered-lora.yaml").write_text(
            "services:\n  comfyui-image-frontend:\n    build:\n"
            f"      context: ./ordered-lora-{OLD}\n"
        )
        self.original = {
            name: (self.root / name).read_bytes()
            for name in (".env", "compose.yaml", "compose.ordered-lora.yaml")
        }
        self.app = {
            "Id": "old-app",
            "Image": "old-image",
            "Config": {"Image": "frontend:" + OLD, "User": "1000:1000"},
            "State": {"Running": True},
            "Mounts": [{"Destination": "/data", "Source": str(self.root / "data")}],
        }
        self.edge = {"Id": "edge", "Image": "edge-image", "Config": {"Image": "caddy:fixed"}}
        self.events = []
        self.fail = None
        self.built = False
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(deploy.backup, "preflight", return_value=self.app))
        self.stack.enter_context(patch.object(deploy, "output", side_effect=self.output))
        self.stack.enter_context(patch.object(deploy, "inspect", side_effect=self.inspect))
        self.stack.enter_context(patch.object(deploy.subprocess, "run", side_effect=self.fake_run))
        self.stack.enter_context(
            patch.object(deploy.backup, "stopped_archive", side_effect=self.archive)
        )
        self.stack.enter_context(
            patch.object(deploy.backup, "verify_archive", return_value="verified-hash")
        )
        self.stack.enter_context(
            patch.object(deploy, "verify_service", return_value={"https": "verified"})
        )
        self.stack.enter_context(patch.object(deploy, "smoke_image", side_effect=self.smoke))
        self.stack.enter_context(
            patch.object(
                deploy.shutil,
                "disk_usage",
                return_value=type("Disk", (), {"free": 100 * 1024**3})(),
            )
        )

    def config(self):
        sha = (self.root / ".env").read_text().splitlines()[0].split("=")[1]
        return {
            "services": {
                deploy.APP: {
                    "image": "frontend:" + sha,
                    "build": {"context": str(self.root / ("ordered-lora-" + sha))},
                    "environment": {"CIF_IMAGE_TAG": sha, "KEEP_SETTING": "unchanged"},
                },
                deploy.EDGE: {
                    "image": "caddy:fixed",
                    "environment": {"CIF_TLS_HOSTNAME": "image-studio.lan"},
                },
            }
        }

    def inspect(self, container):
        return self.edge if container == "edge" else self.app

    def output(self, *args):
        if args[0] == "git":
            if "get-url" in args:
                return "https://github.com/astigmatism/comfyui-image-frontend.git\n"
            if "branch" in args:
                return "main\n" if args[2].endswith("/source") else ""
            if "status" in args or "merge-base" in args:
                return ""
            if "rev-parse" in args:
                return (
                    FROZEN if args[2].endswith("/source") else args[2].split("ordered-lora-")[-1]
                ) + "\n"
        if args[0] == "du":
            return "1000 data"
        if args[0] == "openssl":
            return "OK"
        if args[0:3] == ("docker", "image", "inspect"):
            image = (
                "new-image"
                if args[-1].endswith(NEW)
                else ("edge-image" if args[-1] == "caddy:fixed" else "old-image")
            )
            return json.dumps([{"Id": image}])
        if "config" in args:
            return json.dumps(self.config())
        if "ps" in args:
            return "edge\n"
        raise AssertionError(args)

    def fake_run(self, args, **_kwargs):
        if args[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(
                args, 0 if self.built else 1, stdout=json.dumps([{"Id": "new-image"}]).encode()
            )
        phase = "build" if "build" in args else "up" if "up" in args else "worktree"
        self.events.append(phase)
        if self.fail == phase:
            raise subprocess.CalledProcessError(1, args)
        if phase == "build":
            self.built = True
        return subprocess.CompletedProcess(args, 0)

    def archive(self, _app, _source, archive, _timeout, _health, report):
        self.events.append("backup")
        if self.fail == "backup":
            raise RuntimeError("archive failed; old app restarted")
        archive.write_bytes(b"archive fixture")
        report("restarted")

    def smoke(self, _image, user, _log):
        self.assertEqual(user, "1000:1000")
        self.events.append("image-smoke")
        if self.fail == "image-smoke":
            raise RuntimeError("Candidate image failed isolated startup")

    def assert_restored(self):
        for name, expected in self.original.items():
            self.assertEqual((self.root / name).read_bytes(), expected)
        self.assertFalse((self.root / ".deployment-update.lock").exists())

    def test_success_builds_before_backup_and_publishes_verified_receipt(self):
        deploy.deploy(self.root, NEW)
        self.assertEqual(self.events, ["worktree", "build", "image-smoke", "backup", "up"])
        status = json.loads(
            next((self.root / ".deployment-backups").glob("*/deployment-status.json")).read_text()
        )
        self.assertEqual(status["phase"], "complete")
        self.assertEqual(status["exit_code"], 0)
        self.assertIn(NEW, (self.root / ".env").read_text())
        self.assertFalse((self.root / ".deployment-update.lock").exists())

    def test_build_failure_restores_config_without_stopping_app(self):
        self.fail = "build"
        with self.assertRaises(subprocess.CalledProcessError):
            deploy.deploy(self.root, NEW)
        self.assertNotIn("backup", self.events)
        self.assertNotIn("up", self.events)
        self.assert_restored()

    def test_image_startup_failure_preserves_live_app_before_backup_or_cutover(self):
        self.fail = "image-smoke"
        with self.assertRaisesRegex(RuntimeError, "isolated startup"):
            deploy.deploy(self.root, NEW)
        self.assertNotIn("backup", self.events)
        self.assertNotIn("up", self.events)
        self.assert_restored()
        status = json.loads(
            next((self.root / ".deployment-backups").glob("*/deployment-status.json")).read_text()
        )
        self.assertEqual(status["phase"], "failed")
        self.assertEqual(status["failed_phase"], "image-smoke")
        self.assertEqual(status["exit_code"], 1)

    def test_backup_failure_restores_config_without_cutover(self):
        self.fail = "backup"
        with self.assertRaisesRegex(RuntimeError, "archive failed"):
            deploy.deploy(self.root, NEW)
        self.assertNotIn("up", self.events)
        self.assert_restored()

    def test_up_failure_does_not_roll_back_potentially_migrated_data_or_image(self):
        self.fail = "up"
        with self.assertRaises(subprocess.CalledProcessError):
            deploy.deploy(self.root, NEW)
        self.assertIn(NEW, (self.root / ".env").read_text())
        failure = json.loads(
            next((self.root / ".deployment-backups").glob("*/failure.json")).read_text()
        )
        self.assertTrue(failure["cutover_attempted"])
        self.assertFalse(failure["data_restored"])

    def test_check_only_does_not_mutate_configuration_or_stop_app(self):
        deploy.deploy(self.root, NEW, check_only=True)
        self.assertEqual(self.events, [])
        self.assert_restored()
        self.assertFalse((self.root / ".deployment-backups").exists())

    def test_already_current_does_not_build_backup_or_restart(self):
        deploy.deploy(self.root, OLD)
        self.assertEqual(self.events, [])
        self.assert_restored()

    def test_existing_unrecorded_image_is_not_overwritten(self):
        self.built = True
        with self.assertRaisesRegex(RuntimeError, "without a deployment receipt"):
            deploy.deploy(self.root, NEW)
        self.assertNotIn("build", self.events)
        self.assert_restored()

    def test_configuration_gate_rejects_unrelated_changes(self):
        before = self.config()
        after = copy.deepcopy(before)
        after["services"][deploy.EDGE]["image"] = "caddy:unexpected"
        with self.assertRaisesRegex(RuntimeError, "Unexpected Compose change"):
            deploy.verify_candidate(before, after, self.root / ("ordered-lora-" + NEW), NEW)


class MaintenanceContextTests(unittest.TestCase):
    def test_verified_same_path_container_is_accepted(self):
        root = Path("/home/owner/project")
        own = {
            "Config": {
                "Hostname": deploy.backup.socket.gethostname(),
                "User": f"{os.getuid()}:{os.getgid()}",
            },
            "HostConfig": {"Privileged": False},
            "Mounts": [
                {"Destination": p, "Source": p, "Type": "bind", "RW": True}
                for p in (str(root), "/var/run/docker.sock")
            ],
        }
        with patch.object(deploy.backup, "inspect", return_value=own):
            deploy.backup.execution_context(root, "verified-job")
            own["Mounts"][0]["Source"] = "/host" + str(root)
            with self.assertRaisesRegex(RuntimeError, "identical native host paths"):
                deploy.backup.execution_context(root, "verified-job")


if __name__ == "__main__":
    unittest.main()
