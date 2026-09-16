#!/usr/bin/env python3
# ruff: noqa: S603, S607
# Operator-run host tool: fixed executables on the owner's PATH, argument arrays,
# no shell interpolation, and deployment paths/container IDs verified before use.
"""Bounded, restart-safe backup for the documented host-native Path B deployment.

Run with nohup as shown in the runbook. This does not deploy, migrate, restore data,
or change configuration. The caller must exclude other deployment jobs first.
"""

import argparse
import fcntl
import hashlib
import json
import os
import signal
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

APP = "comfyui-image-frontend"
EDGE = "cif-tls-edge"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def output(*args):
    # Never echo command output on failure: inspect/config can contain secrets.
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, f"Command failed: {args[0]} {args[1]}")
    return result.stdout


def inspect(container):
    return json.loads(output("docker", "inspect", container))[0]


def preflight(root, project):
    require(
        not Path("/.dockerenv").exists() and not Path("/run/.containerenv").exists(),
        "Run on the native Docker host through SSH, not inside the agent container",
    )
    require(
        root.is_absolute() and root.resolve() == root and not str(root).startswith("/host/"),
        "Use the canonical native host deployment path",
    )
    require(
        root.stat().st_uid == os.getuid() and os.getuid() != 0,
        "Run as the deployment owner, not root",
    )
    require(
        not os.environ.get("DOCKER_HOST") and not os.environ.get("DOCKER_CONTEXT"),
        "Remove remote Docker environment overrides before using this host-only helper",
    )
    context = json.loads(output("docker", "context", "inspect"))[0]
    require(
        context["Endpoints"]["docker"]["Host"] == "unix:///var/run/docker.sock",
        "Expected the native host Docker socket",
    )
    require(
        output("docker", "info", "--format", "{{.Name}}").strip() == socket.gethostname(),
        "Docker daemon and execution host differ",
    )
    files = [root / "compose.yaml", root / "compose.ordered-lora.yaml"]
    compose = [
        "docker",
        "compose",
        "--project-directory",
        str(root),
        "--env-file",
        str(root / ".env"),
        "-p",
        project,
    ]
    for path in files:
        compose.extend(["-f", str(path)])
    config = json.loads(output(*compose, "config", "--format", "json"))
    require(set(config["services"]) == {APP, EDGE}, "Unexpected Compose service scope")
    containers = {}
    for service, definition in config["services"].items():
        ids = output(*compose, "ps", "--all", "-q", service).split()
        require(len(ids) == 1, f"Expected one existing container for {service}")
        live = inspect(ids[0])
        containers[service] = live
        require(
            live["State"]["Running"] and live["State"].get("Health", {}).get("Status") == "healthy",
            f"Restore existing {service} health before starting an update",
        )
        labels = live["Config"].get("Labels", {})
        require(
            labels.get("com.docker.compose.project") == project
            and labels.get("com.docker.compose.project.working_dir") == str(root)
            and labels.get("com.docker.compose.project.config_files") == ",".join(map(str, files)),
            f"Unexpected Compose identity for {service}",
        )
        require(
            live["Config"]["Image"] == definition["image"],
            f"Saved configuration does not match the running {service} image",
        )
        actual = {m["Destination"]: m for m in live["Mounts"]}
        requested = {m["target"]: m for m in definition.get("volumes", [])}
        require(set(actual) == set(requested), f"Unexpected storage mounts for {service}")
        for target, mount in requested.items():
            current = actual[target]
            require(
                mount["type"] == current["Type"] == "bind"
                and mount["source"] == current["Source"]
                and current["RW"] == (not mount.get("read_only", False)),
                f"Host bind mismatch at {service}:{target}",
            )
            require(Path(current["Source"]).exists(), f"Missing host source for {target}")
    app = containers[APP]
    require(
        len(app["Mounts"]) == 1
        and app["Mounts"][0]["Destination"] == "/data"
        and app["Mounts"][0]["Source"] == str(root / "data")
        and app["Mounts"][0]["RW"],
        "Expected the live deployment-root data bind",
    )
    require(
        not (root / "data").is_symlink()
        and (root / "data/app.db").is_file()
        and (root / "data/assets").is_dir(),
        "Expected the existing production database and assets",
    )
    edge_mounts = {m["Destination"]: Path(m["Source"]) for m in containers[EDGE]["Mounts"]}
    require(
        edge_mounts.get("/etc/caddy/Caddyfile", Path("/nonexistent")).is_file(),
        "Caddyfile must be an existing file",
    )
    certificates = edge_mounts.get("/etc/caddy/certificates")
    require(
        certificates == root / "data/certificates"
        and all((certificates / f).is_file() for f in ("ca.crt", "tls.crt", "tls.key")),
        "Expected existing production TLS material inside the data backup",
    )
    return app


def restart(app_id, timeout):
    subprocess.run(["docker", "start", app_id], check=True, timeout=40, stdout=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = inspect(app_id)["State"]
        if state["Running"] and state.get("Health", {}).get("Status") == "healthy":
            return
        time.sleep(2)
    raise RuntimeError("Original app was started but did not become healthy; inspect immediately")


def stopped_archive(app_id, source, archive, timeout, health_timeout, report):
    """The restart is part of the same host process, including stop/tar failures."""
    try:
        report("stopping")
        subprocess.run(
            ["docker", "stop", "--time", "30", app_id],
            check=True,
            timeout=45,
            stdout=subprocess.DEVNULL,
        )
        require(not inspect(app_id)["State"]["Running"], "App did not stop; refusing live archive")
        report("archiving")
        subprocess.run(
            ["tar", "-C", str(source), "-cf", str(archive), "."], check=True, timeout=timeout
        )
    finally:
        # A second interrupt must not skip recovery after the first interrupt.
        previous = {
            sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            # Restart precedes status-file writes: a full backup disk must not prevent it.
            restart(app_id, health_timeout)
            report("restarted")
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def verify_archive(archive):
    require(archive.stat().st_size > 0, "Empty archive")
    with (
        tarfile.open(archive, "r:") as stream,
        tempfile.TemporaryDirectory(dir=archive.parent) as temp,
    ):
        members = {member.name.removeprefix("./"): member for member in stream.getmembers()}
        require(
            "app.db" in members
            and members["app.db"].isfile()
            and "assets" in members
            and members["assets"].isdir(),
            "Missing database or assets",
        )
        # Check a disposable copy with all SQLite sidecars; never open the live DB here.
        for name in ("app.db", "app.db-wal", "app.db-shm", "app.db-journal"):
            if name in members:
                require(members[name].isfile(), f"Unexpected SQLite file type: {name}")
                with stream.extractfile(members[name]) as src, open(Path(temp) / name, "wb") as dst:
                    while block := src.read(1024 * 1024):
                        dst.write(block)
        connection = sqlite3.connect(Path(temp) / "app.db")
        try:
            require(
                connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "Backup SQLite integrity check failed",
            )
        finally:
            connection.close()
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def interrupted(signum, _frame):
    raise InterruptedError(f"Received signal {signum}; recovering the original app")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--archive-timeout", type=int, default=180)
    parser.add_argument("--health-timeout", type=int, default=120)
    args = parser.parse_args()
    require(args.archive_timeout > 0 and args.health_timeout > 0, "Timeouts must be positive")
    os.umask(0o077)
    app = preflight(args.deploy_root, args.project)
    if args.check_only:
        print("Native host, Compose identity, images, live data bind, TLS and health verified.")
        return 0
    require(
        (args.deploy_root / ".deployment-update.lock").is_dir(), "Acquire the deployment lock first"
    )
    destination = args.output
    require(
        destination is not None
        and destination.is_dir()
        and destination.resolve() == destination
        and destination.is_relative_to(args.deploy_root / ".deployment-backups"),
        "Output must be a new restricted backup directory under the deployment root",
    )
    require(
        destination.stat().st_uid == os.getuid() and destination.stat().st_mode & 0o077 == 0,
        "Backup directory must be owned by you with mode 700",
    )
    status_file = destination / "backup-status.json"
    archive = destination / "data.tar.partial"
    require(
        not any(
            (destination / p).exists()
            for p in ("backup-status.json", "data.tar", "data.tar.partial")
        ),
        "Backup output already exists; inspect the prior job instead of launching it again",
    )
    with (args.deploy_root / ".production-backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = {
            "pid": os.getpid(),
            "app_id": app["Id"],
            "image_id": app["Image"],
            "data_source": str(args.deploy_root / "data"),
            "started_at": time.time(),
        }

        def report(phase, **fields):
            status.update(phase=phase, updated_at=time.time(), **fields)
            temporary = status_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(status, indent=2) + "\n")
            temporary.replace(status_file)
            print(phase, flush=True)

        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, interrupted)
        try:
            report("starting")
            stopped_archive(
                app["Id"],
                args.deploy_root / "data",
                archive,
                args.archive_timeout,
                args.health_timeout,
                report,
            )
            report("verifying")
            checksum = verify_archive(archive)
            archive.rename(destination / "data.tar")
            (destination / "data.tar.sha256").write_text(checksum + "  data.tar\n")
            report("complete", exit_code=0, sha256=checksum)
            return 0
        except Exception as error:
            report("failed", exit_code=1, error=str(error))
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
