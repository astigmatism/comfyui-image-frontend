"""Frozen smoke-image contract from the runner installed on Samus.

Source: scripts/production-update.py at c4227cd0b312477a11df779e42c0ec23efd390f3.
Keep smoke_image verbatim: testing only the latest runner missed a release blocker.
Minimal Docker helpers below support running the unchanged function locally.
"""
# ruff: noqa: S603, S607
import json
import re
import secrets
import subprocess
import tempfile
import time

RUNNER_REVISION = "c4227cd0b312477a11df779e42c0ec23efd390f3"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def inspect(container):
    result = subprocess.run(
        ["docker", "inspect", container], check=True, capture_output=True, text=True, timeout=30
    )
    return json.loads(result.stdout)[0]


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
