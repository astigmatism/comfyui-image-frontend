#!/usr/bin/env python3
# ruff: noqa: S603, S607
"""Launch one durable production update using existing Docker access; no SSH setup."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = "/home/astigmatism/comfyui-image-frontend"


def wait_for_job(container, env, check_only=False):
    """Keep the Portal runner alive until the durable deployment actually finishes."""
    try:
        subprocess.run(
            ["docker", "logs", "--follow", "--tail", "20", container],
            env=env,
            check=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Deployment monitoring timed out; inspect existing job {container}. "
            "It may still be running; do not launch another update."
        ) from error
    # Log EOF can precede Docker publishing the terminal container state.
    subprocess.run(
        ["docker", "wait", container],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    state = json.loads(
        subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", container],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    )
    if state["Running"] or state["Status"] not in ("exited", "dead"):
        raise RuntimeError(f"Deployment job {container} has not finished")
    code = state["ExitCode"]
    if code:
        print(f"Error: deployment job {container} failed with exit code {code}", flush=True)
        return code
    logs = subprocess.run(
        ["docker", "logs", "--tail", "20", container],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.splitlines()
    records = []
    for line in logs:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    result = records[-1] if records and isinstance(records[-1], dict) else {}
    verified = (result.get("phase") == "complete" and result.get("exit_code") == 0) or result.get(
        "outcome"
    ) == ("check-passed" if check_only else "already-current")
    if not verified:
        raise RuntimeError(f"Deployment job {container} exited without a verified result")
    print(json.dumps({"job": container, "exit_code": 0, "verification": "passed"}), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-view", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--wait", action="store_true", help="Stream deployment progress and return its exit code"
    )
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
            ),
            flush=True,
        )
        if args.wait:
            return wait_for_job(container, env, args.check_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
