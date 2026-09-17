"""Real Git permission regression, plus opt-in Docker build/startup regression."""

# ruff: noqa: S603, S607
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "deploy_permissions", ROOT / "scripts/production-update.py"
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class CheckoutPermissionsTests(unittest.TestCase):
    def test_real_git_checkout_is_readable_while_records_remain_private(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
            (source / "backend").mkdir()
            (source / "backend/code.py").write_text("print('code')\n")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            sha = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
            ).strip()
            worktree = root / "release"
            original_umask = os.umask(0o077)
            try:
                with (root / "private.log").open("w") as log:
                    deploy.create_release_worktree(source, worktree, sha, log)
                (root / "private.json").write_text("private record")
            finally:
                os.umask(original_umask)
            self.assertEqual((worktree / "backend/code.py").stat().st_mode & 0o777, 0o644)
            self.assertEqual((worktree / "backend").stat().st_mode & 0o777, 0o755)
            self.assertEqual((root / "private.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / "private.log").stat().st_mode & 0o777, 0o600)


@unittest.skipUnless(
    os.environ.get("CIF_RUN_DOCKER_TESTS") == "1", "set CIF_RUN_DOCKER_TESTS=1 for image regression"
)
class RestrictiveImageTests(unittest.TestCase):
    def test_private_build_context_fails_without_fix_and_starts_with_fix(self):
        tags = [
            "cif-permissions-negative:" + uuid.uuid4().hex,
            "cif-permissions-fixed:" + uuid.uuid4().hex,
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = root / "context"
            context.mkdir(mode=0o700)
            tracked = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"]).split(
                b"\0"
            )
            for entry in tracked:
                if not entry:
                    continue
                relative = Path(os.fsdecode(entry))
                source = ROOT / relative
                if not source.is_file():
                    continue
                dest = context / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
                dest.chmod(0o700 if source.stat().st_mode & 0o111 else 0o600)
            for path in context.rglob("*"):
                if path.is_dir():
                    path.chmod(0o700)
            fixed = (context / "Dockerfile").read_text()
            self.assertIn("chmod -R a+rX /app && ", fixed)
            negative = fixed.replace("chmod -R a+rX /app && ", "")
            try:
                with (root / "docker.log").open("w+") as log:
                    for tag, dockerfile in zip(tags, (negative, fixed), strict=True):
                        (context / "Dockerfile").write_text(dockerfile)
                        subprocess.run(
                            ["docker", "build", "-t", tag, str(context)],
                            check=True,
                            timeout=600,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                        )
                    with self.assertRaisesRegex(RuntimeError, "failed isolated startup"):
                        deploy.smoke_image(tags[0], "1000:1000", log, timeout=30)
                    deploy.smoke_image(tags[1], "1000:1000", log)
                    deploy.smoke_image(tags[1], "12345:23456", log)
            except Exception:
                print((root / "docker.log").read_text()[-10000:])
                raise
            finally:
                for tag in tags:
                    subprocess.run(
                        ["docker", "image", "rm", tag],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )


if __name__ == "__main__":
    unittest.main()
