"""Undo/redo snapshots for projects.

Every mutating API call records a compact snapshot of the editable parts of a
project (options, clips, style, title) *before* the change. The UI can then undo
and redo. Snapshots live in data/history/<pid>.json and are capped so the
workspace stays small.
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

from . import config, store

MAX_SNAPSHOTS = 40
KEEP_KEYS = ("options", "clips", "style", "title")


def _path(pid: str) -> Path:
    d = config.DATA / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{pid}.json"


def _state(project: dict) -> dict:
    return {k: copy.deepcopy(project.get(k)) for k in KEEP_KEYS}


def record(project: dict, label: str = "") -> None:
    """Push the current state onto the undo stack (call before mutating)."""
    path = _path(project["id"])
    data = store.read_json(path, {}) or {}
    undo = data.get("undo") or []
    undo.append({"at": time.time(), "label": label, "state": _state(project)})
    data["undo"] = undo[-MAX_SNAPSHOTS:]
    data["redo"] = []
    store.write_json(path, data)


def can_undo(pid: str) -> bool:
    data = store.read_json(_path(pid), {}) or {}
    return bool(data.get("undo"))


def history_info(pid: str) -> dict:
    data = store.read_json(_path(pid), {}) or {}
    undo = data.get("undo") or []
    redo = data.get("redo") or []
    return {
        "can_undo": bool(undo),
        "can_redo": bool(redo),
        "undo_label": undo[-1]["label"] if undo else None,
    }


def apply_state(project: dict, state: dict) -> dict:
    for key in KEEP_KEYS:
        if key in state:
            project[key] = copy.deepcopy(state[key])
    return project


def undo(project: dict) -> dict:
    path = _path(project["id"])
    data = store.read_json(path, {}) or {}
    undo_stack = data.get("undo") or []
    if not undo_stack:
        raise ValueError("Nothing to undo")
    prev = undo_stack.pop()
    data.setdefault("redo", []).append({"at": time.time(), "label": prev["label"],
                                        "state": _state(project)})
    data["undo"] = undo_stack
    store.write_json(path, data)
    return apply_state(project, prev["state"])


def redo(project: dict) -> dict:
    path = _path(project["id"])
    data = store.read_json(path, {}) or {}
    redo_stack = data.get("redo") or []
    if not redo_stack:
        raise ValueError("Nothing to redo")
    nxt = redo_stack.pop()
    data.setdefault("undo", []).append({"at": time.time(), "label": nxt["label"],
                                        "state": _state(project)})
    data["redo"] = redo_stack
    store.write_json(path, data)
    return apply_state(project, nxt["state"])
