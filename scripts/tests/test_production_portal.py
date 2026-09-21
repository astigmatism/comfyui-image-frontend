"""Portal integration waits for the existing two-file deployment transaction."""
# ruff: noqa: S603, S607

import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "launch", ROOT / "scripts/launch-production-update.py"
)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class PortalWaitTests(unittest.TestCase):
    def setUp(self):
        self.commands = []
        self.state = {"Running": False, "Status": "exited", "ExitCode": 0}
        self.result = {"phase": "complete", "exit_code": 0, "https": "verified"}

    def docker(self, command, **kwargs):
        self.commands.append(command)
        self.assertEqual(kwargs["env"], {"DOCKER_CONFIG": "private-config"})
        if command[1] == "inspect":
            output = json.dumps(self.state)
        elif "--follow" in command:
            # docker logs can exit 0 even when the application job failed.
            self.assertEqual(kwargs["timeout"], 1800)
            output = ""
        else:
            output = json.dumps(self.result) + "\n"
        return subprocess.CompletedProcess(command, 0, stdout=output)

    def wait(self):
        with patch.object(launch.subprocess, "run", side_effect=self.docker):
            return launch.wait_for_job("job-id", {"DOCKER_CONFIG": "private-config"})

    def test_waits_for_job_and_verified_result_before_success(self):
        self.assertEqual(self.wait(), 0)
        self.assertIn("--follow", self.commands[0])
        self.assertEqual(self.commands[1], ["docker", "wait", "job-id"])
        self.assertEqual(self.commands[2][1], "inspect")
        self.assertEqual(self.commands[3], ["docker", "logs", "--tail", "20", "job-id"])

    def test_child_failure_propagates_even_when_log_command_succeeds(self):
        self.state["ExitCode"] = 7
        self.assertEqual(self.wait(), 7)

    def test_structured_cause_precedes_generic_failure_for_portal_parser(self):
        self.state["ExitCode"] = 1
        self.result = {
            "phase": "failed",
            "failed_phase": "recovery-verification",
            "error": "Application readiness failed within 120 seconds",
            "recovery": "restart-attempted",
        }
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.assertEqual(self.wait(), 1)
        first = captured.getvalue().splitlines()[0]
        self.assertIn("Application readiness failed within 120 seconds", first)
        self.assertIn("restart-attempted", first)
        self.assertIn("Deployment job: job-id", first)

    def test_check_only_rejects_restart_before_any_docker_call(self):
        with (
            patch(
                "sys.argv",
                [
                    "launcher",
                    "--source-view",
                    "/unused",
                    "--sha",
                    "a" * 40,
                    "--check-only",
                    "--restart",
                ],
            ),
            patch.object(launch.subprocess, "run") as run,
            patch("sys.stderr", io.StringIO()),
            self.assertRaises(SystemExit) as result,
        ):
            launch.main()
        self.assertEqual(result.exception.code, 2)
        run.assert_not_called()

    def test_running_child_is_never_reported_as_success(self):
        self.state.update(Running=True, Status="running")
        with self.assertRaisesRegex(RuntimeError, "has not finished"):
            self.wait()

    def test_zero_exit_without_verified_result_is_rejected(self):
        self.result = {"phase": "reconciling"}
        with self.assertRaisesRegex(RuntimeError, "without a verified result"):
            self.wait()

    def test_already_current_success_requires_verification_result(self):
        self.result = {"outcome": "already-current", "https": "verified"}
        self.assertEqual(self.wait(), 0)

    def test_timeout_fails_without_stopping_or_relaunching_the_child(self):
        with (
            patch.object(
                launch.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("docker logs", 1800),
            ) as command,
            self.assertRaisesRegex(RuntimeError, "do not launch another update"),
        ):
            launch.wait_for_job("job-id", {})
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0][:3], ["docker", "logs", "--follow"])


class PortalEntrypointTests(unittest.TestCase):
    def test_entrypoint_selects_production_wait_flow_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(ROOT / "update_production_portal", root)
            (root / "update_production").write_text(
                '#!/usr/bin/env bash\nprintf "arguments:%s\\n" "$*"\nexit 7\n'
            )
            (root / "update_production").chmod(0o755)
            result = subprocess.run(
                [str(root / "update_production_portal")],
                env=dict(os.environ, SERVICE_PORTAL_UPDATE_DELEGATED="1"),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout.strip(), "arguments:--wait --restart")
            self.assertIn("Error:", result.stderr)

    def test_entrypoint_requires_portal_context(self):
        env = dict(os.environ)
        env.pop("SERVICE_PORTAL_UPDATE_DELEGATED", None)
        result = subprocess.run(
            [str(ROOT / "update_production_portal")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error:", result.stderr)


@unittest.skipUnless(os.environ.get("CIF_RUN_DOCKER_TESTS") == "1", "opt-in real Docker tests")
class PortalDockerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = "cif-portal-runner-test:" + uuid.uuid4().hex
        subprocess.run(
            ["docker", "build", "-q", "-t", cls.image, str(ROOT / "deployment/runner")],
            check=True,
            timeout=180,
        )

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker", "image", "rm", cls.image], check=True, timeout=30)

    def test_runner_has_required_tools(self):
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "bash",
                self.image,
                "-ec",
                "git --version; python3 --version; docker --version; docker compose version",
            ],
            check=True,
            timeout=30,
        )

    def test_full_portal_bootstrap_in_actual_runner(self):
        # Fake only remote Git content. Run real Bash, Alpine mktemp and Python,
        # starting from the exact Portal entrypoint as its non-root identity.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o755)
            (root / "source").mkdir(mode=0o755)
            (root / "bin").mkdir(mode=0o755)
            (root / "source").chmod(0o755)
            (root / "bin").chmod(0o755)
            for name in ("update_production", "update_production_portal"):
                shutil.copy2(ROOT / name, root)
            (root / "bin/git").write_text(
                '#!/bin/sh\nset -eu\ntest "$1" = -C\ntest "$2" = /project/source\n'
                'shift 2\nprintf "git:%s\\n" "$*" >> /tmp/git-calls\n'
                'case "$*" in\n'
                '"remote get-url origin") '
                "echo https://github.com/astigmatism/comfyui-image-frontend.git;;\n"
                '"fetch origin main") :;;\n'
                '"rev-parse --verify origin/main^{commit}") echo ' + "a" * 40 + ";;\n"
                '"show ' + "a" * 40 + ':scripts/launch-production-update.py") '
                "cat /project/launcher-fixture.py;;\n"
                "*) exit 99;;\nesac\n"
            )
            (root / "bin/git").chmod(0o755)
            (root / "launcher-fixture.py").write_text(
                "import json, os, sys\n"
                'assert sys.argv[1:] == ["--source-view", "/project/source", "--sha", '
                + repr("a" * 40)
                + ', "--wait", "--restart"]\n'
                "assert os.getuid() == 1000\n"
                'print(json.dumps({"bootstrap": "executed", "uid": os.getuid()}))\n'
                'sys.exit(int(os.environ["FIXTURE_EXIT_CODE"]))\n'
            )
            (root / "launcher-fixture.py").chmod(0o644)
            for exit_code in (0, 7):
                with self.subTest(exit_code=exit_code):
                    result = subprocess.run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--user",
                            "1000:1000",
                            "--mount",
                            f"type=bind,source={root},target=/project,readonly",
                            "--env",
                            "SERVICE_PORTAL_UPDATE_DELEGATED=1",
                            "--env",
                            f"FIXTURE_EXIT_CODE={exit_code}",
                            "--env",
                            "PATH=/project/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                            "--entrypoint",
                            "bash",
                            self.image,
                            "-c",
                            "/project/update_production_portal; code=$?; "
                            "test -z \"$(find /tmp -maxdepth 1 -name 'cif-launch.*')\" || exit 98; "
                            'cat /tmp/git-calls; exit "$code"',
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, exit_code, result.stderr)
                    self.assertIn('"bootstrap": "executed"', result.stdout)
                    calls = [line for line in result.stdout.splitlines() if line.startswith("git:")]
                    self.assertEqual(
                        calls,
                        [
                            "git:remote get-url origin",
                            "git:fetch origin main",
                            "git:rev-parse --verify origin/main^{commit}",
                            "git:show " + "a" * 40 + ":scripts/launch-production-update.py",
                        ],
                    )

    def test_real_container_exit_and_verified_result_determine_portal_status(self):
        cases = [
            ('{"phase":"complete","exit_code":0,"https":"verified"}', 0, 0),
            ('{"phase":"failed","exit_code":7}', 7, 7),
            ('{"phase":"reconciling"}', 0, None),
        ]
        for result, exit_code, expected in cases:
            with self.subTest(exit_code=exit_code, result=result):
                container = subprocess.run(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--entrypoint",
                        "sh",
                        self.image,
                        "-c",
                        'sleep 1; printf "%s\\n" "$1"; exit "$2"',
                        "portal-test",
                        result,
                        str(exit_code),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                ).stdout.strip()
                started = time.monotonic()
                try:
                    if expected is None:
                        with self.assertRaisesRegex(RuntimeError, "without a verified result"):
                            launch.wait_for_job(container, os.environ.copy())
                    else:
                        self.assertEqual(
                            launch.wait_for_job(container, os.environ.copy()), expected
                        )
                    self.assertGreater(time.monotonic() - started, 0.2)
                finally:
                    subprocess.run(["docker", "rm", "-f", container], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
