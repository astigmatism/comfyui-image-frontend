#!/usr/bin/env python3
# ruff: noqa: S603, S607
"""Pinned-source releases for Samus, with no persistent source checkout."""

import argparse
import copy
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "backup", Path(__file__).with_name("backup-production-bind.py")
)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
APP, EDGE = backup.APP, backup.EDGE
PROJECT = "comfyui-image-frontend"
REPOSITORY = "https://github.com/astigmatism/comfyui-image-frontend.git"
ORIGIN = "https://192.168.1.5:8443"
REVISION = "org.opencontainers.image.revision"
LAST_READINESS = {"reason": "not-probed"}
require, output, inspect = backup.require, backup.output, backup.inspect


def compose_command(root):
    return [
        "docker",
        "compose",
        "--project-directory",
        str(root),
        "--env-file",
        "/dev/null",
        "-p",
        PROJECT,
        "-f",
        str(root / "compose.yaml"),
    ]


def atomic_write(path, contents):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".update-")
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_compose(root):
    path = root / "compose.yaml"
    require(path.is_file() and not path.is_symlink(), "Expected a regular compose.yaml")
    config = json.loads(path.read_text())
    require(set(config["services"]) == {APP, EDGE}, "Unexpected Compose service scope")
    require(all("build" not in v for v in config["services"].values()), "Unexpected build context")
    require(isinstance(config["services"][APP].get("labels"), dict), "Expected mapping labels")
    require(
        config["services"][EDGE].get("labels", {}).get("io.service-portal.update.enabled")
        == "false",
        "Edge must remain excluded from updates",
    )
    return config


def candidate_config(before, sha):
    after = copy.deepcopy(before)
    app = after["services"][APP]
    app["image"] = "local/comfyui-image-frontend:" + sha
    app["labels"][REVISION] = sha
    return after


def portal_labels(revision, user):
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "Expected full runner revision")
    require(re.fullmatch(r"[1-9][0-9]*:[0-9]+", user), "Expected numeric non-root owner")
    return {
        "io.service-portal.update.enabled": "true",
        "io.service-portal.update.script": "update_production_portal",
        "io.service-portal.update.image": "local/comfyui-image-frontend-release:" + revision,
        "io.service-portal.update.user": user,
    }


def mounts_equal(a, b):
    return sorted(a, key=lambda m: m["Destination"]) == sorted(b, key=lambda m: m["Destination"])


def run(command, log, timeout=180, **kwargs):
    return subprocess.run(
        command, check=True, timeout=timeout, stdout=log, stderr=subprocess.STDOUT, **kwargs
    )


def fetch_source(source, sha, deployed, log):
    # The scratch directory is inside this container, never on the host home mount.
    run(["git", "init", "-q", str(source)], log)
    run(["git", "-C", str(source), "remote", "add", "origin", REPOSITORY], log)
    run(
        [
            "git",
            "-C",
            str(source),
            "fetch",
            "--no-tags",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main",
        ],
        log,
    )
    target = sha or output("git", "-C", str(source), "rev-parse", "origin/main").strip()
    require(re.fullmatch(r"[0-9a-f]{40}", target), "Expected a full target SHA")
    # Both checks use the same fetched main snapshot, never a second branch resolution.
    output("git", "-C", str(source), "merge-base", "--is-ancestor", deployed, target)
    output("git", "-C", str(source), "merge-base", "--is-ancestor", target, "origin/main")
    run(["git", "-C", str(source), "checkout", "-q", "--detach", target], log, umask=0o022)
    require(
        output("git", "-C", str(source), "rev-parse", "HEAD").strip() == target,
        "Source revision mismatch",
    )
    require(not output("git", "-C", str(source), "status", "--porcelain"), "Source is not clean")
    return target


def smoke_image(image_id, runtime_user, log, timeout=90):
    """Start the exact candidate as the production UID with disposable data only."""
    require(
        re.fullmatch(r"[1-9][0-9]*:[0-9]+", runtime_user),
        "Image startup check requires an explicit non-root numeric UID:GID",
    )
    uid, gid = runtime_user.split(":")
    name = f"cif-image-smoke-{time.time_ns()}"
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--user",
        runtime_user,
        "--read-only",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        f"/data:rw,nosuid,nodev,mode=0700,uid={uid},gid={gid}",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,mode=1777",  # noqa: S108 - isolated container tmpfs, no host path
        image_id,
    ]
    probe = """
import app, json, sqlite3, urllib.request
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path
assert Path(app.__file__).is_relative_to(Path('/app/backend')), app.__file__
base = 'http://127.0.0.1:8000'
def read(path):
    return urllib.request.urlopen(base + path, timeout=3).read()
health = json.loads(read('/api/health'))
assert health['status'] == 'ok' and health['database'] and health['worker']['ready']
with sqlite3.connect('file:/data/app.db?mode=ro', uri=True) as db:
    assert db.execute('SELECT COUNT(*) FROM alembic_version').fetchone()[0] == 1
manifest = json.loads(read('/build.json'))
for key in ('app', 'styles', 'lora_stack'):
    assert read(manifest['assets'][key])
print('Candidate import, migrations, database, worker and assets passed as configured UID.')
"""
    try:
        with tempfile.NamedTemporaryFile(mode="w", prefix="cif-smoke-env-") as env_file:
            env_file.write("CIF_SESSION_SECRET=" + secrets.token_hex(32) + "\n")
            env_file.write("CIF_BOOTSTRAP_ADMIN_USERNAME=smoke\n")
            env_file.write("CIF_BOOTSTRAP_ADMIN_TEMPORARY_PASSWORD=" + secrets.token_hex(24) + "\n")
            env_file.write(
                'CIF_COMFYUI_INSTANCES=[{"id":"smoke","label":"Smoke","base_url":"http://127.0.0.1:9"}]\n'
            )
            env_file.flush()
            command[-1:-1] = ["--env-file", env_file.name]
            subprocess.run(command, check=True, timeout=30, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not inspect(name)["State"]["Running"]:
                break
            checked = subprocess.run(
                ["docker", "exec", name, "python", "-c", probe],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if checked.returncode == 0:
                log.write(checked.stdout)
                log.flush()
                return
            time.sleep(2)
        subprocess.run(
            ["docker", "logs", "--tail", "60", name],
            timeout=15,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        raise RuntimeError(
            "Candidate image failed isolated startup; original app remains in service"
        )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            timeout=30,
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
        )


def application_ready(app_id):
    """A bounded direct probe; operational failures are data, not structural failures."""
    code = """
import json, urllib.request, urllib.error
from app.config import get_settings
try:
    response = urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)
except urllib.error.HTTPError as error:
    response = error
health = json.load(response)
health['explicit_runtime'] = get_settings().comfyui_instance_configuration_mode == 'explicit'
print(json.dumps(health))
"""
    try:
        checked = subprocess.run(
            ["docker", "exec", app_id, "python", "-c", code],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if checked.returncode:
            LAST_READINESS.clear()
            LAST_READINESS.update(reason="application-probe-unavailable")
            return False
        health = json.loads(checked.stdout)
        worker = health.get("worker", {})
        LAST_READINESS.clear()
        LAST_READINESS.update(
            database=bool(health.get("database")),
            worker_ready=bool(worker.get("ready")),
            worker_state=worker.get("state")
            if worker.get("state")
            in {
                "not_started",
                "recovering",
                "running",
                "backing_off",
                "stopping",
                "stopped",
                "failed",
            }
            else "unknown",
            automation_ready=bool(health.get("automation", {}).get("ready")),
            explicit_runtime=bool(health.get("explicit_runtime")),
        )
        return bool(
            health.get("status") == "ok"
            and health.get("database")
            and health.get("explicit_runtime")
            and worker.get("state") == "running"
            and worker.get("ready")
            and worker.get("dispatcher_running")
            and worker.get("heartbeat_fresh")
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        LAST_READINESS.clear()
        LAST_READINESS.update(reason="application-probe-unavailable")
        return False


def wait_for_application(app_id, timeout):
    deadline = time.monotonic() + timeout
    while True:
        if application_ready(app_id):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(2, remaining))


def restart_application(app_id, log):
    subprocess.run(
        ["docker", "restart", "--time", "180", app_id],
        check=True,
        timeout=210,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def recover_application(app, report, log):
    if application_ready(app["Id"]):
        report("readiness", recovery="not-needed")
        return False
    readiness = dict(LAST_READINESS)
    # The existing Portal selects the first log line containing "failed" as
    # its error summary. Reserve that word for the eventual human Error line.
    if readiness.get("worker_state") == "failed":
        readiness["worker_state"] = "unavailable"
    report("recovery-wait", recovery="waiting-up-to-60-seconds", readiness=readiness)
    if wait_for_application(app["Id"], 60):
        report("readiness", recovery="recovered-without-restart")
        return False
    report("recovery-restart", recovery="restart-attempted")
    restart_application(app["Id"], log)
    report("recovery-verification", recovery="restart-attempted")
    return True


def verify_service(compose, config, root, image_id, edge_id):
    app_id = output(*compose, "ps", "--all", "-q", APP).strip()
    require(bool(app_id), "App container missing")
    require(inspect(app_id)["Image"] == image_id, "Running app image differs from selected image")
    require(output(*compose, "ps", "--all", "-q", EDGE).strip() == edge_id, "Edge identity changed")
    require(
        wait_for_application(app_id, 120),
        "Application readiness failed within 120 seconds: " + json.dumps(LAST_READINESS),
    )
    deadline = time.monotonic() + 120
    origin = ORIGIN
    curl = [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "10",
        "--noproxy",
        "*",
        "--cacert",
        str(credentials_dir(root) / "tls/ca.crt"),
    ]

    def read_url(path, *, discard=False):
        remaining = deadline - time.monotonic()
        require(remaining > 0, "Final verification exceeded 120 seconds")
        bounded = list(curl)
        bounded[bounded.index("--max-time") + 1] = str(min(10, remaining))
        return output(*bounded, *(["--output", "/dev/null"] if discard else []), origin + path)

    try:
        health = json.loads(read_url("/api/health"))
    except (RuntimeError, ValueError) as error:
        raise RuntimeError(
            "Trusted HTTPS verification failed while the application is ready"
        ) from error
    require(
        health["status"] == "ok" and health["database"] and health["worker"]["state"] == "running",
        "HTTPS health check failed",
    )
    page = read_url("/")
    manifest = json.loads(read_url("/build.json"))
    image_manifest = json.loads(
        output("docker", "exec", app_id, "cat", "/app/frontend/dist/build.json")
    )
    require(manifest == image_manifest, "Served manifest differs from running image")
    for key, path in manifest["assets"].items():
        require(path.startswith("/assets/") and not path.startswith("//"), "Unexpected asset path")
        if key in ("app", "styles"):
            require(path in page, "HTML asset does not match manifest")
        read_url(path, discard=True)
    for container in (app_id, edge_id):
        while inspect(container)["State"].get("Health", {}).get("Status") != "healthy":
            require(
                time.monotonic() < deadline,
                "TLS edge health failed while the application is ready"
                if container == edge_id
                else "Application container health did not recover",
            )
            time.sleep(2)
    return {
        "app_id": app_id,
        "app_started_at": inspect(app_id)["State"]["StartedAt"],
        "asset_version": manifest["asset_version"],
        "worker": "running",
        "https": "verified",
    }


def credentials_dir(root):
    return root.parent.parent / "credentials" / root.name


def fingerprints(root):
    paths = [root / "Caddyfile", *credentials_dir(root).rglob("*")]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


def structural_preflight(root):
    require(
        output("docker", "info", "--format", "{{.Name}}").strip() == "samus",
        "Expected the Samus production Docker daemon",
    )
    app = backup.preflight(
        root, PROJECT, os.environ.get("CIF_MAINTENANCE_CONTAINER"), require_health=False
    )
    before = read_compose(root)
    edge_id = output(*compose_command(root), "ps", "--all", "-q", EDGE).strip()
    edge = inspect(edge_id)
    deployed = app["Config"].get("Labels", {}).get(REVISION, "")
    require(re.fullmatch(r"[0-9a-f]{40}", deployed), "Expected the live release revision label")
    require(
        before["services"][APP]["labels"].get(REVISION) == deployed,
        "Saved and running release revisions differ",
    )
    require(
        re.fullmatch(r"[1-9][0-9]*:[0-9]+", app["Config"]["User"]),
        "Expected an explicit production UID:GID",
    )
    for live in (app, edge):
        require(
            json.loads(output("docker", "image", "inspect", live["Config"]["Image"]))[0]["Id"]
            == live["Image"],
            "An existing image tag was overwritten",
        )
    state_path = root / "release-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        require(
            state.get("status") == "healthy"
            and state.get("candidate") == deployed
            and state.get("image") == app["Config"]["Image"]
            and state.get("image_id") == app["Image"],
            "Release state differs from live app",
        )
    ca, certificate, private_key = backup.tls_files(root)
    hostname = ORIGIN.split("//", 1)[1].split(":", 1)[0]
    ipaddress.ip_address(hostname)
    output(
        "openssl",
        "verify",
        "-CAfile",
        str(ca),
        "-verify_ip",
        hostname,
        str(certificate),
    )
    output("openssl", "x509", "-in", str(certificate), "-noout", "-checkend", "86400")
    require(
        output("openssl", "x509", "-in", str(certificate), "-noout", "-pubkey")
        == output("openssl", "pkey", "-in", str(private_key), "-pubout"),
        "TLS certificate and private key do not match",
    )
    return before, app, edge, deployed


def reconcile(compose, log):
    run(
        [
            *compose,
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--pull",
            "never",
            "--wait",
            "--wait-timeout",
            "120",
            APP,
        ],
        log,
    )


def restore_database(root, archive, directory):
    """Restore SQLite only; keep the data directory and every asset/upload in place."""
    backup.verify_archive(archive)
    with tempfile.TemporaryDirectory(prefix="database-", dir=directory) as scratch:
        scratch = Path(scratch)
        with tarfile.open(archive, "r:") as stream:
            members = {m.name.removeprefix("./"): m for m in stream.getmembers()}
            for name in ("app.db", "app.db-wal", "app.db-shm", "app.db-journal"):
                if name in members:
                    require(members[name].isfile(), "Unexpected archived database file")
                    with (
                        stream.extractfile(members[name]) as src,
                        (scratch / name).open("wb") as dst,
                    ):
                        shutil.copyfileobj(src, dst)
        # Consolidate any archived WAL into a standalone database before installation.
        with (
            closing(sqlite3.connect(scratch / "app.db")) as src,
            closing(sqlite3.connect(scratch / "restored.db")) as dst,
        ):
            src.backup(dst)
            require(
                dst.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "Restored database integrity check failed",
            )
        failed = directory / "database.after-cutover"
        failed.mkdir(mode=0o700)
        data = root / "data"
        # Rename on the data filesystem; avoid loading a large database into memory.
        fd, temporary = tempfile.mkstemp(prefix=".restore-", dir=data)
        try:
            with os.fdopen(fd, "wb") as dst, (scratch / "restored.db").open("rb") as src:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            for name in ("app.db", "app.db-wal", "app.db-shm", "app.db-journal"):
                path = data / name
                require(not path.is_symlink(), "Unexpected live database symlink")
                if path.exists():
                    path.rename(failed / name)
            os.replace(temporary, data / "app.db")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def restore_files(root, saved):
    for name, content in saved.items():
        if content is None:
            (root / name).unlink(missing_ok=True)
        elif not (root / name).exists() or (root / name).read_bytes() != content:
            atomic_write(root / name, content)


def build_image(source, sha, directory, log):
    image = "local/comfyui-image-frontend:" + sha
    run(
        [
            "git",
            "-C",
            str(source),
            "archive",
            "--format=tar.gz",
            "--output=" + str(directory / "source.tar.gz"),
            sha,
        ],
        log,
    )
    existing = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, timeout=30
    )
    if existing.returncode == 0:
        image_id = json.loads(existing.stdout)[0]["Id"]
        # Never overwrite a full-SHA tag, or accept an unrecorded image by label alone.
        receipts = directory.parent.glob("*/image.json")
        require(
            any(
                json.loads(p.read_text()) == {"sha": sha, "image": image, "image_id": image_id}
                for p in receipts
            ),
            "Target image exists without matching provenance",
        )
    else:
        run(
            ["docker", "build", "--label", REVISION + "=" + sha, "-t", image, str(source)],
            log,
            timeout=600,
        )
        image_id = json.loads(output("docker", "image", "inspect", image))[0]["Id"]
    atomic_write(
        directory / "image.json",
        json.dumps({"sha": sha, "image": image, "image_id": image_id}).encode(),
    )
    return image_id


def save_state(root, previous, sha, image, image_id):
    atomic_write(
        root / "release-state.json",
        json.dumps(
            {
                "previous": previous,
                "candidate": sha,
                "image": image,
                "image_id": image_id,
                "status": "healthy",
            },
            indent=2,
        ).encode()
        + b"\n",
    )


def transaction(root, sha, install, before, app, edge, deployed, source, directory, report, log):
    compose = compose_command(root)
    require(read_compose(root) == before, "Compose changed since structural preflight")
    saved = {
        name: (root / name).read_bytes() if (root / name).exists() else None
        for name in (
            "compose.yaml",
            "release-state.json",
            "release-config.json",
            "update_production",
            "update_production_portal",
        )
    }
    for name, content in saved.items():
        if content is not None:
            atomic_write(directory / (name + ".previous"), content)
    atomic_write(directory / "compose.previous.json", saved["compose.yaml"])
    baseline = fingerprints(root)
    stopped = False
    cutover = False
    archive = directory / "data.tar"
    try:
        if install:
            after = copy.deepcopy(before)
            owner = f"{os.getuid()}:{os.getgid()}"
            labels = portal_labels(install, owner)
            after["services"][APP]["labels"].update(labels)
            image_id = app["Image"]
            runner = json.loads(
                output("docker", "image", "inspect", labels["io.service-portal.update.image"])
            )[0]
            require(
                runner["Config"].get("Labels", {}).get(REVISION) == install,
                "Runner image revision mismatch",
            )
            for name in ("update_production", "update_production_portal"):
                atomic_write(root / name, Path(__file__).with_name(name).read_bytes())
                (root / name).chmod(0o755)
            atomic_write(
                root / "release-config.json",
                json.dumps(
                    {
                        "repository": REPOSITORY,
                        "branch": "main",
                        "runner_revision": install,
                        "runner_image": labels["io.service-portal.update.image"],
                    },
                    indent=2,
                ).encode()
                + b"\n",
            )
        else:
            after = candidate_config(before, sha)
            report("building", target_sha=sha)
            image_id = build_image(source, sha, directory, log)
            report("image-smoke")
            smoke_image(image_id, app["Config"]["User"], log)
        candidate_path = directory / "compose.candidate.json"
        atomic_write(candidate_path, json.dumps(after).encode())
        validation = compose.copy()
        validation[-1] = str(candidate_path)
        run([*validation, "config", "--quiet"], log)
        require(
            (root / "compose.yaml").read_bytes() == saved["compose.yaml"],
            "Configuration changed during preparation",
        )
        require(
            fingerprints(root) == baseline, "Operational configuration changed during preparation"
        )
        current = inspect(app["Id"])
        require(
            current["State"]["Running"]
            and current["Image"] == app["Image"]
            and mounts_equal(current["Mounts"], app["Mounts"]),
            "Original app changed",
        )
        require(
            output(*compose, "ps", "--all", "-q", EDGE).strip() == edge["Id"],
            "Edge identity changed",
        )
        if not install:
            report("backup")
            size = int(output("du", "-sk", str(root / "data")).split()[0]) * 1024
            require(
                shutil.disk_usage(root).free > size * 2 + 2 * 1024**3,
                "Insufficient backup and recovery disk space",
            )
            backup.stopped_archive(
                app["Id"],
                root / "data",
                archive,
                180,
                120,
                lambda phase: report("backup-" + phase),
                keep_stopped=True,
                log=log,
            )
            stopped = True
            report("backup-verifying")
            checksum = backup.verify_archive(archive)
            atomic_write(directory / "data.tar.sha256", (checksum + "  data.tar\n").encode())
        require(fingerprints(root) == baseline, "Operational configuration changed before cutover")
        require(
            (root / "compose.yaml").read_bytes() == saved["compose.yaml"],
            "Compose changed before cutover",
        )
        report("reconciling", cutover_attempted=True)
        cutover = True
        atomic_write(root / "compose.yaml", json.dumps(after, indent=2).encode() + b"\n")
        reconcile(compose, log)
        report("final-verification")
        verified = verify_service(compose, after, root, image_id, edge["Id"])
        backup.preflight(root, PROJECT, os.environ.get("CIF_MAINTENANCE_CONTAINER"))
        require(
            read_compose(root) == after and fingerprints(root) == baseline,
            "Operational configuration changed during verification",
        )
        live = inspect(verified["app_id"])
        require(
            live["Config"].get("Labels", {}).get(REVISION) == sha, "Live revision label mismatch"
        )
        if install:
            require(
                all(live["Config"]["Labels"].get(k) == v for k, v in labels.items()),
                "Portal labels were not published",
            )
        save_state(root, deployed, sha, after["services"][APP]["image"], image_id)
        report(
            "complete",
            exit_code=0,
            outcome="installed" if install else "updated",
            target_sha=sha,
            image_id=image_id,
            **verified,
        )
    except Exception:
        # Recovery must run even if a second signal or a full diagnostic disk intervenes.
        handlers = {
            sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            if cutover:
                run([*compose, "stop", "--timeout", "30", APP], log, timeout=45)
                ids = output(*compose, "ps", "--all", "-q", APP).split()
                require(
                    all(not inspect(c)["State"]["Running"] for c in ids),
                    "Could not stop candidate for rollback",
                )
                if not install:
                    restore_database(root, archive, directory)
                restore_files(root, saved)
                reconcile(compose, log)
                verify_service(compose, before, root, app["Image"], edge["Id"])
                report(
                    None,
                    recovery="rolled-back",
                    configuration_restored=True,
                    data_restored=not install,
                )
            else:
                if stopped:
                    backup.restart(app["Id"], 120)
                if install:
                    restore_files(root, {k: v for k, v in saved.items() if k != "compose.yaml"})
                verify_service(compose, before, root, app["Image"], edge["Id"])
                report(None, recovery="original-preserved")
        except Exception as recovery_error:
            with suppress(OSError):
                log.write(f"Recovery: {type(recovery_error).__name__}\n")
                log.flush()
            report(None, recovery="operator-required")
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        raise


def public_error(error, phase):
    # Public logs never include command arguments, output, paths from data or exception text.
    if isinstance(error, subprocess.TimeoutExpired):
        return f"command timeout in {phase}"
    return f"{type(error).__name__} in {phase}; see restricted deployment.log"


def deploy(root, sha=None, check_only=False, restart=False, install=None):
    require(not (check_only and restart), "--check-only cannot be combined with --restart")
    os.umask(0o077)
    os.environ.update(GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat", COMPOSE_DISABLE_ENV_FILE="1")
    directory = None
    log = None
    acquired = False
    lock = root / ".deployment-update.lock"
    status = {
        "target_sha": sha,
        "job": os.environ.get("CIF_MAINTENANCE_CONTAINER", "native-host"),
        "phase": "bootstrap",
        "recovery": "not-attempted",
        "started_at": time.time(),
        "cutover_attempted": False,
    }

    def report(phase, **fields):
        if phase is not None:
            status["phase"] = phase
        status.update(updated_at=time.time(), **fields)
        if directory:
            atomic_write(directory / "receipt.json", (json.dumps(status, indent=2) + "\n").encode())
        print(json.dumps(status), flush=True)

    try:
        require(
            root.is_absolute() and root.resolve() == root and root.is_dir(),
            "Expected an existing canonical deployment root",
        )
        require(
            root.stat().st_uid == os.getuid() and os.getuid() != 0,
            "Run as the deployment owner, not root",
        )
        require(not (root / "releases").is_symlink(), "Release directory must not be a symlink")
        # All modes, including installation and check-only, use the same atomic lock.
        report("lock")
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError as error:
            raise RuntimeError("Deployment lock exists; inspect the recorded job") from error
        acquired = True
        (lock / "owner.json").write_text(json.dumps({"job": status["job"], "pid": os.getpid()}))
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        directory = root / "releases" / (stamp + "-preflight")
        directory.mkdir(mode=0o700, parents=True)
        log = (directory / "deployment.log").open("w")
        report("structural-preflight")
        before, app, edge, deployed = structural_preflight(root)
        require(
            install or (root / "release-config.json").is_file(), "Install release tooling first"
        )
        if check_only:
            report("operational-preflight", previous_sha=deployed)
            verified = verify_service(compose_command(root), before, root, app["Image"], edge["Id"])
            report("complete", exit_code=0, outcome="check-passed", **verified)
            return
        with tempfile.TemporaryDirectory(prefix="cif-release-") as scratch:
            source = Path(scratch) / "source"
            report("release-fetch")
            sha = deployed if install else fetch_source(source, sha, deployed, log)
            renamed = directory.with_name(stamp + "-" + sha[:12])
            directory.rename(renamed)
            directory = renamed
            report("release-selected", target_sha=sha, release=str(directory.relative_to(root)))
            require(read_compose(root) == before, "Compose changed during source fetch")
            report("operational-preflight", previous_sha=deployed)
            # Installation changes labels only; it requires existing health.
            recovered = False if install else recover_application(app, report, log)
            verify_service(compose_command(root), before, root, app["Image"], edge["Id"])
            if recovered:
                report("readiness", recovery="restart-verified")
            if not install and sha == deployed:
                original = (root / "compose.yaml").read_bytes()
                baseline = fingerprints(root)
                if restart and not recovered:
                    report("requested-restart")
                    restart_application(app["Id"], log)
                report("final-verification")
                verified = verify_service(
                    compose_command(root), before, root, app["Image"], edge["Id"]
                )
                require(
                    (root / "compose.yaml").read_bytes() == original
                    and fingerprints(root) == baseline,
                    "Configuration changed during restart",
                )
                if restart or recovered:
                    require(
                        inspect(app["Id"])["State"]["StartedAt"] != app["State"]["StartedAt"],
                        "App start time did not change",
                    )
                report(
                    "complete",
                    exit_code=0,
                    outcome="restarted" if restart or recovered else "already-current",
                    **verified,
                )
            else:
                transaction(
                    root, sha, install, before, app, edge, deployed, source, directory, report, log
                )
    except Exception as error:
        phase = status["phase"]
        if log:
            with suppress(OSError):
                # Do not retain exception arguments from external commands.
                log.write(
                    f"\n{type(error).__name__}: "
                    + (
                        str(error)
                        if isinstance(error, RuntimeError)
                        else "command/system operation unsuccessful"
                    )
                    + "\n"
                )
                log.flush()
        print(
            f"Error: production update failed during {phase}; recovery {status['recovery']}; "
            f"retained job {status['job']} ({public_error(error, phase)}).",
            flush=True,
        )
        report("failed", exit_code=1, failed_phase=phase)
        raise
    finally:
        if log:
            log.close()
        if acquired:
            (lock / "owner.json").unlink(missing_ok=True)
            lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--sha")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--restart", action="store_true")
    mode.add_argument("--install")
    args = parser.parse_args()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, backup.interrupted)
    try:
        deploy(args.deploy_root, args.sha, args.check_only, args.restart, args.install)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
