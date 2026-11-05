#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dango_core — Dango Jump 的跨平台纯逻辑核心。

这里不导入 Tk、Qt、UI Automation 或 PyObjC；Windows 与 macOS 前端
共享关卡生成、控件过滤、跳跃物理和状态机。
"""

from __future__ import annotations

import math
import os
import random
import time
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


# macOS Accessibility 角色（使用 AXRole 字面量，比 PyObjC 常量更稳定）。
MAC_CANDIDATE_CONTROL_TYPES = {
    "AXButton",
    "AXCheckBox",
    "AXRadioButton",
    "AXPopUpButton",
    "AXComboBox",
    "AXTextField",
    "AXTextArea",
    "AXLink",
    "AXRow",
    "AXTabGroup",
    "AXMenuItem",
    "AXSlider",
    "AXStepper",
    "AXDisclosureTriangle",
    "AXCell",
    "AXIncrementor",
    "AXColorWell",
}

CONTROL_TYPE_LABELS.update({
    "AXButton": "按钮",
    "AXCheckBox": "复选框",
    "AXRadioButton": "单选框",
    "AXPopUpButton": "下拉按钮",
    "AXComboBox": "下拉框",
    "AXTextField": "输入框",
    "AXTextArea": "文本区",
    "AXLink": "链接",
    "AXRow": "列表项",
    "AXTabGroup": "标签页",
    "AXMenuItem": "菜单项",
    "AXSlider": "滑块",
    "AXStepper": "步进器",
    "AXDisclosureTriangle": "展开按钮",
    "AXCell": "单元格",
    "AXIncrementor": "微调框",
    "AXColorWell": "颜色选择器",
})

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
    allowed_types: Optional[Iterable[str]] = None,
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
    allowed = set(CANDIDATE_CONTROL_TYPES if allowed_types is None else allowed_types)

    candidates: list[tuple[RawControl, Rect]] = []
    for raw in raw_controls:
        if raw.control_type not in allowed:
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


def startup_countdown(seconds: float, message: str = "Dango Jump：请切换到要游玩的窗口。") -> None:
    """终端倒计时；给用户时间切换到目标窗口。"""
    if seconds <= 0:
        return
    print(message, flush=True)
    whole = max(0, int(math.ceil(seconds)))
    for remaining in range(whole, 0, -1):
        print(f"  {remaining}…", flush=True)
        time.sleep(1.0)
