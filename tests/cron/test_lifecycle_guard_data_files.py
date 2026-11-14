"""The lifecycle guard scans scripts a command can run, never fails closed on the data files it reads.

A ``(`` in a Python heredoc body splits ``pd.read_parquet('cells.parquet')`` into a command-shaped
token, and ``diff <(ls) /dev/null`` leaves ``/dev/null`` in command position. Such a data-named path
is scanned like a mention: read when it is text under the cap, never a fail-closed refusal because it
is large, binary, a device or a live database. A real lifecycle command reached through a script must
still be blocked.
"""

from __future__ import annotations

import json
import os

import pytest

from cron.lifecycle_guard import scan_gateway_lifecycle
from tools.terminal_tool_guards import gateway_lifecycle_block

DATA_SUFFIXES = (
    ".parquet", ".geojson", ".gpkg", ".sqlite", ".db", ".json", ".jsonl", ".csv",
    ".png", ".jpg", ".tif", ".pdf",
)


def _big_data_file(path, size=50 * 1024 * 1024):
    with open(path, "wb") as handle:
        handle.write(b"SQLite format 3\x00")
        handle.truncate(size)
    os.chmod(path, 0o644)
    return path


def _script(path, body, mode=0o755):
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    os.chmod(path, mode)
    return path


def _python_body_reading(path):
    # `env -u` keeps the body in the executed view (it is not an inert heredoc consumer), so only
    # the data rule can pass it.
    return (
        f"env -u PYTHONPATH python3 - <<'E'\n"
        f"rows = len(load('{path}'))\n"
        "print(rows)\n"
        "E"
    )


def test_python_body_reading_a_50mb_sqlite_file_passes(tmp_path):
    _big_data_file(tmp_path / "big.sqlite")
    command = (
        f"cd {tmp_path}; env -u PYTHONPATH python3 - <<'E'\n"
        "import sqlite3\n"
        "c = sqlite3.connect('./big.sqlite')\n"
        "print(c.execute('select 1').fetchone())\n"
        "E"
    )
    assert scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (False, None)


@pytest.mark.parametrize("suffix", DATA_SUFFIXES)
def test_large_binary_data_file_of_each_kind_passes(tmp_path, suffix):
    data = _big_data_file(tmp_path / f"cells{suffix}", size=3 * 1024 * 1024)
    assert scan_gateway_lifecycle(_python_body_reading(data), cwd=str(tmp_path)) == (False, None)


def test_data_file_and_dev_null_in_command_position_pass(tmp_path):
    data = _big_data_file(tmp_path / "parcels.geojson")
    assert scan_gateway_lifecycle(f"diff <(ls) /dev/null; x=( {data} )", cwd=str(tmp_path)) == (False, None)
    assert scan_gateway_lifecycle(f"sort a.csv &>/dev/null; wc -l <(cat {data})", cwd=str(tmp_path)) == (
        False, None,
    )


@pytest.mark.parametrize("verb", ["restart", "stop"])
def test_gateway_lifecycle_reached_through_a_script_is_still_blocked(tmp_path, verb):
    script = _script(tmp_path / "bounce.sh", f"hermes gateway {verb}")
    assert scan_gateway_lifecycle(str(script), cwd=str(tmp_path)) == (True, None)
    assert scan_gateway_lifecycle(f"cd {tmp_path} && ./bounce.sh", cwd=str(tmp_path)) == (True, None)
    # The data rule keys on the name and the run-by-name position only: a shell still runs a
    # non-executable file, and `bash F` keeps the full scan.
    os.chmod(script, 0o644)
    assert scan_gateway_lifecycle(f"bash {script}", cwd=str(tmp_path)) == (True, None)


def test_gateway_stop_two_scripts_deep_with_data_arguments_is_still_blocked(tmp_path):
    data = _big_data_file(tmp_path / "cells.parquet")
    inner = _script(tmp_path / "inner.sh", "launchctl kickstart -k gui/501/ai.hermes.gateway")
    outer = _script(tmp_path / "outer.sh", f"python3 load.py {data} > /dev/null\n{inner}")
    assert scan_gateway_lifecycle(f"{outer} 2>/dev/null", cwd=str(tmp_path)) == (True, None)


def test_small_data_named_script_is_still_scanned(tmp_path):
    # A text file under the cap is read even when it is named like data: renaming restart.sh to
    # restart.json does not hide the command.
    script = _script(tmp_path / "restart.json", "hermes gateway restart")
    assert scan_gateway_lifecycle(f"{script}", cwd=str(tmp_path)) == (True, None)
    assert scan_gateway_lifecycle(f"bash {script}", cwd=str(tmp_path)) == (True, None)


def test_oversized_data_named_file_fails_closed_when_a_shell_runs_it(tmp_path):
    big = _big_data_file(tmp_path / "huge.json")
    unsafe, refusal = scan_gateway_lifecycle(f"bash {big}", cwd=str(tmp_path))
    assert unsafe is True
    assert refusal and "scan cap" in refusal
    # A script mentions it as data first, then a shell runs it: the mention must not mark it seen.
    reader = _script(tmp_path / "reader.sh", f"x=( {big} )")
    unsafe, refusal = scan_gateway_lifecycle(f"{reader}; bash {big}", cwd=str(tmp_path))
    assert unsafe is True and refusal and "scan cap" in refusal


def test_leading_redirection_does_not_hide_the_executed_script(tmp_path):
    script = _script(tmp_path / "restart.sh", "hermes gateway restart")
    assert scan_gateway_lifecycle(f">/dev/null 2>/dev/null {script}", cwd=str(tmp_path)) == (True, None)
    assert scan_gateway_lifecycle(f"> out.log {script}", cwd=str(tmp_path)) == (True, None)


def test_tilde_interpreter_body_is_code_not_shell(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\0" * (2 * 1024 * 1024))
    command = f"~/venv/bin/python - <<'E'\nn = len(open(\n{blob}\n).read())\nE"
    assert scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (False, None)


def test_script_made_executable_in_the_same_command_is_still_scanned(tmp_path):
    script = _script(tmp_path / "restart.sh", "hermes gateway restart", mode=0o644)
    assert scan_gateway_lifecycle(f"chmod +x {script} && {script}", cwd=str(tmp_path)) == (True, None)


def test_oversized_executable_still_fails_closed(tmp_path):
    big = _big_data_file(tmp_path / "tool")
    os.chmod(big, 0o755)
    unsafe, refusal = scan_gateway_lifecycle(str(big), cwd=str(tmp_path))
    assert unsafe is True
    assert refusal and "scan cap" in refusal


class _RecordingEnv:
    def __init__(self):
        self.calls = []

    def execute(self, command, **_kwargs):
        self.calls.append(command)
        return {"returncode": 1, "output": ""}


def test_local_backend_never_spends_remote_reads_on_missing_paths(tmp_path, monkeypatch):
    from tools import process_registry

    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: True)
    body = "\n".join(f"v{i} = (a{i}/b{i})" for i in range(80))
    command = f"python3 - <<'E'\n{body}\nE"

    local = _RecordingEnv()
    assert gateway_lifecycle_block(
        command=command, env=local, env_type="local", cwd=str(tmp_path), workdir=None,
        session_key="data-files-test",
    ) is None
    assert local.calls == []

    remote = _RecordingEnv()
    blocked = gateway_lifecycle_block(
        command=command, env=remote, env_type="ssh", cwd=str(tmp_path), workdir=None,
        session_key="data-files-test",
    )
    assert remote.calls, "a remote backend still reads paths the host does not have"
    assert blocked is None or "scan budget" in json.loads(blocked)["error"]


def test_gateway_tool_path_blocks_a_script_restart_and_passes_data_reads(tmp_path, monkeypatch):
    from tools import process_registry

    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: True)
    script = _script(tmp_path / "bounce.sh", "hermes gateway restart")
    blocked = gateway_lifecycle_block(
        command=f"{script} >/dev/null 2>&1", env=_RecordingEnv(), env_type="local", cwd=str(tmp_path),
        workdir=None, session_key="data-files-test",
    )
    assert blocked and "cannot restart, stop, or uninstall the gateway" in json.loads(blocked)["error"]
    data = _big_data_file(tmp_path / "cells.parquet")
    assert gateway_lifecycle_block(
        command=_python_body_reading(data) + "\nls >/dev/null 2>&1", env=_RecordingEnv(), env_type="local",
        cwd=str(tmp_path), workdir=None, session_key="data-files-test",
    ) is None
