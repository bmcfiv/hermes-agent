"""Desktop-provided project workspaces remain durable session metadata (#108205).

A desktop client names a workspace its gateway host cannot ``isdir``-probe (Docker/remote
backend topology). The failed probe is a topology artifact, not a verdict on the path, so a
nonblank desktop-sourced cwd with the client's own provenance (``cwd_explicit``, #52589)
persists to the session row; an inherited workspace only persists when the cwd resolution
actually adopted it, and a launch-dir fallback still persists nothing.
"""

import os

from hermes_state import SessionDB
from tui_gateway import server


def _gateway_with_db(monkeypatch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda _sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    return db


def _create_desktop_session(params):
    response = server.handle_request(
        {"id": "create", "method": "session.create",
         "params": {"source": "desktop", **params}}
    )
    assert "result" in response, response
    return response["result"]


def test_desktop_explicit_cwd_persists_when_local_probe_cannot_see_it(monkeypatch, tmp_path):
    db = _gateway_with_db(monkeypatch, tmp_path)
    workspace = tmp_path / "desktop-only-workspace"
    assert not workspace.exists()

    sid = None
    try:
        result = _create_desktop_session({"cwd": str(workspace), "cwd_explicit": True})
        sid, stored_id = result["session_id"], result["stored_session_id"]

        session = server._sessions[sid]
        assert session["cwd"] == str(workspace)
        assert session["explicit_cwd"] is True

        assert server._persist_session_row_for_submit("rid", session) is None
        assert db.get_session(stored_id)["cwd"] == str(workspace)
    finally:
        if sid:
            server._sessions.pop(sid, None)
        db.close()


def test_desktop_inherited_cwd_persists_only_when_the_resolution_adopted_it(
    monkeypatch, tmp_path
):
    """An inherited (cwd_explicit=false) workspace under a container backend: the cwd
    resolution adopts the client path (no host isdir on a container path), so it is the
    session's workspace and persists — while still yielding to a named profile's
    configured terminal.cwd inside _completion_cwd (#52589)."""
    db = _gateway_with_db(monkeypatch, tmp_path)
    container_cwd = "/workspace/repo"

    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(server, "_launch_configured_cwd", lambda: None)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)

    sid = None
    try:
        result = _create_desktop_session({"cwd": container_cwd})
        sid, stored_id = result["session_id"], result["stored_session_id"]

        session = server._sessions[sid]
        assert session["cwd"] == container_cwd
        assert session["explicit_cwd"] is True

        assert server._persist_session_row_for_submit("rid", session) is None
        assert db.get_session(stored_id)["cwd"] == container_cwd
    finally:
        if sid:
            server._sessions.pop(sid, None)
        db.close()


def test_desktop_launch_fallback_cwd_still_persists_nothing(monkeypatch, tmp_path):
    """The inherited cwd resolves to the gateway's launch directory (os.getcwd) — an
    artifact of how the app started, not a picked workspace: the row stays NULL and the
    sidebar keeps filing it under "No workspace" (the #98924 rule)."""
    db = _gateway_with_db(monkeypatch, tmp_path)
    launch_dir = tmp_path / "gateway-launch-dir"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    assert os.path.realpath(server._completion_cwd({})) == os.path.realpath(launch_dir)

    sid = None
    try:
        result = _create_desktop_session({"cwd": "relative-inherited-notebook"})
        sid, stored_id = result["session_id"], result["stored_session_id"]

        session = server._sessions[sid]
        assert session["explicit_cwd"] is False

        assert server._persist_session_row_for_submit("rid", session) is None
        assert db.get_session(stored_id)["cwd"] is None
    finally:
        if sid:
            server._sessions.pop(sid, None)
        db.close()


# --- opt-in desktop.single_project_home -------------------------------------------------------


def _profile_home_with_projects(tmp_path, *, flag, folders):
    """A profile home whose config sets ``desktop.single_project_home`` (None = unset) and whose
    projects.db holds one project per folder."""
    from hermes_cli import projects_db as pdb

    home = tmp_path / "profile-home"
    home.mkdir()
    if flag is not None:
        (home / "config.yaml").write_text(f"desktop:\n  single_project_home: {str(flag).lower()}\n")
    with pdb.connect_closing(home / "projects.db") as conn:
        for i, folder in enumerate(folders):
            folder.mkdir(parents=True, exist_ok=True)
            pdb.create_project(conn, name=f"Project {i}", folders=[str(folder)])
    return home


def _bare_desktop_create(monkeypatch, tmp_path, home, params=None):
    """Create a session on the launch profile served from ``home``; return (cwd, explicit, row cwd)."""
    db = _gateway_with_db(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "_hermes_home", home)
    launch_dir = tmp_path / "gateway-launch-dir"
    launch_dir.mkdir(exist_ok=True)
    monkeypatch.chdir(launch_dir)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.setattr(server, "_launch_configured_cwd", lambda: None)
    sid = None
    try:
        response = server.handle_request(
            {"id": "create", "method": "session.create", "params": {"source": "desktop", **(params or {})}})
        assert "result" in response, response
        sid, stored_id = response["result"]["session_id"], response["result"]["stored_session_id"]
        session = server._sessions[sid]
        assert server._persist_session_row_for_submit("rid", session) is None
        return session["cwd"], session["explicit_cwd"], db.get_session(stored_id)["cwd"]
    finally:
        if sid:
            server._sessions.pop(sid, None)
        db.close()


def test_single_project_home_off_keeps_the_bare_desktop_chat_in_home(monkeypatch, tmp_path):
    for flag in (None, False):
        sub = tmp_path / f"flag-{flag}"
        sub.mkdir()
        home = _profile_home_with_projects(sub, flag=flag, folders=[sub / "proj"])
        _cwd, explicit, row_cwd = _bare_desktop_create(monkeypatch, sub, home)
        assert explicit is False
        assert row_cwd is None


def test_single_project_home_starts_a_bare_desktop_chat_in_the_only_project(monkeypatch, tmp_path):
    project = tmp_path / "proj"
    home = _profile_home_with_projects(tmp_path, flag=True, folders=[project])

    cwd, explicit, row_cwd = _bare_desktop_create(monkeypatch, tmp_path, home)

    assert explicit is True
    assert cwd == row_cwd == str(project)


def test_single_project_home_with_two_projects_is_stock(monkeypatch, tmp_path):
    home = _profile_home_with_projects(tmp_path, flag=True, folders=[tmp_path / "a", tmp_path / "b"])

    _cwd, explicit, row_cwd = _bare_desktop_create(monkeypatch, tmp_path, home)

    assert explicit is False
    assert row_cwd is None


def test_single_project_home_never_moves_a_deliberate_pick(monkeypatch, tmp_path):
    home = _profile_home_with_projects(tmp_path, flag=True, folders=[tmp_path / "proj"])
    picked = tmp_path / "picked"
    picked.mkdir()

    cwd, explicit, row_cwd = _bare_desktop_create(
        monkeypatch, tmp_path, home, {"cwd": str(picked), "cwd_explicit": True})

    assert explicit is True
    assert cwd == row_cwd == str(picked)


def test_single_project_home_only_applies_to_desktop_chats(monkeypatch, tmp_path):
    """The resolver itself: cli / api_server sources and branches keep the stock resolution."""
    home = _profile_home_with_projects(tmp_path, flag=True, folders=[tmp_path / "proj"])
    monkeypatch.setattr(server, "_hermes_home", home)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.setattr(server, "_launch_configured_cwd", lambda: None)

    assert server._resolve_create_cwd({}, "desktop", None)[:2] == (True, str(tmp_path / "proj"))
    for source in ("cli", "api_server", "tui"):
        assert server._resolve_create_cwd({}, source, None)[0] is False
    assert server._resolve_create_cwd({"parent_session_id": "s1"}, "desktop", None)[0] is False
