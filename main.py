#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dango Circuit — 在真实桌面控件之间进行多站跳跃的跑酷小游戏。

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

from dango_core import (
    BONUS_STAR_COUNT,
    COURSE_CHECKPOINTS,
    MAX_CHECKPOINTS,
    MIN_CHECKPOINTS,
    START_HEARTS,
    RunConfig,
)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dango Circuit：在真实桌面控件之间进行多站跳跃跑酷。"
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
        "--hearts",
        type=int,
        default=START_HEARTS,
        help=f"每局心数（默认 {START_HEARTS}）；坠落扣一颗。",
    )
    parser.add_argument(
        "--checkpoints",
        type=int,
        default=COURSE_CHECKPOINTS,
        help=f"每局检查点数，含终点（默认 {COURSE_CHECKPOINTS}，范围 {MIN_CHECKPOINTS}-{MAX_CHECKPOINTS}）。",
    )
    parser.add_argument(
        "--stars",
        type=int,
        default=BONUS_STAR_COUNT,
        help=f"每局可选星星数量（默认 {BONUS_STAR_COUNT}）。",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="固定路线随机种子，便于复现和测试。",
    )
    parser.add_argument(
        "--relaxed",
        action="store_true",
        help="练习模式：无限心，坠落不结束本局。",
    )
    parser.add_argument(
        "--mute",
        action="store_true",
        help="静音（仍会保存本地最高分）。",
    )
    # 旧版本参数兼容层；新版本默认就是 3 颗心。
    parser.add_argument(
        "--limit-retries",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    if args.limit_retries or args.max_retries is not None:
        print(
            "提示：--limit-retries/--max-retries 已由 --hearts 取代；"
            "本次按心数处理。",
            flush=True,
        )
        if args.max_retries is not None:
            args.hearts = args.max_retries
    args.hearts = max(1, min(9, int(args.hearts)))
    args.checkpoints = max(
        MIN_CHECKPOINTS,
        min(MAX_CHECKPOINTS, int(args.checkpoints)),
    )
    args.stars = max(0, min(4, int(args.stars)))
    args.run_config = RunConfig(
        hearts=args.hearts,
        checkpoint_count=args.checkpoints,
        star_count=args.stars,
        seed=args.seed,
        relaxed=bool(args.relaxed),
    )
    return args

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

    print("Dango Circuit 仅支持 Windows 10/11 与 macOS 14+。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
