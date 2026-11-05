#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dango_audio — 小型程序化音效播放器（无额外 Python 依赖）。

在 Windows 使用 winsound，在 macOS 使用系统 afplay。音效首次播放时
用标准库 wave 生成并缓存到临时目录；任何失败都静默降级。
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Iterable

SAMPLE_RATE = 22050
SFX_DIR = Path(tempfile.gettempdir()) / "dango_jump_sfx_v1"

# (start_freq, end_freq, seconds, volume)
SOUND_SPECS: dict[str, list[tuple[float, float, float, float]]] = {
    "launch": [(420.0, 780.0, 0.10, 0.22)],
    "land": [(150.0, 90.0, 0.08, 0.30)],
    "checkpoint": [(660.0, 660.0, 0.07, 0.22), (990.0, 990.0, 0.10, 0.22)],
    "perfect": [(1046.5, 1046.5, 0.06, 0.20), (1568.0, 1568.0, 0.13, 0.20)],
    "star": [(880.0, 1320.0, 0.12, 0.22)],
    "fall": [(260.0, 80.0, 0.26, 0.26)],
    "victory": [
        (523.25, 523.25, 0.09, 0.20),
        (659.25, 659.25, 0.09, 0.20),
        (783.99, 783.99, 0.09, 0.20),
        (1046.5, 1046.5, 0.22, 0.22),
    ],
    "game_over": [
        (440.0, 440.0, 0.12, 0.20),
        (349.23, 349.23, 0.12, 0.20),
        (261.63, 220.0, 0.32, 0.22),
    ],
}


def _tone_samples(freq_start: float, freq_end: float, seconds: float, volume: float) -> Iterable[int]:
    count = max(1, int(SAMPLE_RATE * seconds))
    fade = max(1, min(count // 4, int(SAMPLE_RATE * 0.012)))
    phase = 0.0
    for i in range(count):
        progress = i / max(1, count - 1)
        freq = freq_start + (freq_end - freq_start) * progress
        phase += 2.0 * math.pi * freq / SAMPLE_RATE
        env = 1.0
        if i < fade:
            env = i / fade
        elif i > count - fade:
            env = max(0.0, (count - i) / fade)
        value = math.sin(phase) * volume * env
        yield int(max(-1.0, min(1.0, value)) * 32767)


def _ensure_sound(name: str) -> Path | None:
    spec = SOUND_SPECS.get(name)
    if not spec:
        return None
    path = SFX_DIR / f"{name}.wav"
    if path.exists():
        return path
    try:
        SFX_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            frames = bytearray()
            for freq_start, freq_end, seconds, volume in spec:
                for sample in _tone_samples(freq_start, freq_end, seconds, volume):
                    frames.extend(struct.pack("<h", sample))
                # 短促音符之间留 12ms 静音
                frames.extend(b"\x00\x00" * int(SAMPLE_RATE * 0.012))
            wav.writeframes(bytes(frames))
        os.replace(tmp, path)
        return path
    except Exception:
        return None


class SoundPlayer:
    """异步音效播放器；不可用时完全静默。"""

    def __init__(self, *, muted: bool = False) -> None:
        self.muted = bool(muted)
        self._backend = self._detect_backend()
        self._enabled = self._backend is not None

    @staticmethod
    def _detect_backend() -> str | None:
        if sys.platform == "win32":
            try:
                import winsound  # noqa: F401

                return "winsound"
            except Exception:
                return None
        if sys.platform == "darwin":
            return "afplay" if shutil.which("afplay") else None
        return None

    @property
    def available(self) -> bool:
        return bool(self._enabled)

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)

    def play(self, name: str) -> None:
        if not self._enabled or self.muted:
            return
        path = _ensure_sound(name)
        if path is None:
            return
        try:
            if self._backend == "winsound":
                import winsound

                winsound.PlaySound(
                    str(path),
                    winsound.SND_FILENAME
                    | winsound.SND_ASYNC
                    | winsound.SND_NODEFAULT,
                )
            elif self._backend == "afplay":
                subprocess.Popen(
                    ["afplay", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception:
            # 音效永远不能影响游戏主循环。
            pass


__all__ = ["SoundPlayer", "SOUND_SPECS"]
