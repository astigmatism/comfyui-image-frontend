#!/usr/bin/env python3
# ruff: noqa: S603, S607
"""Launch and wait for a pinned, durable Samus release job using the local socket."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = "/home/astigmatism/deployments/comfyui-image-frontend"
CREDENTIALS = "/home/astigmatism/credentials/comfyui-image-frontend"
REPOSITORY = "https://github.com/astigmatism/comfyui-image-frontend.git"
STATUS = {"phase": "launcher-preflight", "job": "none-launched"}


def wait_for_job(container, env, check_only=False):
    """Only the final verified result AND a successful exit constitute success."""
    try:
        subprocess.run(
            ["docker", "logs", "--follow", "--tail", "all", container],
            env=env,
            check=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Monitoring timed out; inspect retained job {container}; do not launch another update"
        ) from error
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
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except json.JSONDecodeError:
            pass
    result = records[-1] if records else {}
    if state["ExitCode"]:
        # The child's single sanitized Error line has already been streamed.
        if not any(line.startswith("Error:") for line in logs):
            print(
                f"Error: production update failed during job-execution; "
                f"recovery unknown; retained job {container}.",
                flush=True,
            )
        return state["ExitCode"]
    if not (
        result.get("phase") == "complete"
        and result.get("exit_code") == 0
        and result.get("https") == "verified"
        and (not check_only or result.get("outcome") == "check-passed")
    ):
        raise RuntimeError(f"Deployment job {container} exited without a verified result")
    return 0


def job_command(root, image, name, owner, socket_gid, args):
    command = [
        "docker",
        "run",
        "-d",
        "--init",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        "cif.production-update=true",
        "--label",
        "io.service-portal.hidden=true",
        "--label",
        "io.service-portal.maintenance=true",
        "--user",
        owner,
        "--group-add",
        str(socket_gid),
        "--mount",
        f"type=bind,source={root},target={root}",
        "--mount",
        f"type=bind,source={CREDENTIALS},target={CREDENTIALS},readonly",
        "--mount",
        "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
        "--env",
        "CIF_MAINTENANCE_CONTAINER=" + name,
        image,
        "--deploy-root",
        str(root),
    ]
    if args.check_only:
        command.append("--check-only")
    if args.restart:
        command.append("--restart")
    if args.sha:
        command.extend(["--sha", args.sha])
    if args.install:
        command.extend(["--install", args.install])
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--sha", help="Optional full reviewed ancestor of main to deploy")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--restart", action="store_true")
    mode.add_argument("--install", metavar="RUNNER_SHA")
    parser.add_argument("--wait", action="store_true", help="Compatibility flag; always waits")
    args = parser.parse_args()
    if args.sha and not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise RuntimeError("Expected a full target SHA")
    if args.install and (args.sha or not re.fullmatch(r"[0-9a-f]{40}", args.install)):
        raise RuntimeError("Installation requires only a full runner revision")
    if str(args.deploy_root) != ROOT or args.deploy_root.resolve() != args.deploy_root:
        raise RuntimeError("Unexpected production deployment path")
    owner = args.deploy_root.stat()
    if owner.st_uid == 0 or owner.st_uid != os.getuid():
        raise RuntimeError("Run as the deployment owner, not root")
    if args.install:
        revision = args.install
        image = "local/comfyui-image-frontend-release:" + revision
    else:
        config = json.loads((args.deploy_root / "release-config.json").read_text())
        revision, image = config["runner_revision"], config["runner_image"]
        if config["repository"] != REPOSITORY or config["branch"] != "main":
            raise RuntimeError("Unexpected release source")
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or image != (
        "local/comfyui-image-frontend-release:" + revision
    ):
        raise RuntimeError("Expected a pinned release runner")
    sock = Path("/var/run/docker.sock")
    if not sock.is_socket():
        raise RuntimeError("Native Docker socket unavailable")
    with tempfile.TemporaryDirectory(prefix="cif-docker-") as config_dir:
        env = dict(os.environ, DOCKER_HOST="unix://" + str(sock), DOCKER_CONFIG=config_dir)
        env.pop("DOCKER_CONTEXT", None)

        def output(command):
            return subprocess.run(
                command, env=env, check=True, capture_output=True, text=True, timeout=60
            ).stdout.strip()

        if output(["docker", "info", "--format", "{{.Name}}"]) != "samus":
            raise RuntimeError("Expected the Samus production Docker daemon")
        STATUS["phase"] = "runner-validation"
        runner = json.loads(output(["docker", "image", "inspect", image]))[0]
        if runner["Config"].get("Labels", {}).get("org.opencontainers.image.revision") != revision:
            raise RuntimeError("Runner image revision does not match its pinned tag")
        name = f"cif-production-update-{time.time_ns()}"
        STATUS.update(phase="job-launch", job=name)
        print(json.dumps({"phase": "launch", "job": name}), flush=True)
        output(
            job_command(
                args.deploy_root,
                image,
                name,
                f"{owner.st_uid}:{owner.st_gid}",
                sock.stat().st_gid,
                args,
            )
        )
        STATUS["phase"] = "job-monitor"
        return wait_for_job(name, env, args.check_only)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Do not expose exception text, arguments, Docker config or credentials.
        print(
            f"Error: production update failed during {STATUS['phase']}; recovery unknown; "
            f"retained job {STATUS['job']} ({type(error).__name__}); inspect before retrying.",
            flush=True,
        )
        sys.exit(1)
