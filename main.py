#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dango Jump — 在真实桌面/窗口控件之间跳跃的小游戏。

Windows 10/11:
    * Tk 透明覆盖层 + UI Automation
    * 运行：python main.py --delay 3

macOS 14+:
    * PySide6 透明覆盖层 + Accessibility (AXUIElement)
    * 运行：python main.py --delay 3

公共纯逻辑位于 dango_core.py；平台后端分别位于 windows_app.py 和
macos_app.py。依赖与授权步骤见 README.md。
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from dango_core import LIMIT_RETRIES, MAX_RETRIES


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dango Jump：在真实桌面/窗口控件之间蓄力跳跃。"
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="直接扫描整个桌面，而不是启动时的前台窗口/App。",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="启动前倒计时秒数，用于切换到目标窗口（默认 3 秒）。",
    )
    parser.add_argument(
        "--limit-retries",
        action="store_true",
        default=LIMIT_RETRIES,
        help=f"启用有限坠落重试（默认 {LIMIT_RETRIES}）。",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help=f"有限重试时的机会数（默认 {MAX_RETRIES}）。",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if sys.platform == "win32":
        from windows_app import run_windows

        return run_windows(args)

    if sys.platform == "darwin":
        try:
            from macos_app import run_macos
        except ImportError as exc:
            print("缺少 macOS 依赖。请先运行：")
            print("  pip install -r requirements.txt")
            print(f"原始错误：{exc}")
            return 1
        return run_macos(args)

    print("Dango Jump 仅支持 Windows 10/11 与 macOS 14+。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
