#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dango Jump — 在真实桌面/窗口控件之间跳跃的小游戏（Windows 单主屏）。

核心思路：
    * 用 UI Automation 枚举当前前台窗口（或整个桌面）的可交互控件；
    * 用 Tk 创建一个无边框、置顶、色键透明的全屏覆盖层；
    * 玩家在覆盖层上控制一只水色小团子，从起点控件跳到目标控件。

运行：
    python main.py --delay 3

依赖见 requirements.txt。程序只支持 Windows；在其它平台上会打印提示并退出。
当前文件中的纯逻辑部分不依赖 Windows/Tk，便于单元测试。
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

# =============================================================================
# 可配置参数（集中放在文件顶部，便于调整手感）
# =============================================================================

# 物理 -------------------------------------------------------------------------
GRAVITY = 2200.0                    # 重力，px/s²
CHARGE_SECONDS = 1.15               # 从 0 蓄到 100% 所需秒数
JUMP_DISTANCE_MIN = 80.0            # 0% 蓄力时的水平距离，px
JUMP_DISTANCE_MAX = 1200.0          # 100% 蓄力时的水平距离，px
JUMP_FLIGHT_SECONDS = 0.92          # 同高度完整抛物线飞行时间，秒
CHAR_SIZE = 30                      # 小团子直径，px
CHAR_COLLISION_WIDTH = 24.0         # 落点检测使用的有效宽度，px
MAX_DT = 0.05                       # 单帧最大物理步长，避免切后台后瞬移
MAX_SUBSTEP_PX = 3.5                # 子步最大位移，防止高速穿透
FALL_MARGIN = 90                    # 落出屏幕下方多少像素算一次坠落
MAX_FLIGHT_SECONDS = 8.0            # 极端情况下的飞行保护

# 关卡与枚举 -------------------------------------------------------------------
MIN_CONTROL_SIZE = 20               # 控件宽或高小于该值则忽略，px
START_DISTANCE_THRESHOLD = 420.0    # 起点与目标的期望水平距离，px
START_THRESHOLD_FACTORS = (1.0, 0.7, 0.4, 0.0)  # 不够时逐步放宽
SCAN_TIMEOUT_SECONDS = 2.5          # UI Automation 遍历时间预算
SCAN_MAX_DEPTH = 24
SCAN_MAX_NODES = 3000
TARGET_MOVE_TOLERANCE = 6           # 目标/当前控件移动超过该值则重开，px
MONITOR_INTERVAL_MS = 500           # 目标存活检查间隔

# 重试 -------------------------------------------------------------------------
LIMIT_RETRIES = False               # 是否限制坠落重试次数
MAX_RETRIES = 5

# 窗口与视觉 -------------------------------------------------------------------
APP_NAME = "DANGO JUMP"
TRANSPARENT_KEY = "#010203"         # Windows 色键；美术颜色不要使用该值
FPS_MS = 16

COL_INK = "#07111A"
COL_PANEL = "#0B1622"
COL_PANEL_2 = "#111F2E"
COL_AQUA = "#22D3EE"
COL_AQUA_SOFT = "#67E8F9"
COL_AQUA_DIM = "#155E75"
COL_AMBER = "#FBBF24"
COL_AMBER_SOFT = "#FDE68A"
COL_GREEN = "#34D399"
COL_RED = "#FB7185"
COL_TEXT = "#F8FAFC"
COL_MUTED = "#94A3B8"
COL_LINE = "#334155"

FONT_TITLE = ("Microsoft YaHei UI", -25, "bold")
FONT_H1 = ("Microsoft YaHei UI", -23, "bold")
FONT_BODY = ("Microsoft YaHei UI", -14)
FONT_BODY_BOLD = ("Microsoft YaHei UI", -14, "bold")
FONT_SMALL = ("Microsoft YaHei UI", -12)
FONT_SMALL_BOLD = ("Microsoft YaHei UI", -12, "bold")
FONT_MONO = ("Consolas", -12)

# UI Automation 控件类型。这些类型通常是“可操作”的。
CANDIDATE_CONTROL_TYPES = {
    "ButtonControl",
    "SplitButtonControl",
    "EditControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "ComboBoxControl",
    "HyperlinkControl",
    "ListItemControl",
    "TabItemControl",
    "MenuItemControl",
    "SliderControl",
    "SpinnerControl",
    "TreeItemControl",
}

CONTROL_TYPE_LABELS = {
    "ButtonControl": "按钮",
    "SplitButtonControl": "下拉按钮",
    "EditControl": "输入框",
    "CheckBoxControl": "复选框",
    "RadioButtonControl": "单选框",
    "ComboBoxControl": "下拉框",
    "HyperlinkControl": "链接",
    "ListItemControl": "列表项",
    "TabItemControl": "标签页",
    "MenuItemControl": "菜单项",
    "SliderControl": "滑块",
    "SpinnerControl": "微调框",
    "TreeItemControl": "树节点",
}

# 游戏状态
STATE_READY = "ready"
STATE_CHARGING = "charging"
STATE_AIRBORNE = "airborne"
STATE_VICTORY = "victory"
STATE_GAME_OVER = "game_over"


# =============================================================================
# 基础数据结构
# =============================================================================


@dataclass(frozen=True)
class Rect:
    """屏幕物理像素矩形，right/bottom 为开区间风格。"""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0

    def intersect(self, other: "Rect") -> Optional["Rect"]:
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right, bottom)

    def contains_point(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def overlap_ratio(self, other: "Rect") -> float:
        """交集面积 / 较小矩形面积。用于去掉完全重叠/嵌套控件。"""
        inter = self.intersect(other)
        if inter is None:
            return 0.0
        smaller = min(self.area, other.area)
        if smaller <= 0:
            return 0.0
        return inter.area / float(smaller)


@dataclass
class RawControl:
    """从 UI Automation 读到的原始控件信息，尚未过滤。"""

    rect: Rect
    name: str = ""
    control_type: str = ""
    enabled: bool = True
    offscreen: bool = False
    process_id: int = 0
    source_rect: Optional[Rect] = None
    element: Any = field(default=None, repr=False, compare=False)


@dataclass
class Platform:
    """一个可落脚控件。uid 在一次扫描内唯一。"""

    uid: int
    rect: Rect
    source_rect: Rect
    name: str
    control_type: str
    element: Any = field(default=None, repr=False, compare=False)

    @property
    def center_x(self) -> float:
        return self.rect.center_x

    @property
    def label(self) -> str:
        return control_type_label(self.control_type)


@dataclass
class Level:
    start: Platform
    target: Platform
    platforms: list[Platform]


@dataclass
class ScanResult:
    platforms: list[Platform]
    nodes_seen: int
    skipped: int
    elapsed: float


@dataclass
class UpdateEvents:
    """物理更新向外层汇报的事件。"""

    landed: Optional[Platform] = None
    victory: bool = False
    fell: bool = False
    game_over: bool = False


@dataclass
class Landing:
    platform: Platform
    x: float
    top: int
    t: float


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    color: str
    size: float


class ScanError(RuntimeError):
    """控件读取失败/超时/控件不足。"""

    def __init__(self, message: str, kind: str = "read") -> None:
        super().__init__(message)
        self.kind = kind


class LevelError(RuntimeError):
    """无法从当前候选控件生成关卡。"""


# =============================================================================
# 文本与几何工具
# =============================================================================


def control_type_label(control_type: str) -> str:
    return CONTROL_TYPE_LABELS.get(control_type, "控件")


def sanitize_name(name: str) -> str:
    text = " ".join((name or "").split())
    return text or "未命名"


def truncate_text(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def platform_display_name(platform: Platform) -> str:
    return f"{truncate_text(sanitize_name(platform.name), 26)} · {platform.label}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def rects_differ(a: Rect, b: Rect, tolerance: int) -> bool:
    return (
        abs(a.left - b.left) > tolerance
        or abs(a.top - b.top) > tolerance
        or abs(a.right - b.right) > tolerance
        or abs(a.bottom - b.bottom) > tolerance
    )


def rounded_rect_points(
    x1: float, y1: float, x2: float, y2: float, radius: float, steps: int = 5
) -> list[float]:
    """生成圆角矩形多边形点集（Canvas 使用）。"""
    radius = max(0.0, min(radius, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    if radius <= 0:
        return [x1, y1, x2, y1, x2, y2, x1, y2]
    points: list[float] = []
    corners = (
        (x2 - radius, y1 + radius, -90.0, 0.0),
        (x2 - radius, y2 - radius, 0.0, 90.0),
        (x1 + radius, y2 - radius, 90.0, 180.0),
        (x1 + radius, y1 + radius, 180.0, 270.0),
    )
    for cx, cy, start_deg, end_deg in corners:
        for i in range(steps + 1):
            angle = math.radians(start_deg + (end_deg - start_deg) * i / steps)
            points.extend((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def point_in_rect(x: float, y: float, rect: Rect) -> bool:
    return rect.contains_point(x, y)


# =============================================================================
# 控件过滤
# =============================================================================


def filter_platforms(
    raw_controls: Iterable[RawControl],
    screen: Rect,
    own_pid: Optional[int] = None,
    min_size: int = MIN_CONTROL_SIZE,
) -> list[Platform]:
    """把原始控件过滤为可落脚平台。

    规则：
    * 类型必须属于可交互控件；
    * 可见、启用、非本程序窗口；
    * 与主屏相交并裁剪后宽高 >= min_size；
    * 完全重叠/强嵌套的控件只保留更小的那个（目标是更精确的控件）。
    """
    if own_pid is None:
        own_pid = os.getpid()

    candidates: list[tuple[RawControl, Rect]] = []
    for raw in raw_controls:
        if raw.control_type not in CANDIDATE_CONTROL_TYPES:
            continue
        if not raw.enabled or raw.offscreen:
            continue
        if raw.process_id and raw.process_id == own_pid:
            continue
        # 若控件上边缘本身在屏幕外，裁剪后的“顶部”是屏幕边框而不是控件，
        # 不能作为落脚点。
        if raw.rect.top < screen.top:
            continue
        clipped = raw.rect.intersect(screen)
        if clipped is None:
            continue
        if clipped.width < min_size or clipped.height < min_size:
            continue
        candidates.append((raw, clipped))

    # 面积小的优先保留，避免嵌套父控件吞掉真正的按钮。
    candidates.sort(key=lambda item: item[1].area)
    kept: list[tuple[RawControl, Rect]] = []
    for raw, clipped in candidates:
        if any(clipped.overlap_ratio(other_rect) >= 0.92 for _, other_rect in kept):
            continue
        kept.append((raw, clipped))

    kept.sort(key=lambda item: (item[1].top, item[1].left))
    platforms: list[Platform] = []
    for index, (raw, clipped) in enumerate(kept):
        platforms.append(
            Platform(
                uid=index,
                rect=clipped,
                source_rect=raw.source_rect or raw.rect,
                name=sanitize_name(raw.name),
                control_type=raw.control_type,
                element=raw.element,
            )
        )
    return platforms


# =============================================================================
# 跳跃数学、关卡生成与命中检测
# =============================================================================


def jump_apex() -> float:
    """固定飞行时间下的抛物线最高点（相对起点脚底）。"""
    return GRAVITY * (JUMP_FLIGHT_SECONDS ** 2) / 8.0


def charge_power(elapsed: float) -> float:
    if elapsed <= 0:
        return 0.0
    return clamp(elapsed / CHARGE_SECONDS, 0.0, 1.0)


def jump_distance(power: float) -> float:
    power = clamp(power, 0.0, 1.0)
    return JUMP_DISTANCE_MIN + (JUMP_DISTANCE_MAX - JUMP_DISTANCE_MIN) * power


def initial_velocity(power: float, direction: int) -> tuple[float, float]:
    """返回 (vx, vy)。水平距离由蓄力映射，垂直初速固定。"""
    direction = 1 if direction >= 0 else -1
    distance = jump_distance(power)
    vx = direction * distance / JUMP_FLIGHT_SECONDS
    vy = -GRAVITY * JUMP_FLIGHT_SECONDS / 2.0
    return vx, vy


def trajectory_points(
    start_x: float,
    start_feet: float,
    power: float,
    direction: int,
    duration: float = 0.62,
    samples: int = 18,
) -> list[tuple[float, float]]:
    vx, vy = initial_velocity(power, direction)
    points: list[tuple[float, float]] = []
    total = max(0.05, duration)
    for i in range(1, samples + 1):
        t = total * i / samples
        points.append(
            (
                start_x + vx * t,
                start_feet + vy * t + 0.5 * GRAVITY * t * t,
            )
        )
    return points


def can_directly_reach(start: Platform, target: Platform) -> bool:
    """关卡生成用的轻量可达性检查（忽略中间控件遮挡）。

    只保证从起点到目标的“空旷直线抛物线”有一个合理的蓄力范围，
    中间控件仍可能成为落点，但那通常会提供继续前进的平台。
    """
    dx = abs(start.center_x - target.center_x)
    if dx > JUMP_DISTANCE_MAX:
        return False
    # 目标顶部若高于起点可达到的最高点，则无法从上方落下。
    rise = start.rect.top - target.rect.top
    if rise > jump_apex() - 18.0:
        return False
    return True


def generate_level(platforms: Sequence[Platform], rng: Optional[random.Random] = None) -> Level:
    """随机目标 + 逐级放宽阈值选起点。"""
    if len(platforms) < 2:
        raise LevelError("至少需要 2 个可用控件才能生成关卡。")

    rng = rng or random.Random()
    all_platforms = list(platforms)
    targets = list(all_platforms)
    rng.shuffle(targets)

    for target in targets[: min(24, len(targets))]:
        for factor in START_THRESHOLD_FACTORS:
            threshold = START_DISTANCE_THRESHOLD * factor
            eligible = [
                p
                for p in all_platforms
                if p.uid != target.uid
                and abs(p.center_x - target.center_x) >= threshold
            ]
            if not eligible:
                continue

            # 只保留基本可达的起点，再从中挑水平距离最远的几个。
            viable = [p for p in eligible if can_directly_reach(p, target)]
            if not viable:
                continue
            viable.sort(key=lambda p: abs(p.center_x - target.center_x), reverse=True)
            start = rng.choice(viable[:3])
            return Level(start=start, target=target, platforms=all_platforms)

    raise LevelError("当前候选控件之间距离过远或高度差过大，无法生成可达关卡。")


def find_landing(
    prev_x: float,
    prev_feet: float,
    cur_x: float,
    cur_feet: float,
    platforms: Sequence[Platform],
    half_width: float = CHAR_COLLISION_WIDTH / 2.0,
) -> Optional[Landing]:
    """扫掠检测：角色下落时最先穿过哪个控件的上边缘。

    只把控件上边缘当作可站立面；选择交点时间最早的控件。这样既不会
    穿过控件，也能保证路径上多个控件时只落在第一个。
    """
    if cur_feet <= prev_feet + 1e-9:
        return None

    best: Optional[Landing] = None
    for platform in platforms:
        rect = platform.rect
        top = rect.top
        if top < prev_feet - 1e-6 or top > cur_feet + 1e-6:
            continue
        denom = cur_feet - prev_feet
        if denom <= 1e-9:
            continue
        t = (top - prev_feet) / denom
        if t < -1e-6 or t > 1.0 + 1e-6:
            continue
        x = prev_x + (cur_x - prev_x) * t
        if x + half_width <= rect.left + 1 or x - half_width >= rect.right - 1:
            continue
        if best is None or t < best.t - 1e-9:
            best = Landing(platform=platform, x=x, top=top, t=t)
    return best


# =============================================================================
# 游戏状态机
# =============================================================================


class GameModel:
    """与 Tk/Windows 无关的游戏逻辑，便于单元测试。"""

    def __init__(
        self,
        level: Level,
        screen: Rect,
        *,
        limit_retries: bool = LIMIT_RETRIES,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.level = level
        self.screen = screen
        self.limit_retries = limit_retries
        self.max_retries = max_retries
        self.state = STATE_READY
        self.direction = 1
        self.power = 0.0
        self.charge_started = 0.0
        self.char_x = 0.0
        self.char_feet = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.current = level.start
        self.jumps = 0
        self.falls = 0
        self.flight_time = 0.0
        self.missed = False
        self._reset_position()

    # -- 查询 -----------------------------------------------------------------

    @property
    def remaining_retries(self) -> Optional[int]:
        if not self.limit_retries:
            return None
        return max(0, self.max_retries - self.falls)

    @property
    def is_charging(self) -> bool:
        return self.state == STATE_CHARGING

    # -- 关卡/位置 -------------------------------------------------------------

    def _default_direction(self) -> int:
        return 1 if self.level.target.center_x >= self.level.start.center_x else -1

    def _reset_position(self) -> None:
        self.current = self.level.start
        self.char_x = self.level.start.rect.center_x
        self.char_feet = float(self.level.start.rect.top)
        self.vx = 0.0
        self.vy = 0.0
        self.power = 0.0
        self.flight_time = 0.0
        self.missed = False
        self.direction = self._default_direction()
        self.state = STATE_READY

    def load_level(self, level: Level, *, preserve_stats: bool = True) -> None:
        old_jumps = self.jumps
        old_falls = self.falls
        self.level = level
        if preserve_stats:
            self.jumps = old_jumps
            self.falls = old_falls
        else:
            self.jumps = 0
            self.falls = 0
        self._reset_position()

    # -- 输入 -----------------------------------------------------------------

    def set_direction(self, direction: int) -> bool:
        if self.state not in (STATE_READY, STATE_CHARGING):
            return False
        self.direction = 1 if direction >= 0 else -1
        return True

    def begin_charge(self, now: float) -> bool:
        if self.state != STATE_READY:
            return False
        self.state = STATE_CHARGING
        self.charge_started = now
        self.power = 0.0
        return True

    def update_charge(self, now: float) -> None:
        if self.state == STATE_CHARGING:
            self.power = charge_power(now - self.charge_started)

    def cancel_charge(self) -> None:
        if self.state == STATE_CHARGING:
            self.power = 0.0
            self.state = STATE_READY

    def release(self, now: float) -> bool:
        if self.state != STATE_CHARGING:
            return False
        self.update_charge(now)
        self.vx, self.vy = initial_velocity(self.power, self.direction)
        self.state = STATE_AIRBORNE
        self.jumps += 1
        self.flight_time = 0.0
        self.missed = False
        return True

    # -- 物理 -----------------------------------------------------------------

    def tick(self, dt: float) -> UpdateEvents:
        events = UpdateEvents()
        if self.state != STATE_AIRBORNE:
            return events

        dt = clamp(float(dt), 0.0, MAX_DT)
        if dt <= 0:
            return events

        # 根据最大速度决定子步数量，避免高速穿过细控件。
        speed = max(abs(self.vx), abs(self.vy) + GRAVITY * dt)
        displacement = speed * dt
        steps = max(1, min(32, int(math.ceil(displacement / MAX_SUBSTEP_PX))))
        sub_dt = dt / steps

        for _ in range(steps):
            prev_x = self.char_x
            prev_feet = self.char_feet

            self.char_x += self.vx * sub_dt
            self.vy += GRAVITY * sub_dt
            self.char_feet += self.vy * sub_dt
            self.flight_time += sub_dt

            if not self.missed:
                landing = find_landing(
                    prev_x,
                    prev_feet,
                    self.char_x,
                    self.char_feet,
                    self.level.platforms,
                )
                if landing is not None:
                    self._land_on(landing)
                    events.landed = landing.platform
                    if self.state == STATE_VICTORY:
                        events.victory = True
                    return events

                if (
                    self.char_x < -FALL_MARGIN
                    or self.char_x > self.screen.right + FALL_MARGIN
                ):
                    self.missed = True

            if (
                self.char_feet > self.screen.bottom + FALL_MARGIN
                or self.flight_time > MAX_FLIGHT_SECONDS
            ):
                events.fell = True
                events.game_over = self._finish_fall()
                return events

        return events

    def _land_on(self, landing: Landing) -> None:
        platform = landing.platform
        rect = platform.rect
        margin = CHAR_COLLISION_WIDTH / 2.0 + 2.0
        if rect.width <= margin * 2 + 2:
            x = rect.center_x
        else:
            x = clamp(landing.x, rect.left + margin, rect.right - margin)

        self.char_x = x
        self.char_feet = float(rect.top)
        self.vx = 0.0
        self.vy = 0.0
        self.power = 0.0
        self.flight_time = 0.0
        self.missed = False
        self.current = platform
        if platform.uid == self.level.target.uid:
            self.state = STATE_VICTORY
        else:
            self.state = STATE_READY

    def _finish_fall(self) -> bool:
        """完成一次坠落。返回是否进入 game over。"""
        self.falls += 1
        self.vx = 0.0
        self.vy = 0.0
        self.power = 0.0
        if self.limit_retries and self.falls >= self.max_retries:
            self.state = STATE_GAME_OVER
            return True
        self._reset_position()
        return False

    def force_land_for_test(self, platform: Platform, x: Optional[float] = None) -> None:
        """测试辅助：直接完成一次落点判定，不参与正式流程。"""
        px = self.char_x if x is None else x
        self._land_on(Landing(platform=platform, x=px, top=platform.rect.top, t=0.0))


# =============================================================================
# Windows：DPI、窗口、UI Automation
# =============================================================================


def enable_dpi_awareness() -> None:
    """必须在创建 Tk 窗口之前调用。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            try:
                if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                    return
            except Exception:
                pass
        try:
            shcore = ctypes.windll.shcore
            if hasattr(shcore, "SetProcessDpiAwareness"):
                # PROCESS_PER_MONITOR_DPI_AWARE = 2
                if shcore.SetProcessDpiAwareness(2) in (0, 0x80070005, -2147024891):
                    return
        except Exception:
            pass
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        # DPI 声明失败时仍允许启动，但坐标可能在高 DPI 下不准确。
        pass


def primary_screen_rect() -> Rect:
    if sys.platform != "win32":
        raise RuntimeError("Dango Jump 仅支持 Windows。")
    import ctypes

    user32 = ctypes.windll.user32
    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    if width <= 0 or height <= 0:
        raise RuntimeError("无法读取主屏尺寸。")
    return Rect(0, 0, width, height)


def get_window_title(hwnd: int) -> str:
    if sys.platform != "win32" or not hwnd:
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value.strip()
    except Exception:
        return ""


def get_foreground_window_info() -> tuple[int, str]:
    if sys.platform != "win32":
        raise RuntimeError("Dango Jump 仅支持 Windows。")
    import ctypes

    hwnd = int(ctypes.windll.user32.GetForegroundWindow())
    return hwnd, get_window_title(hwnd)


def set_tool_window(root: Any) -> None:
    """让覆盖层不出现在任务栏/Alt-Tab 中。失败不影响游戏。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(user32.GetAncestor(root.winfo_id(), 2))  # GA_ROOT
        if not hwnd:
            return
        gwl_exstyle = -20
        ws_ex_toolwindow = 0x00000080
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_long(hwnd, gwl_exstyle))
        set_long(hwnd, gwl_exstyle, style | ws_ex_toolwindow)
    except Exception:
        pass


def root_hwnd(root: Any) -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.user32.GetAncestor(root.winfo_id(), 2))
    except Exception:
        return 0


def force_foreground(hwnd: int) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _uia_rect(value: Any) -> Optional[Rect]:
    if value is None:
        return None
    try:
        left = int(round(float(value.left)))
        top = int(round(float(value.top)))
        right = int(round(float(value.right)))
        bottom = int(round(float(value.bottom)))
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right, bottom)
    except Exception:
        return None


def _safe_attr(control: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(control, name)
    except Exception:
        return default


def scan_controls(
    scope: str,
    hwnd: int,
    screen: Rect,
    *,
    own_pid: Optional[int] = None,
) -> ScanResult:
    """枚举前台窗口或桌面控件并过滤。

    scope: "window" 或 "desktop"。
    """
    if sys.platform != "win32":
        raise ScanError("Dango Jump 仅支持 Windows。", kind="platform")

    try:
        import uiautomation as auto
    except Exception as exc:
        raise ScanError(
            "未安装 uiautomation。请运行：pip install -r requirements.txt\n"
            f"原始错误：{exc}",
            kind="dependency",
        ) from exc

    try:
        if scope == "desktop":
            root = auto.GetRootControl()
        else:
            if not hwnd:
                raise ScanError("没有可读取的前台窗口。", kind="window")
            root = auto.ControlFromHandle(int(hwnd))
    except ScanError:
        raise
    except Exception as exc:
        raise ScanError(f"无法读取当前窗口控件：{exc}", kind="read") from exc

    # 导入 UIAutomation 的冷启动时间不计入控件遍历预算。
    started = time.monotonic()
    own_pid = os.getpid() if own_pid is None else own_pid
    deadline = started + SCAN_TIMEOUT_SECONDS
    queue: deque[tuple[Any, int]] = deque([(root, 0)])
    raw_controls: list[RawControl] = []
    nodes_seen = 0
    skipped = 0

    while queue:
        if time.monotonic() > deadline:
            raise ScanError(
                f"读取控件超时（超过 {SCAN_TIMEOUT_SECONDS:.1f} 秒），"
                "请重试或按 D 扫描整个桌面。",
                kind="timeout",
            )

        control, depth = queue.popleft()
        nodes_seen += 1
        if nodes_seen > SCAN_MAX_NODES:
            break

        try:
            control_type = str(_safe_attr(control, "ControlTypeName", "") or "")
            if control_type in CANDIDATE_CONTROL_TYPES:
                rect = _uia_rect(_safe_attr(control, "BoundingRectangle", None))
                if rect is not None:
                    enabled = bool(_safe_attr(control, "IsEnabled", True))
                    offscreen = bool(_safe_attr(control, "IsOffscreen", False))
                    pid = int(_safe_attr(control, "ProcessId", 0) or 0)
                    name = str(_safe_attr(control, "Name", "") or "")
                    automation_id = str(_safe_attr(control, "AutomationId", "") or "")
                    if not name:
                        name = automation_id
                    raw_controls.append(
                        RawControl(
                            rect=rect,
                            source_rect=rect,
                            name=name,
                            control_type=control_type,
                            enabled=enabled,
                            offscreen=offscreen,
                            process_id=pid,
                            element=control,
                        )
                    )
        except Exception:
            skipped += 1

        if depth >= SCAN_MAX_DEPTH:
            continue
        try:
            children = control.GetChildren()
        except Exception:
            skipped += 1
            children = []
        if not children:
            continue
        for child in children:
            queue.append((child, depth + 1))

    elapsed = time.monotonic() - started
    platforms = filter_platforms(raw_controls, screen, own_pid=own_pid)
    if len(platforms) < 2:
        raise ScanError(
            f"当前范围可用控件不足（找到 {len(platforms)} 个）。"
            "请换一个控件更多的窗口，或按 D 扫描整个桌面。",
            kind="insufficient",
        )
    return ScanResult(
        platforms=platforms,
        nodes_seen=nodes_seen,
        skipped=skipped,
        elapsed=elapsed,
    )


def platform_is_live(platform: Platform, tolerance: int = TARGET_MOVE_TOLERANCE) -> bool:
    """查询 UIA 判断控件是否仍存在、可见且没有移动。"""
    element = platform.element
    if element is None:
        return True
    try:
        if bool(_safe_attr(element, "IsOffscreen", False)):
            return False
        live = _uia_rect(_safe_attr(element, "BoundingRectangle", None))
        if live is None:
            return False
        return not rects_differ(platform.source_rect, live, tolerance)
    except Exception:
        return False


# =============================================================================
# Tk 覆盖层
# =============================================================================


class OverlayApp:
    """透明覆盖层、输入、渲染与 UI Automation 生命周期。"""

    def __init__(
        self,
        screen: Rect,
        scope: str,
        hwnd: int,
        window_title: str,
        *,
        limit_retries: bool = LIMIT_RETRIES,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        import tkinter as tk

        self.tk = tk
        self.screen = screen
        self.scope = scope
        self.hwnd = hwnd
        self.window_title = window_title or "未命名窗口"
        self.limit_retries = limit_retries
        self.max_retries = max_retries

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.geometry(f"{screen.width}x{screen.height}+0+0")
        self.root.configure(bg=TRANSPARENT_KEY)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError as exc:
            self.root.destroy()
            raise RuntimeError("当前 Windows 环境不支持透明覆盖层（-transparentcolor）。") from exc

        self.canvas = tk.Canvas(
            self.root,
            width=screen.width,
            height=screen.height,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
            takefocus=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.root.update_idletasks()
        set_tool_window(self.root)
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)

        # 运行状态
        self.mode = "scanning"          # scanning / playing / error / victory / game_over
        self.model: Optional[GameModel] = None
        self.scanning = True
        self.running = True
        self.focus_lost = False
        self._suppress_focus = False
        self._focus_grace_until = time.monotonic() + 1.2
        self._space_down = False
        self._last_tick = time.perf_counter()
        self._scan_reason = "正在读取当前窗口控件…"
        self._card_buttons: list[dict[str, Any]] = []
        self._hover_button: Optional[str] = None
        self._toast_text = ""
        self._toast_until = 0.0
        self._particles: list[Particle] = []
        self._sprites: dict[int, Any] = {}
        self._fallback_sprite = False
        self._hud_target_id: Optional[int] = None
        self._hud_stats_id: Optional[int] = None
        self._hud_hint_id: Optional[int] = None
        self._target_outline_ids: list[int] = []
        self._monitor_job: Optional[str] = None
        self._tick_job: Optional[str] = None
        self._release_job: Optional[str] = None
        self._focus_check_job: Optional[str] = None
        self._last_scan: Optional[ScanResult] = None

        self._build_sprites()
        self._bind_events()
        self._draw_scanning_card()
        self._grab_focus()
        # 先让扫描卡片完成一次绘制，再做可能耗时的 UIA 枚举。
        self.root.after(140, lambda: self._perform_scan(reset_stats=True))
        self._tick_job = self.root.after(FPS_MS, self._tick)

    # =========================================================================
    # 生命周期与焦点
    # =========================================================================

    def run(self) -> None:
        self.root.mainloop()

    def _bind_events(self) -> None:
        self.root.bind("<KeyPress>", self._on_key_press)
        self.root.bind("<KeyRelease>", self._on_key_release)
        self.root.bind("<FocusIn>", self._on_focus_in)
        self.root.bind("<FocusOut>", self._on_focus_out)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self.canvas.configure(cursor="arrow"))

    def _grab_focus(self) -> None:
        if not self.running:
            return
        self._suppress_focus = False
        self.focus_lost = False
        if self._release_job:
            try:
                self.root.after_cancel(self._release_job)
            except Exception:
                pass
            self._release_job = None
        try:
            self.canvas.delete("focus_hint")
        except Exception:
            pass
        self._focus_grace_until = time.monotonic() + 0.8
        try:
            hwnd = root_hwnd(self.root)
            if hwnd:
                force_foreground(hwnd)
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        if self._focus_check_job:
            try:
                self.root.after_cancel(self._focus_check_job)
            except Exception:
                pass
        self._focus_check_job = self.root.after(260, self._verify_focus)

    def _verify_focus(self) -> None:
        """Windows 偶尔会拒绝 SetForegroundWindow；主动检查并提示点击。"""
        self._focus_check_job = None
        if not self.running or self._suppress_focus:
            return
        if self.mode in ("victory", "game_over"):
            return
        if sys.platform != "win32":
            return
        try:
            import ctypes

            foreground = int(ctypes.windll.user32.GetForegroundWindow())
            own = root_hwnd(self.root)
            if own and foreground != own:
                self.focus_lost = True
                self._space_down = False
                if self.model is not None:
                    self.model.cancel_charge()
                if self.mode in ("scanning", "error"):
                    self._draw_card_focus_hint()
        except Exception:
            pass

    def _draw_card_focus_hint(self) -> None:
        """扫描/错误卡片失去焦点时，给一个可见且可点击的恢复提示。"""
        if not self.running:
            return
        try:
            self.canvas.delete("focus_hint")
            w = self.screen.width
            x1, x2 = w / 2 - 220, w / 2 + 220
            self._pill(
                x1,
                96,
                x2,
                142,
                COL_PANEL_2,
                COL_RED,
                12,
                tags=("focus_hint",),
            )
            self.canvas.create_text(
                w / 2,
                119,
                text="键盘焦点已丢失 · 点击此处恢复",
                fill=COL_RED,
                font=FONT_SMALL_BOLD,
                tags=("focus_hint",),
            )
        except Exception:
            pass

    def _on_focus_in(self, _event: Any = None) -> None:
        self.focus_lost = False
        try:
            self.canvas.delete("focus_hint")
        except Exception:
            pass

    def _on_focus_out(self, _event: Any = None) -> None:
        if not self.running or self._suppress_focus:
            return
        if time.monotonic() < self._focus_grace_until:
            return
        if self.mode in ("victory", "game_over"):
            return
        self.focus_lost = True
        self._space_down = False
        if self.model is not None:
            self.model.cancel_charge()
        if self.mode in ("scanning", "error"):
            self._draw_card_focus_hint()

    def _release_keyboard_focus(self) -> None:
        """胜利/结束时把键盘还给启动前的窗口，覆盖层卡片仍可见。"""
        self._release_job = None
        if not self.running:
            return
        self._suppress_focus = True
        self.focus_lost = False
        if self.hwnd:
            force_foreground(self.hwnd)

    def _quit(self) -> None:
        if not self.running:
            return
        self.running = False
        for job in (self._tick_job, self._monitor_job, self._release_job, self._focus_check_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        previous = self.hwnd
        try:
            self.root.destroy()
        except Exception:
            pass
        if previous:
            force_foreground(previous)

    # =========================================================================
    # 输入
    # =========================================================================

    def _on_key_press(self, event: Any) -> str:
        keysym = str(getattr(event, "keysym", ""))
        if keysym == "Escape":
            self._quit()
            return "break"

        if self.mode == "error":
            if keysym.lower() == "r":
                self._action_retry()
            elif keysym.lower() == "d":
                self._action_desktop()
            return "break"

        if self.mode in ("victory", "game_over"):
            if keysym.lower() == "r":
                self._action_restart()
            return "break"

        if self.mode != "playing" or self.model is None:
            return "break"

        if self.focus_lost:
            if keysym == "space":
                self._grab_focus()
            return "break"

        if keysym == "Left":
            self.model.set_direction(-1)
            return "break"
        if keysym == "Right":
            self.model.set_direction(1)
            return "break"
        if keysym == "space":
            if not self._space_down:
                self._space_down = True
                self.model.begin_charge(time.perf_counter())
            return "break"
        if keysym.lower() == "r":
            self._begin_scan(reset_stats=True, reason="正在重新读取控件并生成新关卡…")
            return "break"
        return "break"

    def _on_key_release(self, event: Any) -> str:
        if str(getattr(event, "keysym", "")) != "space":
            return "break"
        if not self._space_down:
            return "break"
        self._space_down = False
        if (
            self.mode == "playing"
            and self.model is not None
            and not self.focus_lost
        ):
            now = time.perf_counter()
            if self.model.release(now):
                self._refresh_hud()
        return "break"

    def _on_click(self, event: Any) -> str:
        x, y = float(event.x), float(event.y)
        for button in self._card_buttons:
            if point_in_rect(x, y, button["rect"]):
                self._grab_focus()
                self._activate_action(str(button["action"]))
                return "break"

        if self.focus_lost or self.mode in ("victory", "game_over"):
            self._grab_focus()
        return "break"

    def _on_motion(self, event: Any) -> None:
        hover: Optional[str] = None
        for button in self._card_buttons:
            if point_in_rect(event.x, event.y, button["rect"]):
                hover = str(button["action"])
                break
        if hover != self._hover_button:
            self._hover_button = hover
            self.canvas.configure(cursor="hand2" if hover else "arrow")

    def _activate_action(self, action: str) -> None:
        if action == "retry":
            self._action_retry()
        elif action == "desktop":
            self._action_desktop()
        elif action == "restart":
            self._action_restart()
        elif action == "quit":
            self._quit()

    def _action_retry(self) -> None:
        self._grab_focus()
        preserve = self.model is not None
        self._begin_scan(
            reset_stats=not preserve,
            reason="正在重新读取当前窗口控件…",
        )

    def _action_desktop(self) -> None:
        self.scope = "desktop"
        self.window_title = "整个桌面"
        self._grab_focus()
        self._begin_scan(
            reset_stats=self.model is None,
            reason="正在扫描整个桌面控件…",
        )

    def _action_restart(self) -> None:
        self._grab_focus()
        self._suppress_focus = False
        self._begin_scan(reset_stats=True, reason="正在重新读取控件并生成新关卡…")

    # =========================================================================
    # 扫描与关卡
    # =========================================================================

    def _begin_scan(self, *, reset_stats: bool, reason: str) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.mode = "scanning"
        self.focus_lost = False
        self._space_down = False
        self._scan_reason = reason
        if self._release_job:
            try:
                self.root.after_cancel(self._release_job)
            except Exception:
                pass
            self._release_job = None
        if self._monitor_job:
            try:
                self.root.after_cancel(self._monitor_job)
            except Exception:
                pass
            self._monitor_job = None
        self._draw_scanning_card()
        self.root.after(90, lambda: self._perform_scan(reset_stats=reset_stats))

    def _perform_scan(self, *, reset_stats: bool) -> None:
        if not self.running:
            self.scanning = False
            return
        try:
            result = scan_controls(
                self.scope,
                self.hwnd,
                self.screen,
                own_pid=os.getpid(),
            )
            level = generate_level(result.platforms)
        except (ScanError, LevelError) as exc:
            self.scanning = False
            self._last_scan = None
            self._show_error_card(str(exc))
            return
        except Exception as exc:
            self.scanning = False
            traceback.print_exc()
            self._show_error_card(f"读取控件时发生意外错误：{exc}")
            return

        self._last_scan = result
        preserve = (not reset_stats) and self.model is not None
        if preserve:
            self.model.load_level(level, preserve_stats=True)
        else:
            self.model = GameModel(
                level,
                self.screen,
                limit_retries=self.limit_retries,
                max_retries=self.max_retries,
            )

        self.scanning = False
        self.mode = "playing"
        self._particles.clear()
        self._space_down = False
        self._build_scene()
        self._grab_focus()
        self._schedule_monitor()

    def _schedule_monitor(self) -> None:
        if self._monitor_job:
            try:
                self.root.after_cancel(self._monitor_job)
            except Exception:
                pass
        self._monitor_job = self.root.after(MONITOR_INTERVAL_MS, self._monitor_targets)

    def _monitor_targets(self) -> None:
        self._monitor_job = None
        if not self.running:
            return
        if self.mode != "playing" or self.model is None or self.scanning:
            if self.mode == "playing":
                self._schedule_monitor()
            return

        # 失焦时保持暂停，避免用户切到别的窗口后游戏又强行抢回焦点。
        if self.focus_lost:
            self._schedule_monitor()
            return

        target = self.model.level.target
        current = self.model.current
        target_ok = platform_is_live(target)
        current_ok = current.uid == target.uid or platform_is_live(current)

        if not target_ok or not current_ok:
            self._begin_scan(
                reset_stats=False,
                reason="控件已消失或移动，正在重新生成关卡…",
            )
            return

        self._schedule_monitor()

    # =========================================================================
    # 游戏循环
    # =========================================================================

    def _tick(self) -> None:
        if not self.running:
            return
        now = time.perf_counter()
        dt = clamp(now - self._last_tick, 0.0, MAX_DT)
        self._last_tick = now

        try:
            if self.mode == "playing" and self.model is not None:
                if not self.focus_lost:
                    if self.model.state == STATE_CHARGING:
                        self.model.update_charge(now)
                    elif self.model.state == STATE_AIRBORNE:
                        events = self.model.tick(dt)
                        self._handle_events(events)
                self._update_particles(dt)
                self._pulse_target(now)
                self._draw_dynamic(now)
            elif self.mode == "victory" and now > self._toast_until:
                pass
        except Exception:
            traceback.print_exc()
            self._show_error_card("游戏运行发生异常，请按 R 重试。")

        if self.running:
            self._tick_job = self.root.after(FPS_MS, self._tick)

    def _handle_events(self, events: UpdateEvents) -> None:
        if events.landed is not None:
            self._spawn_particles(
                self.model.char_x,
                self.model.char_feet,
                9,
                COL_AQUA_SOFT,
            )
            self._refresh_hud()
            self._draw_markers()

        if events.victory:
            self._enter_victory()
            return

        if events.game_over:
            self._enter_game_over()
            return

        if events.fell:
            self.show_toast(
                "坠落！已回到起点。" if not self.model.limit_retries else "坠落！损失一次机会。",
                1.8,
            )
            self._refresh_hud()
            self._draw_markers()

    def _enter_victory(self) -> None:
        self.mode = "victory"
        self._space_down = False
        self._draw_victory_card()
        self._release_job = self.root.after(320, self._release_keyboard_focus)

    def _enter_game_over(self) -> None:
        self.mode = "game_over"
        self._space_down = False
        self._draw_game_over_card()
        self._release_job = self.root.after(320, self._release_keyboard_focus)

    def show_toast(self, text: str, seconds: float = 1.8) -> None:
        self._toast_text = text
        self._toast_until = time.perf_counter() + seconds

    def _spawn_particles(self, x: float, y: float, count: int, color: str) -> None:
        rng = random.Random()
        for _ in range(count):
            angle = rng.uniform(math.pi * 1.05, math.pi * 1.95)
            speed = rng.uniform(70.0, 180.0)
            life = rng.uniform(0.25, 0.5)
            self._particles.append(
                Particle(
                    x=x,
                    y=y,
                    vx=math.cos(angle) * speed,
                    vy=math.sin(angle) * speed - 40.0,
                    life=life,
                    max_life=life,
                    color=color if rng.random() > 0.25 else COL_TEXT,
                    size=rng.uniform(2.0, 4.0),
                )
            )

    def _update_particles(self, dt: float) -> None:
        if not self._particles:
            return
        alive: list[Particle] = []
        for particle in self._particles:
            particle.life -= dt
            if particle.life <= 0:
                continue
            particle.vy += GRAVITY * 0.35 * dt
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            alive.append(particle)
        self._particles = alive

    # =========================================================================
    # 绘制：场景、HUD、动态层
    # =========================================================================

    def _build_sprites(self) -> None:
        """用 Pillow 生成抗锯齿小团子；失败时退回 Canvas 图形。"""
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            self._fallback_sprite = True
            return

        try:
            scale = 4
            size = CHAR_SIZE
            images: dict[int, Any] = {}
            for direction in (-1, 1):
                px = size * scale
                image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                margin = 2 * scale
                top = 3 * scale
                bottom = px - 2 * scale
                radius = 7 * scale
                draw.rounded_rectangle(
                    (margin, top, px - margin, bottom),
                    radius=radius,
                    fill=(34, 211, 238, 255),
                    outline=(7, 17, 26, 255),
                    width=max(2, 1 * scale),
                )
                # 高光
                draw.ellipse(
                    (
                        margin + 4 * scale,
                        top + 3 * scale,
                        margin + 9 * scale,
                        top + 6 * scale,
                    ),
                    fill=(236, 254, 255, 200),
                )
                # 眼睛：朝向变化时左右移动
                eye_shift = 1.2 * scale * direction
                eye_y1 = top + 8 * scale
                eye_y2 = top + 13 * scale
                for eye_x in (px * 0.37, px * 0.63):
                    draw.ellipse(
                        (
                            eye_x - 1.5 * scale + eye_shift,
                            eye_y1,
                            eye_x + 1.5 * scale + eye_shift,
                            eye_y2,
                        ),
                        fill=(7, 17, 26, 255),
                    )
                    draw.ellipse(
                        (
                            eye_x - 1.1 * scale + eye_shift,
                            eye_y1 + 0.4 * scale,
                            eye_x - 0.1 * scale + eye_shift,
                            eye_y1 + 1.4 * scale,
                        ),
                        fill=(248, 250, 252, 255),
                    )
                # 腮红
                draw.ellipse(
                    (
                        margin + 2 * scale,
                        top + 13 * scale,
                        margin + 6 * scale,
                        top + 15 * scale,
                    ),
                    fill=(251, 113, 133, 140),
                )
                draw.ellipse(
                    (
                        px - margin - 6 * scale,
                        top + 13 * scale,
                        px - margin - 2 * scale,
                        top + 15 * scale,
                    ),
                    fill=(251, 113, 133, 140),
                )
                resized = image.resize((size, size), Image.LANCZOS)
                images[direction] = ImageTk.PhotoImage(resized)
            self._sprites = images
        except Exception:
            self._sprites = {}
            self._fallback_sprite = True

    def _rounded(self, x1: float, y1: float, x2: float, y2: float, radius: float,
                 fill: str, outline: str = "", width: int = 1,
                 tags: tuple[str, ...] = ()) -> int:
        points = rounded_rect_points(x1, y1, x2, y2, radius)
        return self.canvas.create_polygon(
            points,
            fill=fill,
            outline=outline or fill,
            width=width,
            tags=tags,
        )

    def _pill(self, x1: float, y1: float, x2: float, y2: float, fill: str,
              outline: str = COL_LINE, radius: Optional[float] = None,
              tags: tuple[str, ...] = ()) -> int:
        radius = min(12.0, (y2 - y1) / 2.0) if radius is None else radius
        return self._rounded(x1, y1, x2, y2, radius, fill, outline, 1, tags=tags)

    def _build_scene(self) -> None:
        self.canvas.delete("all")
        self._card_buttons = []
        self._hover_button = None
        if self.model is None:
            return
        self._draw_candidate_outlines()
        self._draw_hud()
        # 先画 HUD，再画标记，确保靠近屏幕顶部的目标不会被 HUD 盖住。
        self._draw_markers()
        self._draw_dynamic(time.perf_counter())

    def _draw_candidate_outlines(self) -> None:
        if self.model is None:
            return
        for platform in self.model.level.platforms:
            r = platform.rect
            self.canvas.create_rectangle(
                r.left,
                r.top,
                r.right,
                r.bottom,
                outline="#3B4A5E",
                width=1,
                dash=(1, 5),
                tags=("candidate",),
            )

    def _draw_markers(self) -> None:
        if self.model is None:
            return
        self.canvas.delete("markers")
        self._target_outline_ids = []
        level = self.model.level

        # 起点：从平台左上角伸出的短标记 + 文本
        start = level.start.rect
        sx = max(8, start.left + 4)
        sy = max(28, start.top - 24)
        self._pill(sx - 2, sy - 13, sx + 50, sy + 13, COL_PANEL, COL_AQUA_DIM, 8,
                   tags=("markers",))
        self.canvas.create_text(
            sx + 24,
            sy,
            text="起点",
            fill=COL_AQUA,
            font=FONT_SMALL_BOLD,
            tags=("markers",),
        )
        self.canvas.create_polygon(
            sx + 24,
            start.top - 1,
            sx + 18,
            start.top - 11,
            sx + 30,
            start.top - 11,
            fill=COL_AQUA,
            outline=COL_INK,
            width=1,
            tags=("markers",),
        )

        # 当前平台高亮
        cur = self.model.current.rect
        self.canvas.create_rectangle(
            cur.left,
            cur.top,
            cur.right,
            cur.bottom,
            outline=COL_AQUA,
            width=2,
            tags=("markers",),
        )

        # 目标：双层虚线 + 标签
        target = level.target.rect
        self._target_outline_ids.append(
            self.canvas.create_rectangle(
                target.left - 3,
                target.top - 3,
                target.right + 3,
                target.bottom + 3,
                outline=COL_AMBER,
                width=3,
                dash=(8, 5),
                tags=("markers", "target_outline"),
            )
        )
        self._target_outline_ids.append(
            self.canvas.create_rectangle(
                target.left + 2,
                target.top + 2,
                target.right - 2,
                target.bottom - 2,
                outline=COL_AMBER_SOFT,
                width=1,
                dash=(3, 7),
                tags=("markers", "target_outline"),
            )
        )
        label_text = f"目标 · {platform_display_name(level.target)}"
        label_w = min(360, max(150, len(label_text) * 13 + 28))
        lx = clamp(target.center_x, 12 + label_w / 2, self.screen.right - 12 - label_w / 2)
        ly = target.top - 26 if target.top > 80 else target.bottom + 26
        self._pill(
            lx - label_w / 2,
            ly - 15,
            lx + label_w / 2,
            ly + 15,
            COL_PANEL,
            COL_AMBER,
            9,
            tags=("markers",),
        )
        self.canvas.create_text(
            lx,
            ly,
            text=truncate_text(label_text, 30),
            fill=COL_AMBER_SOFT,
            font=FONT_SMALL_BOLD,
            tags=("markers",),
        )

    def _draw_hud(self) -> None:
        if self.model is None:
            return
        w = self.screen.width
        # 品牌
        self._pill(20, 18, 338, 82, COL_PANEL, COL_LINE, 12)
        self.canvas.create_text(
            38,
            37,
            text=APP_NAME,
            anchor="w",
            fill=COL_AQUA,
            font=("Consolas", -19, "bold"),
        )
        title = self._window_label()
        self.canvas.create_text(
            38,
            61,
            text=truncate_text(title, 38),
            anchor="w",
            fill=COL_MUTED,
            font=FONT_SMALL,
        )

        # 目标
        target_x1 = max(360, w / 2 - 250)
        target_x2 = min(w - 340, w / 2 + 250)
        if target_x2 - target_x1 < 280:
            target_x1 = w / 2 - 200
            target_x2 = w / 2 + 200
        self._pill(target_x1, 18, target_x2, 82, COL_PANEL, COL_AMBER, 12)
        self.canvas.create_text(
            (target_x1 + target_x2) / 2,
            36,
            text="当前目标",
            fill=COL_AMBER,
            font=FONT_SMALL,
        )
        self._hud_target_id = self.canvas.create_text(
            (target_x1 + target_x2) / 2,
            60,
            text="",
            fill=COL_TEXT,
            font=FONT_BODY_BOLD,
        )

        # 统计
        stat_x1 = w - 320
        self._pill(stat_x1, 18, w - 20, 82, COL_PANEL, COL_LINE, 12)
        self._hud_stats_id = self.canvas.create_text(
            stat_x1 + 16,
            38,
            anchor="w",
            text="",
            fill=COL_TEXT,
            font=FONT_SMALL_BOLD,
        )
        self.canvas.create_text(
            stat_x1 + 16,
            61,
            anchor="w",
            text="R 新关卡 · Esc 退出",
            fill=COL_MUTED,
            font=FONT_SMALL,
        )
        self._refresh_hud()

    def _window_label(self) -> str:
        if self._last_scan is not None:
            if self.scope == "desktop":
                return "范围：整个桌面"
            scope = "窗口"
            return f"{scope}：{self.window_title}"
        if self.scope == "desktop":
            return "范围：整个桌面"
        return f"窗口：{self.window_title}"

    def _refresh_hud(self) -> None:
        if self.model is None or self.mode not in ("playing",):
            return
        if self._hud_target_id is not None:
            self.canvas.itemconfigure(
                self._hud_target_id,
                text=platform_display_name(self.model.level.target),
            )
        if self._hud_stats_id is not None:
            stats = f"跳跃 {self.model.jumps}  ·  坠落 {self.model.falls}"
            if self.model.limit_retries:
                stats += f"  ·  剩余 {self.model.remaining_retries}"
            self.canvas.itemconfigure(self._hud_stats_id, text=stats)

    def _draw_character(self) -> None:
        if self.model is None:
            return
        x = self.model.char_x
        feet = self.model.char_feet
        if feet < -60 or feet > self.screen.bottom + 60:
            return
        if self._sprites:
            sprite = self._sprites.get(self.model.direction, self._sprites.get(1))
            if sprite is not None:
                self.canvas.create_image(
                    x,
                    feet + 1,
                    image=sprite,
                    anchor="s",
                    tags=("dynamic", "actor"),
                )
                return

        # Pillow 不可用时的几何团子
        r = CHAR_SIZE / 2
        self.canvas.create_oval(
            x - r,
            feet - CHAR_SIZE,
            x + r,
            feet,
            fill=COL_AQUA,
            outline=COL_INK,
            width=2,
            tags=("dynamic", "actor"),
        )
        eye_dir = 1.4 * self.model.direction
        for ex in (x - 5, x + 5):
            self.canvas.create_oval(
                ex - 1.8 + eye_dir,
                feet - 19,
                ex + 1.8 + eye_dir,
                feet - 14,
                fill=COL_INK,
                outline="",
                tags=("dynamic", "actor"),
            )
        self.canvas.create_oval(
            x - 9,
            feet - 24,
            x - 4,
            feet - 19,
            fill=COL_TEXT,
            outline="",
            tags=("dynamic", "actor"),
        )

    def _draw_direction(self) -> None:
        if self.model is None:
            return
        x = self.model.char_x
        feet = self.model.char_feet
        y = feet - CHAR_SIZE - 10
        for direction in (-1, 1):
            active = self.model.direction == direction
            tip_x = x + direction * 44
            tip_x = clamp(tip_x, 14, self.screen.right - 14)
            color = COL_AQUA if active else COL_AQUA_DIM
            if direction < 0:
                points = (tip_x, y, tip_x + 12, y - 8, tip_x + 12, y + 8)
            else:
                points = (tip_x, y, tip_x - 12, y - 8, tip_x - 12, y + 8)
            self.canvas.create_polygon(
                points,
                fill=color if active else COL_PANEL,
                outline=color,
                width=2 if active else 1,
                tags=("dynamic",),
            )
        if self.model.state == STATE_CHARGING:
            self.canvas.create_text(
                x + self.model.direction * 72,
                y,
                text="蓄力",
                fill=COL_AQUA_SOFT,
                font=FONT_SMALL_BOLD,
                tags=("dynamic",),
            )

    def _draw_trajectory(self) -> None:
        if self.model is None or self.model.state != STATE_CHARGING:
            return
        points = trajectory_points(
            self.model.char_x,
            self.model.char_feet,
            self.model.power,
            self.model.direction,
        )
        for index, (px, py) in enumerate(points):
            if not (0 <= px <= self.screen.right and 0 <= py <= self.screen.bottom):
                continue
            radius = max(1.0, 3.2 * (1.0 - index / len(points)))
            self.canvas.create_oval(
                px - radius,
                py - radius,
                px + radius,
                py + radius,
                fill=COL_AQUA_DIM if index % 2 else COL_AQUA_SOFT,
                outline="",
                tags=("dynamic",),
            )

    def _draw_charge_ui(self) -> None:
        if self.model is None:
            return
        w = self.screen.width
        h = self.screen.height
        charging = self.model.state == STATE_CHARGING
        panel_w = 500
        x1 = w / 2 - panel_w / 2
        y1 = h - 108
        x2 = x1 + panel_w
        y2 = h - 38
        outline = COL_AMBER if charging and self.model.power >= 0.999 else COL_LINE
        self._pill(x1, y1, x2, y2, COL_PANEL, outline, 14, tags=("dynamic",))

        if charging:
            percent = int(round(self.model.power * 100))
            distance = int(round(jump_distance(self.model.power)))
            label = f"蓄力 {percent:>3d}%   ·   预计 {distance:>4d} px"
            self.canvas.create_text(
                x1 + 18,
                y1 + 18,
                text=label,
                anchor="w",
                fill=COL_AMBER_SOFT if percent >= 100 else COL_TEXT,
                font=FONT_SMALL_BOLD,
                tags=("dynamic",),
            )
            bar_x1 = x1 + 18
            bar_x2 = x2 - 18
            bar_y1 = y1 + 36
            bar_y2 = y1 + 52
            self._rounded(
                bar_x1,
                bar_y1,
                bar_x2,
                bar_y2,
                5,
                COL_INK,
                COL_LINE,
                1,
                tags=("dynamic",),
            )
            fill_w = (bar_x2 - bar_x1) * self.model.power
            if fill_w > 1:
                color = COL_AMBER if self.model.power >= 0.999 else COL_AQUA
                self._rounded(
                    bar_x1,
                    bar_y1,
                    bar_x1 + fill_w,
                    bar_y2,
                    5,
                    color,
                    color,
                    0,
                    tags=("dynamic",),
                )
            for tick in (0.25, 0.5, 0.75):
                tx = bar_x1 + (bar_x2 - bar_x1) * tick
                self.canvas.create_line(
                    tx,
                    bar_y1 + 2,
                    tx,
                    bar_y2 - 2,
                    fill=COL_PANEL,
                    width=1,
                    tags=("dynamic",),
                )
        else:
            text = "←  →  切换方向    ·    长按空格蓄力    ·    松开起跳"
            self.canvas.create_text(
                w / 2,
                (y1 + y2) / 2,
                text=text,
                fill=COL_TEXT,
                font=FONT_BODY,
                tags=("dynamic",),
            )

    def _draw_particles(self) -> None:
        for particle in self._particles:
            ratio = max(0.0, particle.life / particle.max_life)
            radius = max(0.7, particle.size * ratio)
            self.canvas.create_oval(
                particle.x - radius,
                particle.y - radius,
                particle.x + radius,
                particle.y + radius,
                fill=particle.color,
                outline="",
                tags=("dynamic",),
            )

    def _draw_focus_banner(self) -> None:
        w = self.screen.width
        h = self.screen.height
        x1, y1, x2, y2 = w / 2 - 250, h / 2 - 74, w / 2 + 250, h / 2 + 74
        self._pill(x1, y1, x2, y2, COL_PANEL, COL_RED, 16, tags=("dynamic",))
        self.canvas.create_text(
            w / 2,
            y1 + 38,
            text="已失去键盘焦点",
            fill=COL_RED,
            font=FONT_H1,
            tags=("dynamic",),
        )
        self.canvas.create_text(
            w / 2,
            y1 + 76,
            text="点击游戏区域重新聚焦 · Esc 退出",
            fill=COL_TEXT,
            font=FONT_BODY,
            tags=("dynamic",),
        )
        self.canvas.create_text(
            w / 2,
            y1 + 105,
            text="游戏已暂停",
            fill=COL_MUTED,
            font=FONT_SMALL,
            tags=("dynamic",),
        )

    def _draw_toast(self, now: float) -> None:
        if not self._toast_text or now > self._toast_until:
            return
        w = self.screen.width
        text = truncate_text(self._toast_text, 50)
        width = min(640, max(260, len(text) * 15 + 48))
        x = w / 2
        y = 112
        self._pill(x - width / 2, y - 20, x + width / 2, y + 20, COL_PANEL_2, COL_AQUA_DIM, 12,
                   tags=("dynamic",))
        self.canvas.create_text(
            x,
            y,
            text=text,
            fill=COL_AQUA_SOFT,
            font=FONT_SMALL_BOLD,
            tags=("dynamic",),
        )

    def _draw_dynamic(self, now: float) -> None:
        self.canvas.delete("dynamic")
        if self.model is None or self.mode != "playing":
            return

        self._draw_trajectory()
        self._draw_particles()
        self._draw_direction()
        self._draw_character()
        self._draw_charge_ui()
        self._draw_toast(now)
        if self.focus_lost:
            self._draw_focus_banner()

    def _pulse_target(self, now: float) -> None:
        if not self._target_outline_ids:
            return
        phase = int(now * 2.5) % 2
        color = COL_AMBER if phase == 0 else COL_AMBER_SOFT
        try:
            for item in self._target_outline_ids:
                self.canvas.itemconfigure(item, outline=color)
        except Exception:
            pass

    # =========================================================================
    # 卡片界面
    # =========================================================================

    def _draw_scanning_card(self) -> None:
        self.mode = "scanning"
        self.canvas.delete("all")
        self._card_buttons = []
        self._hover_button = None
        self.canvas.configure(cursor="arrow")
        self._draw_card_shell(COL_AQUA_DIM)
        w, h = self.screen.width, self.screen.height
        self.canvas.create_text(
            w / 2,
            h / 2 - 62,
            text="正在读取控件",
            fill=COL_AQUA,
            font=FONT_H1,
            tags=("card",),
        )
        self.canvas.create_text(
            w / 2,
            h / 2 - 20,
            text=truncate_text(self._scan_reason, 42),
            fill=COL_TEXT,
            font=FONT_BODY,
            tags=("card",),
        )
        self.canvas.create_text(
            w / 2,
            h / 2 + 34,
            text=f"范围：{'整个桌面' if self.scope == 'desktop' else self.window_title}",
            fill=COL_MUTED,
            font=FONT_SMALL,
            tags=("card",),
        )
        self.canvas.create_text(
            w / 2,
            h / 2 + 74,
            text="UI Automation 遍历可能需要 1–3 秒 · Esc 退出",
            fill=COL_MUTED,
            font=FONT_SMALL,
            tags=("card",),
        )
        # 扫描点装饰
        for i in range(3):
            self.canvas.create_oval(
                w / 2 - 18 + i * 18,
                h / 2 + 104,
                w / 2 - 12 + i * 18,
                h / 2 + 110,
                fill=COL_AQUA if i == 0 else COL_AQUA_DIM,
                outline="",
                tags=("card",),
            )

    def _draw_error_card(self, message: str) -> None:
        self.mode = "error"
        self.canvas.delete("all")
        self._card_buttons = []
        self.canvas.configure(cursor="arrow")
        self._draw_card_shell(COL_RED)
        w, h = self.screen.width, self.screen.height
        self.canvas.create_text(
            w / 2,
            h / 2 - 92,
            text="无法读取当前窗口控件",
            fill=COL_RED,
            font=FONT_H1,
            tags=("card",),
        )
        self.canvas.create_text(
            w / 2,
            h / 2 - 42,
            text=truncate_text(message, 86),
            fill=COL_TEXT,
            font=FONT_BODY,
            width=660,
            justify="center",
            tags=("card",),
        )
        self.canvas.create_text(
            w / 2,
            h / 2 + 20,
            text="提示：目标窗口不要最小化；管理员窗口需以管理员身份运行本程序。",
            fill=COL_MUTED,
            font=FONT_SMALL,
            tags=("card",),
        )
        self._draw_card_buttons(
            [
                ("retry", "R  重试"),
                ("desktop", "D  扫描整个桌面"),
                ("quit", "Esc  退出"),
            ],
            y=h / 2 + 84,
        )

    def _draw_victory_card(self) -> None:
        self.mode = "victory"
        self.canvas.delete("all")
        self._card_buttons = []
        self._hover_button = None
        self.canvas.configure(cursor="arrow")
        self._draw_card_shell(COL_GREEN)
        w, h = self.screen.width, self.screen.height
        assert self.model is not None
        # 简单彩屑
        rng = random.Random(7)
        for _ in range(28):
            x = rng.uniform(w / 2 - 270, w / 2 + 270)
            y = rng.uniform(h / 2 - 150, h / 2 + 130)
            color = rng.choice((COL_AQUA, COL_AMBER, COL_GREEN, COL_TEXT))
            size = rng.uniform(2, 5)
            self.canvas.create_oval(
                x,
                y,
                x + size,
                y + size,
                fill=color,
                outline="",
                tags=("card",),
            )
        self.canvas.create_text(
            w / 2,
            h / 2 - 110,
            text="落点命中！",
            fill=COL_GREEN,
            font=("Microsoft YaHei UI", -42, "bold"),
            tags=("card",),
        )
        self.canvas.create_text(
            w / 2,
            h / 2 - 55,
            text=f"你落到了目标控件 · {platform_display_name(self.model.level.target)}",
            fill=COL_TEXT,
            font=FONT_BODY_BOLD,
            tags=("card",),
        )
        stats = (
            f"跳跃 {self.model.jumps} 次   ·   坠落 {self.model.falls} 次"
            f"   ·   剩余机会 {self.model.remaining_retries if self.model.limit_retries else '无限'}"
        )
        self.canvas.create_text(
            w / 2,
            h / 2 - 12,
            text=stats,
            fill=COL_MUTED,
            font=FONT_SMALL,
            tags=("card",),
        )
        self._draw_card_buttons(
            [("restart", "再玩一局"), ("quit", "退出")],
            y=h / 2 + 70,
        )
        self.canvas.create_text(
            w / 2,
            h / 2 + 126,
            text="键盘焦点已释放；点击按钮继续，点击卡片后也可用 R / Esc。",
            fill=COL_MUTED,
            font=FONT_SMALL,
            tags=("card",),
        )

    def _draw_game_over_card(self) -> None:
        self.mode = "game_over"
        self.canvas.delete("all")
        self._card_buttons = []
        self._hover_button = None
        self.canvas.configure(cursor="arrow")
        self._draw_card_shell(COL_AMBER)
        w, h = self.screen.width, self.screen.height
        assert self.model is not None
        self.canvas.create_text(
            w / 2,
            h / 2 - 88,
            text="机会用尽",
            fill=COL_AMBER,
            font=("Microsoft YaHei UI", -42, "bold"),
            tags=("card",),
        )
        self.canvas.create_text(
            w / 2,
            h / 2 - 30,
            text=f"本局跳跃 {self.model.jumps} 次，坠落 {self.model.falls} 次。",
            fill=COL_TEXT,
            font=FONT_BODY,
            tags=("card",),
        )
        self._draw_card_buttons(
            [("restart", "再玩一局"), ("quit", "退出")],
            y=h / 2 + 60,
        )
        self.canvas.create_text(
            w / 2,
            h / 2 + 116,
            text="键盘焦点已释放；点击按钮继续，点击卡片后也可用 R / Esc。",
            fill=COL_MUTED,
            font=FONT_SMALL,
            tags=("card",),
        )

    def _draw_card_shell(self, accent: str) -> None:
        w, h = self.screen.width, self.screen.height
        cw = min(760, w - 96)
        ch = min(380, h - 120)
        x1 = w / 2 - cw / 2
        y1 = h / 2 - ch / 2
        x2 = w / 2 + cw / 2
        y2 = h / 2 + ch / 2
        # 外阴影 + 卡片
        self._rounded(x1 + 10, y1 + 12, x2 + 10, y2 + 12, 22, "#000000", "", 0, tags=("card",))
        self._rounded(x1, y1, x2, y2, 22, COL_PANEL, accent, 2, tags=("card",))
        self.canvas.create_line(
            x1 + 24,
            y1 + 2,
            x2 - 24,
            y1 + 2,
            fill=accent,
            width=3,
            tags=("card",),
        )
        self.canvas.create_text(
            x1 + 26,
            y1 + 22,
            text=APP_NAME,
            anchor="w",
            fill=COL_AQUA,
            font=("Consolas", -12, "bold"),
            tags=("card",),
        )
        self.canvas.create_text(
            x2 - 26,
            y1 + 22,
            text="桌面即关卡 · 控件即平台",
            anchor="e",
            fill=COL_MUTED,
            font=FONT_SMALL,
            tags=("card",),
        )

    def _draw_card_buttons(
        self, buttons: Sequence[tuple[str, str]], *, y: float
    ) -> None:
        w = self.screen.width
        count = max(1, len(buttons))
        gap = 12
        button_w = min(210, (min(760, w - 96) - 80 - gap * (count - 1)) / count)
        total = button_w * count + gap * (count - 1)
        start_x = w / 2 - total / 2
        for index, (action, label) in enumerate(buttons):
            x1 = start_x + index * (button_w + gap)
            x2 = x1 + button_w
            rect = Rect(int(x1), int(y - 24), int(x2), int(y + 24))
            fill = COL_AQUA_DIM if action != "quit" else COL_PANEL_2
            outline = COL_AQUA if action != "quit" else COL_LINE
            if action == "desktop":
                fill = COL_PANEL_2
                outline = COL_AMBER
            self._rounded(x1, rect.top, x2, rect.bottom, 12, fill, outline, 1, tags=("card",))
            self.canvas.create_text(
                (x1 + x2) / 2,
                y,
                text=label,
                fill=COL_TEXT,
                font=FONT_SMALL_BOLD,
                tags=("card",),
            )
            self._card_buttons.append({"action": action, "rect": rect})

    # =========================================================================
    # 错误入口
    # =========================================================================

    def _show_error_card(self, message: str) -> None:
        if not self.running:
            return
        self.scanning = False
        self._space_down = False
        if self.model is not None:
            self.model.cancel_charge()
        if self._monitor_job:
            try:
                self.root.after_cancel(self._monitor_job)
            except Exception:
                pass
            self._monitor_job = None
        self.canvas.delete("dynamic")
        self._draw_error_card(message)
        self._grab_focus()


# =============================================================================
# CLI 与入口
# =============================================================================


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dango Jump：在真实 Windows 桌面控件之间蓄力跳跃。"
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="直接扫描整个桌面，而不是启动时的前台窗口。",
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
        help=f"启用有限重试（默认 {LIMIT_RETRIES}）。",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help=f"有限重试时的机会数（默认 {MAX_RETRIES}）。",
    )
    return parser.parse_args(argv)


def _countdown(seconds: float) -> None:
    if seconds <= 0:
        return
    print("Dango Jump：请切换到要游玩的窗口。", flush=True)
    whole = max(0, int(math.ceil(seconds)))
    for remaining in range(whole, 0, -1):
        print(f"  {remaining}…", flush=True)
        time.sleep(1.0)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if sys.platform != "win32":
        print("Dango Jump 仅支持 Windows 10/11（透明覆盖层与 UI Automation）。")
        return 2

    try:
        enable_dpi_awareness()
        screen = primary_screen_rect()

        if args.desktop:
            # 仍记下当前前台窗口，胜利/退出时用于归还键盘焦点。
            hwnd, _ = get_foreground_window_info()
            window_title = "整个桌面"
            scope = "desktop"
        else:
            _countdown(max(0.0, min(30.0, args.delay)))
            hwnd, window_title = get_foreground_window_info()
            if not hwnd:
                print("无法获取前台窗口，请重试或使用 --desktop。")
                return 1
            window_title = window_title or "未命名窗口"
            scope = "window"
            print(f"目标窗口：{window_title}", flush=True)

        try:
            import tkinter  # noqa: F401  确认 Tk 可用
        except Exception as exc:
            print(f"Tkinter 不可用：{exc}")
            return 1

        app = OverlayApp(
            screen,
            scope,
            hwnd,
            window_title,
            limit_retries=bool(args.limit_retries),
            max_retries=max(1, int(args.max_retries)),
        )
        app.run()
        return 0
    except KeyboardInterrupt:
        print("\n已退出。")
        return 0
    except Exception as exc:
        traceback.print_exc()
        print(f"启动失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
