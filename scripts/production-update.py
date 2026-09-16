#!/usr/bin/env python3
# ruff: noqa: S603, S607
"""The fixed two-file production deployment. Launched as a durable Docker job."""

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "backup", Path(__file__).with_name("backup-production-bind.py")
)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
APP, EDGE = backup.APP, backup.EDGE
PROJECT = "comfyui-image-frontend"
require, output, inspect = backup.require, backup.output, backup.inspect


def compose_command(root):
    return [
        "docker",
        "compose",
        "--project-directory",
        str(root),
        "--env-file",
        str(root / ".env"),
        "-p",
        PROJECT,
        "-f",
        str(root / "compose.yaml"),
        "-f",
        str(root / "compose.ordered-lora.yaml"),
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


def candidate_files(root, sha):
    patterns = {
        ".env": (
            rb"(?m)^([ \t]*CIF_IMAGE_TAG[ \t]*=[ \t]*)"
            rb"(?:[0-9a-f]{7,40}|\"[0-9a-f]{7,40}\"|'[0-9a-f]{7,40}')"
            rb"([ \t]*(?:#[^\r\n]*)?\r?)$",
            sha.encode(),
        ),
        "compose.ordered-lora.yaml": (
            rb"(?m)^([ \t]+context:[ \t]*)\./ordered-lora-[0-9a-f]{7,40}([ \t]*(?:#[^\r\n]*)?\r?)$",
            f"./ordered-lora-{sha}".encode(),
        ),
    }
    prepared = {}
    for name, (pattern, value) in patterns.items():
        path = root / name
        require(path.is_file() and not path.is_symlink(), "Expected regular deployment files")
        changed, count = re.subn(
            pattern, lambda m, value=value: m[1] + value + m[2], path.read_bytes()
        )
        require(count == 1, "Unexpected deployment file layout; no automatic edits allowed")
        prepared[name] = changed
    return prepared


def verify_candidate(before, after, worktree, sha):
    expected = copy.deepcopy(before)
    app = expected["services"][APP]
    app["image"] = app["image"].rsplit(":", 1)[0] + ":" + sha
    app["build"]["context"] = str(worktree)
    if "CIF_IMAGE_TAG" in app.get("environment", {}):
        app["environment"]["CIF_IMAGE_TAG"] = sha
    require(after == expected, "Unexpected Compose change; refusing to recreate services")
    return app["image"]


def mounts_equal(a, b):
    return sorted(a, key=lambda m: m["Destination"]) == sorted(b, key=lambda m: m["Destination"])


def verify_service(compose, config, root, image_id, edge_id):
    app_id = output(*compose, "ps", "-q", APP).strip()
    require(bool(app_id), "App container missing")
    require(inspect(app_id)["Image"] == image_id, "Running app image differs from selected image")
    require(output(*compose, "ps", "-q", EDGE).strip() == edge_id, "Edge identity changed")
    code = (
        "import json,urllib.request; from app.config import get_settings; "
        "h=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=5)); "
        "h['explicit_runtime']=get_settings().comfyui_instance_configuration_mode=='explicit'; "
        "print(json.dumps(h))"
    )
    deadline = time.monotonic() + 120
    while True:
        h = json.loads(output("docker", "exec", app_id, "python", "-c", code))
        worker = h.get("worker", {})
        if (
            h.get("status") == "ok"
            and h.get("database")
            and h.get("explicit_runtime")
            and worker.get("state") == "running"
            and worker.get("ready")
            and worker.get("dispatcher_running")
            and worker.get("heartbeat_fresh")
        ):
            break
        require(time.monotonic() < deadline, "Worker did not finish recovering within 120 seconds")
        time.sleep(2)
    for container in (app_id, edge_id):
        require(
            inspect(container)["State"].get("Health", {}).get("Status") == "healthy",
            "Container unhealthy",
        )
    edge = config["services"][EDGE]
    host = edge["environment"]["CIF_TLS_HOSTNAME"]
    port = next(p for p in edge["ports"] if p["target"] == 8443)
    origin = f"https://{host}:{port['published']}"
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
        str(root / "data/certificates/ca.crt"),
        "--resolve",
        f"{host}:{port['published']}:{port['host_ip']}",
    ]
    health = json.loads(output(*curl, origin + "/api/health"))
    require(
        health["status"] == "ok" and health["database"] and health["worker"]["state"] == "running",
        "HTTPS health check failed",
    )
    page = output(*curl, origin + "/")
    manifest = json.loads(output(*curl, origin + "/build.json"))
    image_manifest = json.loads(
        output("docker", "exec", app_id, "cat", "/app/frontend/dist/build.json")
    )
    require(manifest == image_manifest, "Served manifest differs from running image")
    for key in ("app", "styles", "lora_stack"):
        path = manifest["assets"][key]
        require(path.startswith("/assets/") and not path.startswith("//"), "Unexpected asset path")
        if key in ("app", "styles"):
            require(path in page, "HTML asset does not match manifest")
        output(*curl, "--output", "/dev/null", origin + path)
    return {
        "app_id": app_id,
        "asset_version": manifest["asset_version"],
        "worker": "running",
        "https": "verified",
    }


def deploy(root, sha, check_only=False):
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Expected a full target SHA")
    os.umask(0o077)
    os.environ.update(GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
    source = root / "source"
    compose = compose_command(root)
    lock = root / ".deployment-update.lock"
    require(not lock.exists(), "Deployment lock exists; report it rather than force-unlocking")
    require(not (source / ".git/service-portal-update.lock").exists(), "Portal update lock exists")
    app = backup.preflight(root, PROJECT, os.environ.get("CIF_MAINTENANCE_CONTAINER"))
    before = json.loads(output(*compose, "config", "--format", "json"))
    edge_id = output(*compose, "ps", "-q", EDGE).strip()
    edge = inspect(edge_id)
    require(
        output("git", "-C", str(source), "remote", "get-url", "origin").strip()
        == "https://github.com/astigmatism/comfyui-image-frontend.git",
        "Unexpected Git remote",
    )
    require(
        output("git", "-C", str(source), "branch", "--show-current").strip() == "main"
        and not output("git", "-C", str(source), "status", "--porcelain"),
        "Frozen checkout must be clean main",
    )
    frozen = output("git", "-C", str(source), "rev-parse", "HEAD").strip()
    deployed = app["Config"]["Image"].rsplit(":", 1)[1]
    require(re.fullmatch(r"[0-9a-f]{40}", deployed), "Running image must use its full release SHA")
    output("git", "-C", str(source), "merge-base", "--is-ancestor", deployed, sha)
    output("git", "-C", str(source), "merge-base", "--is-ancestor", sha, "origin/main")
    old_worktree = before["services"][APP]["build"]["context"]
    require(
        output("git", "-C", old_worktree, "rev-parse", "HEAD").strip() == deployed
        and not output("git", "-C", old_worktree, "status", "--porcelain"),
        "Current release context mismatch",
    )
    for live in (app, edge):
        require(
            json.loads(output("docker", "image", "inspect", live["Config"]["Image"]))[0]["Id"]
            == live["Image"],
            "An existing image tag was overwritten",
        )
    cert_dir = root / "data/certificates"
    fingerprints = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in cert_dir.iterdir()
        if p.is_file()
    }
    hostname = before["services"][EDGE]["environment"]["CIF_TLS_HOSTNAME"]
    output(
        "openssl",
        "verify",
        "-CAfile",
        str(cert_dir / "ca.crt"),
        "-verify_hostname",
        hostname,
        str(cert_dir / "tls.crt"),
    )
    output("openssl", "x509", "-in", str(cert_dir / "tls.crt"), "-noout", "-checkend", "86400")
    if check_only or sha == deployed:
        verified = verify_service(compose, before, root, app["Image"], edge_id)
        print(
            json.dumps(
                {
                    "outcome": "check-passed" if check_only else "already-current",
                    "target_sha": sha,
                    "deployed_sha": deployed,
                    **verified,
                }
            ),
            flush=True,
        )
        return
    candidate = candidate_files(root, sha)
    size = int(output("du", "-sk", str(root / "data")).split()[0]) * 1024
    require(
        shutil.disk_usage(root).free > size + 2 * 1024**3, "Insufficient backup/build disk space"
    )
    lock.mkdir()
    directory = (
        root
        / ".deployment-backups"
        / ("update-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + sha[:12])
    )
    saved = {}
    cutover = False
    log = None
    try:
        (lock / "owner.json").write_text(
            json.dumps({"job": os.environ.get("CIF_MAINTENANCE_CONTAINER"), "pid": os.getpid()})
        )
        directory.mkdir(mode=0o700, parents=True)
        log = (directory / "deployment.log").open("w")
        status = {
            "target_sha": sha,
            "previous_sha": deployed,
            "frozen_main": frozen,
            "started_at": time.time(),
        }

        def report(phase, **fields):
            status.update(phase=phase, updated_at=time.time(), **fields)
            atomic_write(
                directory / "deployment-status.json", (json.dumps(status, indent=2) + "\n").encode()
            )
            print(json.dumps({"phase": phase, "time": status["updated_at"], **fields}), flush=True)

        def run(command, timeout=180):
            subprocess.run(
                command, check=True, timeout=timeout, stdout=log, stderr=subprocess.STDOUT
            )

        report("preparing", backup=str(directory))
        for name in (".env", "compose.yaml", "compose.ordered-lora.yaml"):
            saved[name] = (root / name).read_bytes()
            (directory / name).write_bytes(saved[name])
        (directory / "compose.before.json").write_text(json.dumps(before))
        (directory / "containers.before.json").write_text(json.dumps([app, edge]))
        worktree = root / ("ordered-lora-" + sha)
        if not worktree.exists():
            run(["git", "-C", str(source), "worktree", "add", "--detach", str(worktree), sha])
        require(
            output("git", "-C", str(worktree), "rev-parse", "HEAD").strip() == sha
            and not output("git", "-C", str(worktree), "branch", "--show-current").strip()
            and not output("git", "-C", str(worktree), "status", "--porcelain"),
            "Target worktree must be clean and detached",
        )
        for name, content in candidate.items():
            atomic_write(root / name, content)
        after = json.loads(output(*compose, "config", "--format", "json"))
        target_image = verify_candidate(before, after, worktree, sha)
        (directory / "compose.candidate.json").write_text(json.dumps(after))
        records = root / ".deployment-records"
        records.mkdir(exist_ok=True)
        receipt = records / (sha + ".json")
        existing = subprocess.run(
            ["docker", "image", "inspect", target_image], capture_output=True, timeout=30
        )
        report("building")
        if existing.returncode == 0:
            require(
                receipt.is_file(),
                "Target image already exists without a deployment receipt; refusing overwrite",
            )
            require(
                json.loads(receipt.read_text())
                == {
                    "sha": sha,
                    "context": str(worktree),
                    "image_id": json.loads(existing.stdout)[0]["Id"],
                },
                "Target image provenance mismatch",
            )
        else:
            run([*compose, "build", APP], timeout=600)
        image_id = json.loads(output("docker", "image", "inspect", target_image))[0]["Id"]
        atomic_write(
            receipt,
            json.dumps({"sha": sha, "context": str(worktree), "image_id": image_id}).encode(),
        )
        current = inspect(app["Id"])
        require(
            current["State"]["Running"]
            and current["Image"] == app["Image"]
            and mounts_equal(current["Mounts"], app["Mounts"]),
            "Original app changed during build",
        )
        with (root / ".production-backup.lock").open("a") as backup_lock:
            fcntl.flock(backup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            archive = directory / "data.tar.partial"
            backup.stopped_archive(
                app["Id"], root / "data", archive, 180, 120, lambda phase: report("backup-" + phase)
            )
            report("backup-verifying")
            checksum = backup.verify_archive(archive)
            archive.rename(directory / "data.tar")
            (directory / "data.tar.sha256").write_text(checksum + "  data.tar\n")
        require(
            output("git", "-C", str(source), "rev-parse", "HEAD").strip() == frozen,
            "Frozen main changed",
        )
        require(
            json.loads(output(*compose, "config", "--format", "json")) == after,
            "Configuration changed during build/backup",
        )
        require(
            inspect(edge_id)["Image"] == edge["Image"]
            and json.loads(output("docker", "image", "inspect", edge["Config"]["Image"]))[0]["Id"]
            == edge["Image"],
            "Edge image changed",
        )
        report("reconciling")
        cutover = True
        run(
            [
                *compose,
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                "120",
            ]
        )
        report("verifying")
        verified = verify_service(compose, after, root, image_id, edge_id)
        backup.preflight(root, PROJECT, os.environ.get("CIF_MAINTENANCE_CONTAINER"))
        require(
            output("git", "-C", str(source), "rev-parse", "HEAD").strip() == frozen,
            "Frozen main changed during verification",
        )
        require(
            fingerprints
            == {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in cert_dir.iterdir()
                if p.is_file()
            },
            "TLS material changed",
        )
        report("complete", exit_code=0, image_id=image_id, **verified)
    except Exception as error:
        if not cutover:
            for name, contents in saved.items():
                atomic_write(root / name, contents)
        if directory.exists():
            atomic_write(
                directory / "failure.json",
                json.dumps(
                    {
                        "error": str(error),
                        "cutover_attempted": cutover,
                        "configuration_restored": not cutover,
                        "data_restored": False,
                    }
                ).encode(),
            )
        raise
    finally:
        if log:
            log.close()
        (lock / "owner.json").unlink(missing_ok=True)
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, backup.interrupted)
    deploy(args.deploy_root, args.sha, args.check_only)


if __name__ == "__main__":
    main()
