#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dango_progress — 本地最高分与设置持久化。

只写用户目录，不联网、不进入仓库。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

PROGRESS_VERSION = 1


@dataclass
class Progress:
    best_score: int = 0
    best_time: Optional[float] = None
    best_stars: int = 0
    plays: int = 0
    muted: bool = False
    version: int = PROGRESS_VERSION


def default_progress_path() -> Path:
    override = os.environ.get("DANGO_PROGRESS_PATH")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "DangoJump"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / "DangoJump" if appdata else Path.home() / ".dango_jump"
    else:
        base = Path.home() / ".dango_jump"
    return base / "progress.json"


def load_progress(path: Optional[Path] = None) -> Progress:
    path = Path(path) if path is not None else default_progress_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return Progress()
        return Progress(
            best_score=max(0, int(raw.get("best_score", 0))),
            best_time=(
                float(raw["best_time"])
                if raw.get("best_time") is not None
                else None
            ),
            best_stars=max(0, min(3, int(raw.get("best_stars", 0)))),
            plays=max(0, int(raw.get("plays", 0))),
            muted=bool(raw.get("muted", False)),
            version=PROGRESS_VERSION,
        )
    except Exception:
        return Progress()


def save_progress(progress: Progress, path: Optional[Path] = None) -> bool:
    path = Path(path) if path is not None else default_progress_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(progress)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".progress-",
            suffix=".json",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp_name, path)
            return True
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass
    except Exception:
        return False


def record_result(
    progress: Progress,
    *,
    score: int,
    elapsed: float,
    stars: int,
    completed: bool,
) -> tuple[Progress, bool]:
    """把一局结果合并进本地记录，返回 (新记录, 是否刷新最高分)。"""
    new_best = score > progress.best_score
    updated = Progress(
        best_score=max(progress.best_score, int(score)),
        best_time=progress.best_time,
        best_stars=max(progress.best_stars, min(3, int(stars))),
        plays=progress.plays + 1,
        muted=progress.muted,
        version=PROGRESS_VERSION,
    )
    if completed:
        if updated.best_time is None or elapsed < updated.best_time:
            updated.best_time = float(elapsed)
    return updated, new_best


__all__ = [
    "Progress",
    "default_progress_path",
    "load_progress",
    "save_progress",
    "record_result",
]
