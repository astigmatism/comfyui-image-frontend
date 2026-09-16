#!/usr/bin/env python3
# ruff: noqa: S603, S607
"""Launch one durable production update using existing Docker access; no SSH setup."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = "/home/astigmatism/comfyui-image-frontend"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-view", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise RuntimeError("Expected a full target SHA")
    view = args.source_view.parent
    if str(view) not in (ROOT, "/host" + ROOT):
        raise RuntimeError("Unexpected production checkout path")
    sock = next(
        (p for p in (Path("/var/run/docker.sock"), Path("/host/run/docker.sock")) if p.is_socket()),
        None,
    )
    if sock is None:
        raise RuntimeError(
            "Docker socket unavailable; report the access blocker. Do not configure SSH."
        )
    with tempfile.TemporaryDirectory(prefix="cif-docker-") as config:
        env = dict(os.environ, DOCKER_HOST="unix://" + str(sock), DOCKER_CONFIG=config)
        env.pop("DOCKER_CONTEXT", None)

        def run(command, **kwargs):
            return subprocess.run(command, env=env, check=True, timeout=180, **kwargs)

        daemon = run(
            ["docker", "info", "--format", "{{.Name}}"], capture_output=True, text=True
        ).stdout.strip()
        if daemon != "samus":
            raise RuntimeError("Expected the Samus production Docker daemon")
        existing = run(
            ["docker", "ps", "-q", "--filter", "label=cif.production-update=true"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if existing:
            raise RuntimeError("A deployment/check is already running: " + existing)
        paths = [
            "deployment/production-runner/Dockerfile",
            "scripts/production-update.py",
            "scripts/backup-production-bind.py",
        ]
        content = b"".join(
            run(
                ["git", "-C", str(args.source_view), "show", f"{args.sha}:{p}"], capture_output=True
            ).stdout
            for p in paths
        )
        image = "comfyui-image-frontend-deployer:" + hashlib.sha256(content).hexdigest()[:20]
        found = subprocess.run(
            ["docker", "image", "inspect", image],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if found.returncode:
            archive = run(
                ["git", "-C", str(args.source_view), "archive", args.sha, *paths],
                capture_output=True,
            ).stdout
            run(["docker", "build", "-q", "-t", image, "-f", paths[0], "-"], input=archive)
        name = f"cif-production-update-{time.time_ns()}"
        owner = view.stat()
        command = [
            "docker",
            "run",
            "-d",
            "--init",
            "--name",
            name,
            "--label",
            "cif.production-update=true",
            "--user",
            f"{owner.st_uid}:{owner.st_gid}",
            "--group-add",
            str(sock.stat().st_gid),
            "--mount",
            f"type=bind,source={ROOT},target={ROOT}",
            "--mount",
            "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
            "--env",
            "CIF_MAINTENANCE_CONTAINER=" + name,
            image,
            "--deploy-root",
            ROOT,
            "--sha",
            args.sha,
        ]
        if args.check_only:
            command.append("--check-only")
        container = run(command, capture_output=True, text=True).stdout.strip()
        print(
            json.dumps(
                {
                    "job": name,
                    "container_id": container,
                    "target_sha": args.sha,
                    "check_only": args.check_only,
                    "inspect_command": (
                        f"docker -H unix://{sock} inspect --format '{{{{json .State}}}}' {name}"
                    ),
                    "logs_command": f"docker -H unix://{sock} logs --tail 20 {name}",
                }
            )
        )


if __name__ == "__main__":
    main()
