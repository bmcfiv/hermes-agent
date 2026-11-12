"""Opt-in ``desktop.single_project_home``: a profile's only project doubles as its home.

Stock behaviour: every session no project claims lands in the synthetic Home bucket
(``project_tree.NO_PROJECT_ID``) and a bare Desktop new chat persists no cwd. A profile that keeps
exactly ONE active project therefore shows two buckets for one body of work. With the flag on and
exactly one active project:

- the project tree files would-be Home sessions under that project (``build_tree(home_project_id=)``);
- a desktop chat the user did not aim at a folder starts in the project's primary folder, so its
  ``AGENTS.md`` / project skills load and the row is stored under the project.

No project, two or more, a flag that is off, or a primary folder that is gone: stock behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

CONFIG_KEY = "single_project_home"


def enabled(cfg: dict) -> bool:
    """``desktop.single_project_home`` is literally ``true`` in an effective user config."""
    desktop = cfg.get("desktop") if isinstance(cfg, dict) else None
    return isinstance(desktop, dict) and desktop.get(CONFIG_KEY) is True


def single_project(
        projects: list[dict], exists: Callable[[str], bool] = os.path.isdir) -> Optional[dict]:
    """The only active project, when there is exactly one and its primary folder exists."""
    active = [p for p in projects or [] if not p.get("archived")]
    if len(active) != 1:
        return None
    path = str(active[0].get("primary_path") or "").strip()
    return active[0] if path and exists(path) else None


def _folders(project: dict) -> list[str]:
    paths = [str(project.get("primary_path") or "")]
    paths += [str(f.get("path") or "") for f in project.get("folders") or []]
    return [os.path.abspath(os.path.expanduser(p)) for p in dict.fromkeys(paths) if p]


def _inside(path: str, folders: list[str]) -> bool:
    target = os.path.abspath(os.path.expanduser(path)) if path else ""
    return bool(target) and any(
        target == f or target.startswith(f.rstrip(os.sep) + os.sep) for f in folders)


def eligible(params: dict, source: str, remote_cwd: bool) -> bool:
    """Only a desktop chat with no deliberate workspace: not a folder pick, a branch or ssh."""
    return (source == "desktop" and params.get("cwd_explicit") is not True
            and not params.get("parent_session_id") and not remote_cwd)


def create_cwd(
        params: dict, source: str, resolved: tuple[bool, str, bool], projects: list[dict]) -> Optional[str]:
    """The folder a new session starts in instead of ``resolved``, or None to keep it.

    ``resolved`` is ``_resolve_create_cwd``'s ``(explicit_cwd, session_cwd, remote_cwd)``; a cwd
    already inside one of the project's folders stays where it is.
    """
    explicit, cwd, remote = resolved
    if not eligible(params, source, remote):
        return None
    project = single_project(projects)
    if project is None or (explicit and _inside(cwd, _folders(project))):
        return None
    return os.path.abspath(os.path.expanduser(str(project["primary_path"])))


def profile_create_cwd(
        params: dict, source: str, resolved: tuple[bool, str, bool], home: Path) -> Optional[str]:
    """``create_cwd`` for the profile at ``home``: ITS config flag and ITS projects.db."""
    if not eligible(params, source, resolved[2]):
        return None
    from hermes_cli.config_effective import load_user_config_effective
    db_path = home / "projects.db"
    if not enabled(load_user_config_effective(home / "config.yaml")) or not db_path.is_file():
        return None
    from hermes_cli import projects_db as pdb
    with pdb.connect_closing(db_path) as conn:
        projects = [p.to_dict() for p in pdb.list_projects(conn)]
    return create_cwd(params, source, resolved, projects)
