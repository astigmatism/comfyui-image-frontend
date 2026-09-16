"""Recovery-path tests; no Docker daemon or production data is used."""

import importlib.util
import json
import signal
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "backup", Path(__file__).resolve().parents[1] / "backup-production-bind.py"
)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class RecoveryTests(unittest.TestCase):
    def run_archive(self, failure=None, fail_stop=False):
        events = []

        def run(command, **_kwargs):
            events.append(command[0:2])
            if failure is not None and (command[0] == "tar" or fail_stop):
                raise failure

        with (
            patch.object(backup.subprocess, "run", side_effect=run),
            patch.object(backup, "inspect", return_value={"State": {"Running": False}}),
            patch.object(backup, "restart", side_effect=lambda *_: events.append("restart")),
        ):
            try:
                backup.stopped_archive(
                    "original-id",
                    Path("/data"),
                    Path("/backup/data.tar"),
                    180,
                    120,
                    lambda phase: events.append(phase),
                )
            finally:
                self.assertIn("restart", events)
                self.assertEqual(events[-2:], ["restart", "restarted"])
        return events

    def test_success_restarts_before_returning_to_verification(self):
        self.assertEqual(
            self.run_archive(),
            ["stopping", ["docker", "stop"], "archiving", ["tar", "-C"], "restart", "restarted"],
        )

    def test_tar_failure_still_restarts_and_propagates_failure(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_archive(subprocess.CalledProcessError(2, "tar"))

    def test_archive_timeout_still_restarts(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_archive(subprocess.TimeoutExpired("tar", 180))

    def test_uncertain_stop_result_still_attempts_restart(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_archive(subprocess.TimeoutExpired("docker stop", 45), fail_stop=True)

    def test_termination_signal_handler_routes_through_restart(self):
        with (
            self.assertRaises(InterruptedError),
            patch.object(
                backup.subprocess,
                "run",
                side_effect=lambda *a, **kw: backup.interrupted(signal.SIGTERM, None),
            ),
            patch.object(backup, "restart") as restart,
        ):
            try:
                backup.stopped_archive(
                    "original-id",
                    Path("/data"),
                    Path("/backup/a"),
                    180,
                    120,
                    lambda phase: None,
                )
            finally:
                restart.assert_called_once_with("original-id", 120)

    def test_failed_status_write_does_not_skip_restart(self):
        with patch.object(backup, "restart") as restart:
            with self.assertRaises(OSError):
                backup.stopped_archive(
                    "original-id",
                    Path("/data"),
                    Path("/backup/a"),
                    180,
                    120,
                    lambda _: (_ for _ in ()).throw(OSError("disk full")),
                )
            restart.assert_called_once_with("original-id", 120)

    def test_failed_restart_cannot_produce_success(self):
        with (
            patch.object(backup.subprocess, "run"),
            patch.object(backup, "inspect", return_value={"State": {"Running": False}}),
            patch.object(backup, "restart", side_effect=RuntimeError("unhealthy")),
            self.assertRaisesRegex(RuntimeError, "unhealthy"),
        ):
            backup.stopped_archive(
                "id", Path("/data"), Path("/backup/a"), 180, 120, lambda phase: None
            )


class ArchiveTests(unittest.TestCase):
    def test_sqlite_wal_is_included_in_integrity_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            data.mkdir()
            (data / "assets").mkdir()
            db = sqlite3.connect(data / "app.db")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE sample (value TEXT)")
            db.execute("INSERT INTO sample VALUES ('committed in WAL')")
            db.commit()
            archive = root / "data.tar"
            with tarfile.open(archive, "w") as stream:
                stream.add(data, arcname=".")
            # Keep the source connection open so closing it cannot checkpoint the WAL.
            try:
                connect = sqlite3.connect

                def check_committed_rows(path):
                    copy = connect(path)
                    self.assertEqual(
                        copy.execute("SELECT value FROM sample").fetchall(), [("committed in WAL",)]
                    )
                    return copy

                with patch.object(backup.sqlite3, "connect", side_effect=check_committed_rows):
                    self.assertEqual(len(backup.verify_archive(archive)), 64)
                self.assertTrue((data / "app.db-wal").exists())
            finally:
                db.close()

    def test_bad_database_is_not_a_successful_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "assets").mkdir()
            (root / "app.db").write_bytes(b"invalid SQLite data" * 50)
            archive = root / "bad.tar"
            with tarfile.open(archive, "w") as stream:
                stream.add(root / "app.db", arcname="./app.db")
                stream.add(root / "assets", arcname="./assets")
            with self.assertRaises(sqlite3.DatabaseError):
                backup.verify_archive(archive)

    def test_missing_database_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "empty.tar"
            with tarfile.open(archive, "w"):
                pass
            with self.assertRaisesRegex(RuntimeError, "Missing database"):
                backup.verify_archive(archive)


class HostGateTests(unittest.TestCase):
    def test_translated_host_path_is_rejected_before_docker_calls(self):
        with (
            patch.object(backup.Path, "exists", return_value=False),
            patch.object(backup.Path, "resolve", return_value=Path("/host/home/owner/app")),
            patch.object(backup, "output") as output,
        ):
            with self.assertRaisesRegex(RuntimeError, "canonical native host"):
                backup.preflight(Path("/host/home/owner/app"), "project")
            output.assert_not_called()

    def test_agent_container_is_rejected_before_docker_calls(self):
        with (
            patch.object(backup.Path, "exists", return_value=True),
            patch.object(backup, "output") as output,
        ):
            with self.assertRaisesRegex(RuntimeError, "native Docker host"):
                backup.preflight(Path("/home/owner/app"), "project")
            output.assert_not_called()

    def test_wrong_mount_is_rejected_even_when_both_compose_configs_agree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            files = [str(root / p) for p in ("compose.yaml", "compose.ordered-lora.yaml")]
            definition = {
                "image": "app:sha",
                "volumes": [
                    {"type": "bind", "source": "/host" + str(root / "data"), "target": "/data"}
                ],
            }
            live = {
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "Config": {
                    "Image": "app:sha",
                    "Labels": {
                        "com.docker.compose.project": "project",
                        "com.docker.compose.project.working_dir": str(root),
                        "com.docker.compose.project.config_files": ",".join(files),
                    },
                },
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(root / "data"),
                        "Destination": "/data",
                        "RW": True,
                    }
                ],
            }

            def output(*args):
                if args[1:3] == ("context", "inspect"):
                    return json.dumps(
                        [{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
                    )
                if args[1] == "info":
                    return backup.socket.gethostname()
                if "config" in args:
                    return json.dumps(
                        {"services": {backup.APP: definition, backup.EDGE: definition}}
                    )
                return "existing-id"

            original_exists = Path.exists
            with (
                patch.object(
                    backup.Path,
                    "exists",
                    lambda p: (
                        False
                        if str(p) in ("/.dockerenv", "/run/.containerenv")
                        else original_exists(p)
                    ),
                ),
                patch.dict(backup.os.environ, {}, clear=True),
                patch.object(backup, "output", side_effect=output),
                patch.object(backup, "inspect", return_value=live),
                self.assertRaisesRegex(RuntimeError, "Host bind mismatch"),
            ):
                backup.preflight(root, "project")


if __name__ == "__main__":
    unittest.main()
