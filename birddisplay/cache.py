"""Disk cache: the seam between the fetcher and the renderer.

Every write goes through write_json_atomic() so a crash or a power cut
mid-write leaves the previous cache intact. A half-written cache.json
would put a blank screen on the wall until someone noticed.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import Board

log = logging.getLogger(__name__)


class CacheMissing(Exception):
    """No cache on disk yet."""


class CacheCorrupt(Exception):
    """Cache exists but cannot be parsed or is the wrong schema version."""


def write_json_atomic(path: Path, payload: Any) -> None:
    """Serialise payload to path via a temp file in the same directory.

    Same directory matters: os.replace is only atomic within a filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    if not path.exists():
        raise CacheMissing(f"no cache at {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise CacheCorrupt(f"{path} is not valid JSON: {exc}") from exc


def save_board(path: Path, board: Board) -> None:
    write_json_atomic(path, board.to_dict())
    log.info("wrote board cache to %s", path)


def load_board(path: Path) -> Board:
    data = read_json(path)
    try:
        return Board.from_dict(data)
    except (KeyError, ValueError, TypeError) as exc:
        raise CacheCorrupt(f"{path} is not a usable board: {exc}") from exc


def load_state(path: Path) -> dict[str, Any]:
    """Cross-run state. Never fatal -- a missing or broken state file just
    means we forget which species we recently featured."""
    try:
        data = read_json(path)
    except (CacheMissing, CacheCorrupt) as exc:
        log.debug("no usable state file (%s); starting fresh", exc)
        return {"featured": []}
    if not isinstance(data, dict):
        return {"featured": []}
    data.setdefault("featured", [])
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    write_json_atomic(path, state)


def record_featured(state: dict[str, Any], species_code: str, memory: int) -> None:
    """Push a species onto the recently-featured list, newest first."""
    featured = [c for c in state.get("featured", []) if c != species_code]
    featured.insert(0, species_code)
    state["featured"] = featured[: max(memory, 1)]
    state["last_featured_at"] = datetime.now(timezone.utc).isoformat()
