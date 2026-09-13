"""Service Portal "Update and restart" integration tests.

These tests execute the real ``scripts/update-and-restart.sh`` inside a
synthetic checkout against fake ``git`` and ``docker`` executables that log
every invocation. That proves the script's safety checks, command ordering,
and failure handling without touching real Git state, a real Docker daemon,
or any real container.

The synthetic layout mirrors the production one: the script lives at
``<root>/scripts/update-and-restart.sh`` and resolves the repository root
from its own location, exactly as Service Portal sees it when it mounts the
compose project directory and runs the labeled script.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REAL_SCRIPT = REPOSITORY_ROOT / "scripts" / "update-and-restart.sh"
WRAPPER = REPOSITORY_ROOT / "update_and_restart"
COMPOSE_EXAMPLE = REPOSITORY_ROOT / "compose.example.yml"
ENV_EXAMPLE = REPOSITORY_ROOT / ".env.example"
RUNNER_DOCKERFILE = REPOSITORY_ROOT / "deployment" / "runner" / "Dockerfile"

EXPECTED_REMOTE = "https://github.com/astigmatism/comfyui-image-frontend.git"
LOCK_DIRNAME = "service-portal-update.lock"

FAKE_GIT = r"""#!/usr/bin/env bash
# Fake git shim. Logs every invocation to $FAKE_LOG and answers from
# GIT_SHIM_* environment variables so each test selects a scenario.
{
  printf 'git'
  for arg in "$@"; do printf ' %s' "$arg"; done
  printf '\n'
} >> "$FAKE_LOG"

case "$*" in
  "rev-parse --is-inside-work-tree")
    if [ "${GIT_SHIM_NO_WORK_TREE:-0}" = "1" ]; then exit 1; fi
    echo true
    ;;
  "branch --show-current")
    # Distinguish "unset" (happy-path default) from an explicitly empty
    # value (detached HEAD scenario).
    if [ -n "${GIT_SHIM_BRANCH+x}" ]; then
      printf '%s\n' "$GIT_SHIM_BRANCH"
    else
      printf 'main\n'
    fi
    ;;
  "rev-parse --abbrev-ref --symbolic-full-name @{upstream}")
    if [ "${GIT_SHIM_NO_UPSTREAM:-0}" = "1" ]; then exit 1; fi
    printf '%s\n' "${GIT_SHIM_UPSTREAM:-origin/main}"
    ;;
  "remote get-url origin")
    if [ "${GIT_SHIM_NO_REMOTE:-0}" = "1" ]; then exit 1; fi
    printf '%s\n' "${GIT_SHIM_REMOTE_URL:-__EXPECTED_REMOTE__}"
    ;;
  "status --porcelain --untracked-files=normal")
    if [ -n "${GIT_SHIM_DIRTY:-}" ]; then printf '%s\n' "$GIT_SHIM_DIRTY"; fi
    ;;
  "fetch origin main")
    exit "${GIT_SHIM_FETCH_RC:-0}"
    ;;
  "merge-base --is-ancestor HEAD origin/main")
    exit "${GIT_SHIM_IS_ANCESTOR:-0}"
    ;;
  "merge --ff-only origin/main")
    exit "${GIT_SHIM_MERGE_RC:-0}"
    ;;
esac
exit 0
"""

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Fake docker shim. Logs every invocation to $FAKE_LOG. Per-occurrence
# failures are selected with DOCKER_SHIM_*_FAIL_CALLS (1-based occurrence
# numbers, space separated); state lives in $FAKE_STATE_DIR.
{
  printf 'docker'
  for arg in "$@"; do printf ' %s' "$arg"; done
  printf '\n'
} >> "$FAKE_LOG"

count_call() {
  local file="$FAKE_STATE_DIR/$1.count"
  local n
  n=$(cat "$file" 2>/dev/null || printf 0)
  n=$((n + 1))
  printf '%s' "$n" > "$file"
  printf '%s' "$n"
}

fail_if_listed() {
  local n="$1"
  shift
  local wanted
  for wanted in "$@"; do
    if [ "$n" = "$wanted" ]; then return 0; fi
  done
  return 1
}

case "$*" in
  "compose version")
    if [ "${DOCKER_SHIM_COMPOSE_VERSION_RC:-0}" = "1" ]; then exit 1; fi
    echo "Docker Compose version v2.40.0-fake"
    ;;
  "info")
    exit "${DOCKER_SHIM_INFO_RC:-0}"
    ;;
  "compose -f compose.example.yml config --quiet")
    n=$(count_call config)
    if fail_if_listed "$n" ${DOCKER_SHIM_CONFIG_FAIL_CALLS:-}; then exit 1; fi
    exit 0
    ;;
  "compose -f compose.example.yml build")
    n=$(count_call build)
    if fail_if_listed "$n" ${DOCKER_SHIM_BUILD_FAIL_CALLS:-}; then exit 1; fi
    exit 0
    ;;
  "compose -f compose.example.yml up"*)
    n=$(count_call up)
    if fail_if_listed "$n" ${DOCKER_SHIM_UP_FAIL_CALLS:-}; then exit 1; fi
    exit 0
    ;;
  "compose -f compose.example.yml exec -T comfyui-image-frontend"*)
    exit "${DOCKER_SHIM_EXEC_RC:-0}"
    ;;
esac
exit 0
"""


def _dead_pid() -> int:
    """Fork and reap a child so the returned PID is provably dead."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


@pytest.fixture()
def fake_checkout(tmp_path: Path) -> dict:
    """A synthetic checkout: real script, stub compose file, fake shims."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "compose.example.yml").write_text(
        "services:\n  comfyui-image-frontend:\n    image: comfyui-image-frontend:local\n",
        encoding="utf-8",
    )
    script = root / "scripts" / "update-and-restart.sh"
    script.write_text(REAL_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(0o755)
    # The updater issues the appliance-local TLS leaf via a sibling script
    # before recreating the project, so it must be present in the synthetic
    # checkout too (it shells out to the host's OpenSSL).
    cert_script = root / "scripts" / "issue-local-cert.sh"
    cert_script.write_text(
        (REPOSITORY_ROOT / "scripts" / "issue-local-cert.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    cert_script.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shims = {
        "git": FAKE_GIT.replace("__EXPECTED_REMOTE__", EXPECTED_REMOTE),
        "docker": FAKE_DOCKER,
    }
    for name, content in shims.items():
        exe = bin_dir / name
        exe.write_text(content, encoding="utf-8")
        exe.chmod(0o755)

    state_dir = tmp_path / "shim-state"
    state_dir.mkdir()

    return {
        "root": root,
        "bin": bin_dir,
        "log": tmp_path / "cmds.log",
        "state_dir": state_dir,
        "lock_dir": root / ".git" / LOCK_DIRNAME,
    }


def run_updater(
    fake_checkout: dict,
    env_extra: dict | None = None,
    bin_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    env = {
        "PATH": f"{bin_dir or fake_checkout['bin']}:{os.environ['PATH']}",
        "HOME": str(fake_checkout["root"].parent / "home"),
        "FAKE_LOG": str(fake_checkout["log"]),
        "FAKE_STATE_DIR": str(fake_checkout["state_dir"]),
    }
    env.update(env_extra or {})
    script = fake_checkout["root"] / "scripts" / "update-and-restart.sh"
    # Executes the repository's own trusted script inside an isolated temp
    # checkout with fake git/docker shims; no untrusted input is involved.
    return subprocess.run(  # noqa: S603
        [str(script)],
        cwd=fake_checkout["root"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def log_lines(fake_checkout: dict) -> list[str]:
    path = fake_checkout["log"]
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def first_index(lines: list[str], prefix: str) -> int:
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return index
    return -1


def all_indices(lines: list[str], prefix: str) -> list[int]:
    return [index for index, line in enumerate(lines) if line.startswith(prefix)]


def assert_refused(proc: subprocess.CompletedProcess, fragment: str) -> None:
    """The runbook's error-surfacing contract plus the specific cause."""
    assert proc.returncode != 0
    assert "Refusing:" in proc.stderr or "Error:" in proc.stderr
    assert fragment in proc.stderr


# --- Happy path and command ordering ----------------------------------------


def test_happy_path_orders_preparation_before_recreation(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout)

    assert proc.returncode == 0, proc.stderr
    assert "updated and healthy" in proc.stdout

    lines = log_lines(fake_checkout)
    config_indices = all_indices(lines, "docker compose -f compose.example.yml config --quiet")
    fetch = first_index(lines, "git fetch origin main")
    ancestor = first_index(lines, "git merge-base --is-ancestor HEAD origin/main")
    merge = first_index(lines, "git merge --ff-only origin/main")
    build = first_index(lines, "docker compose -f compose.example.yml build")
    up = first_index(lines, "docker compose -f compose.example.yml up")
    exec_ = first_index(
        lines, "docker compose -f compose.example.yml exec -T comfyui-image-frontend"
    )

    # Every mutation-relevant step happens, in a safe order: validate the
    # current config, fetch, prove ancestry, fast-forward merge, re-validate
    # the updated config, build the replacement, then recreate+health-check,
    # and only then run the final in-container verification.
    assert config_indices[0] < fetch, lines
    assert fetch < ancestor < merge, lines
    assert merge < config_indices[1] < build < up < exec_, lines
    assert len(config_indices) == 2

    up_line = lines[up]
    assert up_line == ("docker compose -f compose.example.yml up -d --wait --wait-timeout 120")
    # The whole project is reconciled (no service filter), and nothing is
    # stopped, torn down, or pruned.
    for line in lines:
        assert not re.search(r"\bdown\b", line), line
        assert "prune" not in line, line
        assert not line.startswith("docker compose -f compose.example.yml stop"), line
        assert "git pull" not in line, line
        assert "reset" not in line and "stash" not in line, line

    # The lock is always cleaned up.
    assert not fake_checkout["lock_dir"].exists()


def test_happy_path_respects_custom_bounded_wait(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"CIF_UPDATE_START_TIMEOUT": "90"})

    assert proc.returncode == 0, proc.stderr
    up_lines = [
        line
        for line in log_lines(fake_checkout)
        if line.startswith("docker compose") and " up " in f" {line} "
    ]
    assert up_lines == ["docker compose -f compose.example.yml up -d --wait --wait-timeout 90"]


def test_delegated_portal_environment_is_recognized(fake_checkout: dict) -> None:
    holder = _dead_pid()
    fake_checkout["lock_dir"].mkdir(parents=True)
    (fake_checkout["lock_dir"] / "pid").write_text(f"{holder}\n")
    (fake_checkout["lock_dir"] / "container").write_text("sp-previous-job\n")

    proc = run_updater(
        fake_checkout,
        {
            "SERVICE_PORTAL_UPDATE_DELEGATED": "1",
            "SERVICE_PORTAL_UPDATE_JOB_ID": "job-123",
            "DSH_UPDATE_DELEGATED": "1",
            "DSH_UPDATE_CONTAINER_NAME": "sp-this-job",
        },
    )

    # A lock recorded by a different maintenance container is stale, so the
    # delegated run proceeds; the job context is logged for correlation.
    assert proc.returncode == 0, proc.stderr
    assert "delegated maintenance mode (job: job-123, container: sp-this-job)" in proc.stdout
    assert "Removing stale update lock" in proc.stdout
    assert not fake_checkout["lock_dir"].exists()


# --- Source verification: dirty tree, branch, upstream, remote ----------------


def test_dirty_checkout_refused_before_any_mutation(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"GIT_SHIM_DIRTY": " M backend/app/main.py"})

    assert_refused(proc, "not clean")
    lines = log_lines(fake_checkout)
    # Only the pre-check validation ran: no fetch, merge, build, stop, or
    # recreate operation happened.
    assert first_index(lines, "git fetch") == -1, lines
    assert first_index(lines, "git merge") == -1, lines
    assert first_index(lines, "docker compose -f compose.example.yml build") == -1
    assert first_index(lines, "docker compose -f compose.example.yml up") == -1
    assert first_index(lines, "docker compose -f compose.example.yml stop") == -1


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        ({"GIT_SHIM_BRANCH": ""}, "Detached HEAD"),
        ({"GIT_SHIM_BRANCH": "develop"}, "does not match expected branch"),
        (
            {"GIT_SHIM_REMOTE_URL": "https://github.com/attacker/fork.git"},
            "expected",
        ),
        ({"GIT_SHIM_NO_REMOTE": "1"}, "not configured"),
        ({"GIT_SHIM_NO_UPSTREAM": "1"}, "no upstream"),
        ({"GIT_SHIM_UPSTREAM": "origin/develop"}, "expected 'origin/main'"),
    ],
)
def test_source_verification_refusals(fake_checkout: dict, env: dict, fragment: str) -> None:
    proc = run_updater(fake_checkout, env)

    assert_refused(proc, fragment)
    lines = log_lines(fake_checkout)
    assert first_index(lines, "git fetch") == -1, lines
    assert first_index(lines, "docker compose -f compose.example.yml up") == -1
    assert not fake_checkout["lock_dir"].exists()


def test_expected_remote_can_be_overridden(fake_checkout: dict) -> None:
    proc = run_updater(
        fake_checkout,
        {
            "CIF_UPDATE_EXPECTED_REMOTE": "https://github.com/attacker/fork.git",
            "GIT_SHIM_REMOTE_URL": "https://github.com/attacker/fork.git",
        },
    )
    assert proc.returncode == 0, proc.stderr


def test_expected_branch_can_be_overridden(fake_checkout: dict) -> None:
    proc = run_updater(
        fake_checkout,
        {
            "CIF_UPDATE_EXPECTED_BRANCH": "develop",
            "GIT_SHIM_BRANCH": "develop",
            "GIT_SHIM_UPSTREAM": "origin/develop",
        },
    )
    # The fetch/merge commands still target the configured branch.
    lines = log_lines(fake_checkout)
    assert "git fetch origin develop" in lines
    assert "git merge --ff-only origin/develop" in lines
    assert proc.returncode == 0, proc.stderr


# --- History verification: fast-forward only ----------------------------------


def test_non_fast_forward_history_refused(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"GIT_SHIM_IS_ANCESTOR": "1"})

    assert_refused(proc, "rewritten")
    lines = log_lines(fake_checkout)
    # Fetching is read-only and allowed; the merge, build, and recreate are not.
    assert first_index(lines, "git fetch origin main") != -1
    assert first_index(lines, "git merge --ff-only") == -1, lines
    assert first_index(lines, "docker compose -f compose.example.yml build") == -1
    assert first_index(lines, "docker compose -f compose.example.yml up") == -1


def test_merge_failure_refused_before_recreation(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"GIT_SHIM_MERGE_RC": "1"})

    assert_refused(proc, "Fast-forward-only merge")
    lines = log_lines(fake_checkout)
    assert first_index(lines, "docker compose -f compose.example.yml build") == -1
    assert first_index(lines, "docker compose -f compose.example.yml up") == -1


# --- Replacement prepared before recreation -----------------------------------


def test_invalid_updated_compose_blocks_recreation(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"DOCKER_SHIM_CONFIG_FAIL_CALLS": "2"})

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    lines = log_lines(fake_checkout)
    assert len(all_indices(lines, "docker compose -f compose.example.yml config")) == 2
    assert first_index(lines, "docker compose -f compose.example.yml build") == -1
    assert first_index(lines, "docker compose -f compose.example.yml up") == -1
    assert not fake_checkout["lock_dir"].exists()


def test_invalid_current_compose_blocks_everything(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"DOCKER_SHIM_CONFIG_FAIL_CALLS": "1"})

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    lines = log_lines(fake_checkout)
    assert first_index(lines, "git fetch") == -1, lines
    assert first_index(lines, "docker compose -f compose.example.yml up") == -1


def test_build_failure_propagates_without_recreation(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"DOCKER_SHIM_BUILD_FAIL_CALLS": "1"})

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    lines = log_lines(fake_checkout)
    assert first_index(lines, "docker compose -f compose.example.yml build") != -1
    assert first_index(lines, "docker compose -f compose.example.yml up") == -1
    assert not fake_checkout["lock_dir"].exists()


# --- Recreate with bounded wait, failure propagation, recovery ------------------


def test_up_failure_retries_recovery_once_then_fails(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"DOCKER_SHIM_UP_FAIL_CALLS": "1 2"})

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    lines = log_lines(fake_checkout)
    up_lines = [
        line for line in lines if line.startswith("docker compose -f compose.example.yml up")
    ]
    assert len(up_lines) == 2, up_lines
    assert "--no-build" in up_lines[1]
    assert not fake_checkout["lock_dir"].exists()


def test_up_failure_recovery_pass_can_succeed(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"DOCKER_SHIM_UP_FAIL_CALLS": "1"})

    assert proc.returncode == 0, proc.stderr
    lines = log_lines(fake_checkout)
    up_lines = [
        line for line in lines if line.startswith("docker compose -f compose.example.yml up")
    ]
    assert len(up_lines) == 2
    assert "--no-build" in up_lines[1]
    assert "updated and healthy" in proc.stdout


def test_final_runtime_verification_failure_fails_update(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"DOCKER_SHIM_EXEC_RC": "1"})

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    assert "explicit" in proc.stderr
    assert "updated and healthy" not in proc.stdout
    # The recreation itself happened; the final gate blocks the success exit.
    lines = log_lines(fake_checkout)
    assert first_index(lines, "docker compose -f compose.example.yml up") != -1


# --- Locking ---------------------------------------------------------------------


def test_lock_held_by_live_process_refused(fake_checkout: dict) -> None:
    fake_checkout["lock_dir"].mkdir(parents=True)
    (fake_checkout["lock_dir"] / "pid").write_text(f"{os.getpid()}\n")
    (fake_checkout["lock_dir"] / "container").write_text("local\n")

    proc = run_updater(fake_checkout)

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    assert "already running" in proc.stderr
    lines = log_lines(fake_checkout)
    assert first_index(lines, "git fetch") == -1
    # A lock we did not create is never removed.
    assert fake_checkout["lock_dir"].exists()


def test_stale_lock_from_dead_holder_taken_over(fake_checkout: dict) -> None:
    fake_checkout["lock_dir"].mkdir(parents=True)
    (fake_checkout["lock_dir"] / "pid").write_text(f"{_dead_pid()}\n")
    (fake_checkout["lock_dir"] / "container").write_text("local\n")

    proc = run_updater(fake_checkout)

    assert proc.returncode == 0, proc.stderr
    assert "Removing stale update lock" in proc.stdout
    assert not fake_checkout["lock_dir"].exists()


def test_force_unlock_overrides_live_holder(fake_checkout: dict) -> None:
    fake_checkout["lock_dir"].mkdir(parents=True)
    (fake_checkout["lock_dir"] / "pid").write_text(f"{os.getpid()}\n")
    (fake_checkout["lock_dir"] / "container").write_text("local\n")

    proc = run_updater(fake_checkout, {"CIF_UPDATE_FORCE_UNLOCK": "1"})

    assert proc.returncode == 0, proc.stderr
    assert not fake_checkout["lock_dir"].exists()


# --- Toolchain verification -------------------------------------------------------


def test_missing_docker_binary_refused(fake_checkout: dict, tmp_path: Path) -> None:
    git_only = tmp_path / "bin-git-only"
    git_only.mkdir()
    (git_only / "git").write_text(
        (fake_checkout["bin"] / "git").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (git_only / "git").chmod(0o755)

    # Restrict PATH to system directories plus the git shim so no real
    # Docker CLI can be found.
    proc = run_updater(
        fake_checkout,
        bin_dir=git_only,
        env_extra={"PATH": f"{git_only}:/usr/bin:/bin"},
    )

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    assert "docker" in proc.stderr
    assert log_lines(fake_checkout) == []


def test_missing_compose_plugin_refused(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"DOCKER_SHIM_COMPOSE_VERSION_RC": "1"})

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    _assert_no_source_or_container_work(log_lines(fake_checkout))


def test_unreachable_docker_daemon_refused(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"DOCKER_SHIM_INFO_RC": "1"})

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    _assert_no_source_or_container_work(log_lines(fake_checkout))


def _assert_no_source_or_container_work(lines: list[str]) -> None:
    """Toolchain refusals happen before any Git or container mutation."""
    for line in lines:
        assert "fetch" not in line, line
        assert not line.startswith("docker compose -f compose.example.yml up"), line
        assert not line.startswith("docker compose -f compose.example.yml build"), line
        assert not line.startswith("docker compose -f compose.example.yml stop"), line


def test_missing_compose_file_refused(fake_checkout: dict) -> None:
    (fake_checkout["root"] / "compose.example.yml").unlink()

    proc = run_updater(fake_checkout)

    assert proc.returncode != 0
    assert "Error:" in proc.stderr
    assert "Compose file not found" in proc.stderr


def test_not_a_git_work_tree_refused(fake_checkout: dict) -> None:
    proc = run_updater(fake_checkout, {"GIT_SHIM_NO_WORK_TREE": "1"})

    assert proc.returncode != 0
    assert "Error:" in proc.stderr


# --- Static contract: labels, runner image, wrapper, script shape -----------------


def _script_command_lines() -> list[str]:
    lines = REAL_SCRIPT.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if not line.lstrip().startswith("#")]


def test_portal_script_contract() -> None:
    assert REAL_SCRIPT.exists()
    assert REAL_SCRIPT.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    first_line = REAL_SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!") and "bash" in first_line

    script = REAL_SCRIPT.read_text(encoding="utf-8")
    # Fast-forward-only history handling.
    assert "git merge --ff-only" in script
    # Bounded health wait on recreate.
    assert "up -d --wait" in script and "--wait-timeout" in script
    # Noninteractive Git.
    assert "GIT_TERMINAL_PROMPT=0" in script
    # Error-surfacing terms the portal greps for.
    assert "Error:" in script and "Refusing:" in script

    # No destructive or global housekeeping Git/Docker operations on any
    # command line (user-facing error text may mention such words).
    commands = "\n".join(_script_command_lines())
    assert not re.search(r"\bgit\s+(reset|stash|clean|checkout\s+-f)\b", commands)
    assert not re.search(r"\bgit\s+pull\b", commands)
    assert not re.search(r"\bprune\b", commands)
    assert not re.search(r"compose\b.*\bdown\b", commands)


def test_wrapper_delegates_to_canonical_script() -> None:
    assert WRAPPER.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    wrapper = WRAPPER.read_text(encoding="utf-8")
    assert "scripts/update-and-restart.sh" in wrapper
    assert "exec" in wrapper


def test_compose_labels_opt_in_exactly_one_service() -> None:
    compose = COMPOSE_EXAMPLE.read_text(encoding="utf-8")

    enabled = re.findall(
        r"^\s*io\.service-portal\.update\.enabled:\s*(\S+)\s*$", compose, re.MULTILINE
    )
    assert enabled == ['"true"']

    scripts = re.findall(
        r"^\s*io\.service-portal\.update\.script:\s*(\S+)\s*$", compose, re.MULTILINE
    )
    assert scripts == ['"scripts/update-and-restart.sh"']
    # Repository-relative: no absolute path, no backslashes, no escape.
    assert not scripts[0].startswith("/")
    assert "\\" not in scripts[0]
    assert ".." not in scripts[0]

    images = re.findall(
        r"^\s*io\.service-portal\.update\.image:\s*(\S+)\s*$", compose, re.MULTILINE
    )
    assert len(images) == 1
    image_ref = images[0].strip('"')
    # Env-var templated with a valid Docker image reference default.
    template = re.fullmatch(r"\$\{PROJECT_RUNNER_IMAGE:-([^}]+)\}", image_ref)
    assert template, image_ref
    default_ref = template.group(1)
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*(?::[A-Za-z0-9_][A-Za-z0-9._-]*)?", default_ref), (
        default_ref
    )

    users = re.findall(r"^\s*io\.service-portal\.update\.user:\s*(\S+)\s*$", compose, re.MULTILINE)
    assert len(users) == 1
    user_ref = users[0].strip('"')
    assert re.fullmatch(r"\$\{HOST_UID:-\d+\}:\$\{HOST_GID:-\d+\}", user_ref), user_ref


def test_env_example_documents_portal_variables() -> None:
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    for variable in ("HOST_UID=", "HOST_GID=", "PROJECT_RUNNER_IMAGE="):
        assert variable in env_example, variable
    # The documented build command must name the runner Dockerfile context.
    assert "deployment/runner" in env_example


def test_runner_image_provides_required_toolchain() -> None:
    dockerfile = RUNNER_DOCKERFILE.read_text(encoding="utf-8")
    from_line = next(line for line in dockerfile.splitlines() if line.startswith("FROM"))
    # A Docker CLI image supplies the Docker CLI; compose plugin, bash, and
    # Git are added explicitly.
    assert re.match(r"FROM docker:[\w.-]+-cli\b", from_line), from_line
    install_line = next(line for line in dockerfile.splitlines() if "apk add" in line)
    for tool in ("bash", "git", "docker-compose"):
        assert re.search(rf"(^|\s){tool}(\s|$)", install_line), (tool, install_line)
