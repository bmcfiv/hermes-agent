"""A foreground call with ONE top-level `[nohup] job &` (or `job & disown`) after setup lines and
before report-only lines runs as background=true instead of being refused.

Every other `&` / nohup / disown / setsid shape keeps today's refusal, the converted program is the
same shell program minus the detachment, and its approval identity never changes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

from tools.approval_detection import detect_dangerous_command
from tools.terminal_tool_guards import auto_background_command, wrapped_background_command


@pytest.mark.parametrize(("command", "expected"), [
    ("nohup python3 serve.py > out.log 2>&1 &", "python3 serve.py > out.log 2>&1"),
    ("cd /tmp && nohup python3 job.py > j.log 2>&1 &", "cd /tmp && python3 job.py > j.log 2>&1"),
    ("python3 job.py > j.log 2>&1 & disown", "python3 job.py > j.log 2>&1"),
    ("cd /tmp\nexport A=1\nnohup ./run.sh > run.log 2>&1 &\necho started $!\nsleep 2",
     "cd /tmp\nexport A=1\n./run.sh > run.log 2>&1\necho started $!\nsleep 2"),
    ("mkdir -p logs\npython3 build.py > logs/b.log 2>&1 &", "mkdir -p logs\npython3 build.py > logs/b.log 2>&1"),
])
def test_one_wrapped_job_is_converted(command, expected):
    assert wrapped_background_command(command) == expected
    assert auto_background_command(command) == expected


@pytest.mark.parametrize("command", [
    # Two jobs: one tracked process cannot hold both.
    "nohup a.sh > a.log &\nnohup b.sh > b.log &",
    "a.sh & b.sh &",
    # A readiness check after the job expects it to be running beside the check.
    "nohup python3 serve.py > s.log 2>&1 &\nsleep 2\ncurl -s localhost:8000",
    "nohup python3 serve.py > s.log 2>&1 &\ntail -5 s.log",
    "nohup x.sh &\necho ok; curl -s localhost",
    # Loops, conditionals and groups may run the job zero or many times, or in a subshell.
    "for f in a b; do nohup ./p.sh $f &\ndone",
    "if true; then nohup ./p.sh &\nfi",
    "(nohup ./p.sh > p.log &)",
    # setsid starts a new session; no safe rewrite.
    "setsid ./p.sh > p.log 2>&1 &",
    # Line continuation and executable substitution.
    "nohup ./p.sh \\\n  --flag > p.log &",
    'pid=$(nohup ./p.sh > p.log 2>&1 & echo $!)',
    # A heredoc body the shell reads.
    "bash <<E\nnohup ./p.sh &\nE",
    # The words are data inside quotes.
    "echo 'nohup ./p.sh &'",
    # nohup without a background operator is not a request to background.
    "nohup ./p.sh > p.log 2>&1",
])
def test_ambiguous_shapes_are_not_converted(command):
    assert wrapped_background_command(command) is None


@pytest.mark.parametrize("command", [
    "nohup python3 serve.py > out.log 2>&1 &",
    "cd /tmp\nnohup ./run.sh > run.log 2>&1 &\necho started",
    "rm -rf build\nnohup make > m.log 2>&1 &",
])
def test_conversion_keeps_the_approval_identity(command):
    converted = wrapped_background_command(command)
    if converted is not None:
        assert detect_dangerous_command(converted) == detect_dangerous_command(command)


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_converted_program_prints_what_the_original_prints(tmp_path):
    command = f"cd {tmp_path}\nnohup printf 'job-ran\\n' > j.log 2>&1 &\necho started"
    converted = wrapped_background_command(command)
    assert converted is not None

    def run(script):
        out = subprocess.run(["bash", "-c", script + "\nwait\ncat j.log"], check=False, capture_output=True,
                             timeout=10, text=True)
        return sorted(out.stdout.split())

    assert run(converted) == run(command)


def _env_config(tmp_path):  # same shape as test_terminal_trailing_amp_auto_background
    return {
        "env_type": "local", "timeout": 180, "cwd": str(tmp_path), "host_cwd": None, "modal_mode": "auto",
        "docker_image": "", "modal_image": "", "daytona_image": "",
    }


def test_terminal_tool_runs_a_wrapped_job_in_the_background(tmp_path, monkeypatch):
    """The call reaches the tracked-background spawner with the unwrapped program, and the
    approval guard sees that same program (the spawner is stubbed: this proves the routing)."""
    from tools.terminal_tool import terminal_tool

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    seen, spawned = [], []

    def guard(command, env_type, **_kw):
        seen.append(command)
        return {"approved": True}

    def spawn(**kwargs):
        spawned.append(kwargs)
        return json.dumps({"output": "", "exit_code": 0, "error": None, "session_id": "proc_stub"})

    command = f"cd {tmp_path}\nnohup sleep 0.2 > s.log 2>&1 &\necho started"
    expected = f"cd {tmp_path}\nsleep 0.2 > s.log 2>&1\necho started"
    with patch("tools.terminal_tool._get_env_config", return_value=_env_config(tmp_path)), \
         patch("tools.terminal_tool._start_cleanup_thread"), \
         patch("tools.terminal_tool._check_all_guards", side_effect=guard), \
         patch("tools.terminal_tool.spawn_background_process", side_effect=spawn):
        result = json.loads(terminal_tool(command=command))
    assert result.get("error") is None, result
    assert [s["command"] for s in spawned] == [expected]
    assert spawned[0]["auto_backgrounded"] is True
    assert seen == [expected]


@pytest.mark.parametrize(("command", "expected"), [
    ("nohup a.sh > a.log &\nnohup b.sh > b.log &", "background"),
    ("nohup python3 serve.py > s.log 2>&1 &\ncurl -s localhost:8000", "background"),
    ("setsid ./p.sh > p.log 2>&1 &", "background"),
])
def test_terminal_tool_still_refuses_ambiguous_shapes(tmp_path, command, expected):
    from tools.terminal_tool import terminal_tool

    with patch("tools.terminal_tool._get_env_config", return_value=_env_config(tmp_path)), \
         patch("tools.terminal_tool._start_cleanup_thread"), \
         patch("tools.terminal_tool._acquire_env", side_effect=AssertionError("must not run")):
        result = json.loads(terminal_tool(command=command))
    assert result.get("error") and expected in result["error"].lower()
