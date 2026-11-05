#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dango_core — Dango Circuit 的跨平台纯逻辑核心。

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

# 重试（旧配置，保留给 CLI 兼容层） ---------------------------------------------
LIMIT_RETRIES = False
MAX_RETRIES = 5

# 巡回赛 -----------------------------------------------------------------------
START_HEARTS = 3                    # 默认心数；坠落扣一颗
COURSE_CHECKPOINTS = 4              # 默认检查点数（含终点）
BONUS_STAR_COUNT = 2                # 每局可选星星数量
MIN_CHECKPOINTS = 2                 # 至少两段，避免退化成单次胜利
MAX_CHECKPOINTS = 7
CHECKPOINT_MAX_WIDTH = 360          # 检查点优先选择可读的中小控件
CHECKPOINT_MAX_HEIGHT = 180
PERFECT_CENTER_RATIO = 0.30         # 落点在目标中央 30% 内算 Perfect
CHECKPOINT_BASE_SCORE = 120
CHECKPOINT_SCORE_STEP = 80
PERFECT_BONUS = 60
DIRECT_BONUS = 80
STAR_BONUS = 150
COMBO_MULTIPLIER_STEP = 0.20
MAX_COMBO_MULTIPLIER = 1.80
FINISH_HEART_BONUS = 300
FINISH_TIME_BONUS_BASE = 2000
FINISH_TIME_BONUS_RATE = 30
COURSE_GENERATION_ATTEMPTS = 1600

# 窗口与视觉 -------------------------------------------------------------------
APP_NAME = "DANGO CIRCUIT"
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
COL_STAR = "#FCD34D"
COL_HEART = "#FB7185"
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
class Course:
    """一条从起点出发、若干检查点组成的路线。"""

    start: Platform
    checkpoints: list[Platform]
    bonus_stars: list[Platform]
    platforms: list[Platform]
    shortened: bool = False

    @property
    def goal(self) -> Optional[Platform]:
        return self.checkpoints[-1] if self.checkpoints else None


@dataclass(frozen=True)
class RunConfig:
    hearts: int = START_HEARTS
    checkpoint_count: int = COURSE_CHECKPOINTS
    star_count: int = BONUS_STAR_COUNT
    seed: Optional[int] = None
    relaxed: bool = False


@dataclass
class ScanResult:
    platforms: list[Platform]
    nodes_seen: int
    skipped: int
    elapsed: float


@dataclass
class UpdateEvents:
    """物理/落点更新向外层汇报的事件。"""

    landed: Optional[Platform] = None
    checkpoint: Optional[Platform] = None
    star: Optional[Platform] = None
    perfect: bool = False
    direct: bool = False
    score_delta: int = 0
    combo: int = 0
    checkpoint_index: int = 0
    hearts_left: Optional[int] = None
    victory: bool = False
    fell: bool = False
    game_over: bool = False


@dataclass(frozen=True)
class RunSnapshot:
    score: int
    combo: int
    combo_multiplier: float
    hearts: Optional[int]
    checkpoint_index: int
    checkpoint_count: int
    elapsed: float
    perfect_count: int
    direct_count: int
    stars_collected: int
    stars_total: int
    jumps: int
    falls: int
    state: str


@dataclass(frozen=True)
class RunResult:
    completed: bool
    score: int
    elapsed: float
    hearts_left: Optional[int]
    falls: int
    perfect_count: int
    direct_count: int
    stars_collected: int
    stars_total: int
    rating: int


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


def _checkpoint_pool(platforms: Sequence[Platform], strict: bool = True) -> list[Platform]:
    """优先选中小控件作为检查点；数量不足时退回全部控件。"""
    preferred = [
        p
        for p in platforms
        if p.rect.width <= CHECKPOINT_MAX_WIDTH
        and p.rect.height <= CHECKPOINT_MAX_HEIGHT
    ]
    if strict and len(preferred) >= 3:
        return preferred
    return list(platforms)


def _step_distance_band(step: int) -> tuple[float, float]:
    low = 110.0 + 80.0 * step
    high = 520.0 + 120.0 * step
    return low, min(high, JUMP_DISTANCE_MAX)


def _extend_chain(
    path: list[Platform],
    pool: Sequence[Platform],
    target_length: int,
    used: set[int],
    rng: random.Random,
    attempts: list[int],
) -> bool:
    if len(path) >= target_length:
        return True
    attempts[0] += 1
    if attempts[0] > COURSE_GENERATION_ATTEMPTS:
        return False
    current = path[-1]
    step = len(path)
    low, high = _step_distance_band(step)
    reachable: list[tuple[Platform, float]] = []
    for candidate in pool:
        if candidate.uid in used:
            continue
        if not can_directly_reach(current, candidate):
            continue
        dx = abs(current.center_x - candidate.center_x)
        reachable.append((candidate, dx))
    if not reachable:
        return False

    in_band = [(p, dx) for p, dx in reachable if low <= dx <= high]
    options = in_band if in_band else reachable
    rng.shuffle(options)
    # 限制分支数，避免在大 UI 树上爆炸；随机顺序仍保证路线多样。
    for candidate, _dx in options[:10]:
        path.append(candidate)
        used.add(candidate.uid)
        if _extend_chain(path, pool, target_length, used, rng, attempts):
            return True
        used.remove(candidate.uid)
        path.pop()
    return False


def _build_chain(
    platforms: Sequence[Platform],
    pool: Sequence[Platform],
    length: int,
    rng: random.Random,
    *,
    start: Optional[Platform] = None,
    excluded_uids: Optional[set[int]] = None,
) -> Optional[list[Platform]]:
    """生成包含起点在内、长度为 length 的可达路线。"""
    if length < 2:
        return None
    excluded = set(excluded_uids or ())
    if start is not None:
        starts = [start]
    else:
        starts = [p for p in platforms if p.uid not in excluded]
    rng.shuffle(starts)
    for candidate_start in starts[:40]:
        if len(pool) < length:
            continue
        path = [candidate_start]
        used = {candidate_start.uid} | excluded
        attempts = [0]
        if _extend_chain(path, pool, length, used, rng, attempts):
            return path
    return None


def _pick_bonus_stars(
    platforms: Sequence[Platform],
    route: Sequence[Platform],
    count: int,
    rng: random.Random,
) -> list[Platform]:
    if count <= 0:
        return []
    used = {p.uid for p in route}
    candidates = [p for p in platforms if p.uid not in used]
    if not candidates:
        return []
    # 优先选能被路线中某个检查点抓到的星星，减少“完全够不到”的情况。
    reachable = [
        p
        for p in candidates
        if any(can_directly_reach(checkpoint, p) for checkpoint in route)
    ]
    pool = reachable if len(reachable) >= count else candidates
    rng.shuffle(pool)
    # 去重 UID（路线所有平台本来就不重复）
    chosen: list[Platform] = []
    seen: set[int] = set()
    for platform in pool:
        if platform.uid in seen:
            continue
        seen.add(platform.uid)
        chosen.append(platform)
        if len(chosen) >= count:
            break
    return chosen


def pick_bonus_stars(
    platforms: Sequence[Platform],
    route: Sequence[Platform],
    count: int,
    rng: random.Random,
) -> list[Platform]:
    """供桌面布局变化后的路线修复使用。"""
    return _pick_bonus_stars(platforms, route, count, rng)


def generate_course(
    platforms: Sequence[Platform],
    config: Optional[RunConfig] = None,
    rng: Optional[random.Random] = None,
) -> Course:
    """生成一条保证逐段基本可达的巡回路线。"""
    config = config or RunConfig()
    rng = rng or random.Random(config.seed)
    if len(platforms) < 3:
        raise LevelError("至少需要 3 个可用控件才能生成巡回赛。")

    requested = max(MIN_CHECKPOINTS, min(MAX_CHECKPOINTS, int(config.checkpoint_count)))
    # 优先使用中小控件；不够时放宽到全部平台
    pools = []
    preferred = _checkpoint_pool(platforms, strict=True)
    if preferred:
        pools.append(preferred)
    if len(preferred) != len(platforms):
        pools.append(list(platforms))

    # 先尝试最长路线，再逐步缩短；同一长度下优先使用可控的中小控件。
    for checkpoint_count in range(requested, MIN_CHECKPOINTS - 1, -1):
        for pool in pools:
            chain = _build_chain(platforms, pool, checkpoint_count + 1, rng)
            if chain is None:
                continue
            start = chain[0]
            checkpoints = chain[1:]
            stars = _pick_bonus_stars(
                platforms,
                chain,
                max(0, min(4, int(config.star_count))),
                rng,
            )
            return Course(
                start=start,
                checkpoints=checkpoints,
                bonus_stars=stars,
                platforms=list(platforms),
                shortened=checkpoint_count < requested,
            )

    raise LevelError(
        "当前控件之间无法生成包含至少 2 个检查点的可达路线。"
        "请换一个控件更多的窗口，或按 D 扫描整个桌面。"
    )


def generate_remaining_checkpoints(
    platforms: Sequence[Platform],
    start: Platform,
    count: int,
    rng: random.Random,
    *,
    excluded_uids: Optional[set[int]] = None,
    strict_pool: bool = True,
) -> list[Platform]:
    """从当前平台生成剩余检查点，用于桌面布局变化时保留进度。"""
    count = max(1, int(count))
    pool = _checkpoint_pool(platforms, strict=strict_pool)
    chain = _build_chain(
        platforms,
        pool,
        count + 1,
        rng,
        start=start,
        excluded_uids=excluded_uids,
    )
    if chain is None and strict_pool:
        return generate_remaining_checkpoints(
            platforms,
            start,
            count,
            rng,
            excluded_uids=excluded_uids,
            strict_pool=False,
        )
    if chain is None:
        raise LevelError("桌面布局变化后无法从当前位置生成剩余路线。")
    return chain[1:]

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
    """与 Tk/Windows/macOS 无关的巡回赛逻辑，便于单元测试。"""

    def __init__(
        self,
        course: Course,
        screen: Rect,
        config: Optional[RunConfig] = None,
    ) -> None:
        self.course = course
        self.screen = screen
        self.config = config or RunConfig()
        self.state = STATE_READY
        self.direction = 1
        self.power = 0.0
        self.charge_started = 0.0
        self.char_x = 0.0
        self.char_feet = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.current = course.start
        self.respawn_platform = course.start
        self.jumps = 0
        self.falls = 0
        self.flight_time = 0.0
        self.missed = False
        self.checkpoint_index = 0
        self.score = 0
        self.combo = 0
        self.perfect_count = 0
        self.direct_count = 0
        self.hearts = max(1, int(self.config.hearts))
        self.relaxed = bool(self.config.relaxed)
        self.collected_star_uids: set[int] = set()
        self.elapsed = 0.0
        self.timer_started = False
        self.intermediate_since_checkpoint = False
        self._result: Optional[RunResult] = None
        self._reset_position()

    # -- 查询 -----------------------------------------------------------------

    @property
    def hearts_left(self) -> Optional[int]:
        if self.relaxed:
            return None
        return max(0, self.hearts)

    @property
    def next_target(self) -> Optional[Platform]:
        if 0 <= self.checkpoint_index < len(self.course.checkpoints):
            return self.course.checkpoints[self.checkpoint_index]
        return None

    @property
    def goal(self) -> Optional[Platform]:
        return self.course.goal

    @property
    def combo_multiplier(self) -> float:
        if self.combo <= 0:
            return 1.0
        return min(
            MAX_COMBO_MULTIPLIER,
            1.0 + COMBO_MULTIPLIER_STEP * (self.combo - 1),
        )

    @property
    def rating(self) -> int:
        if self._result is not None:
            return self._result.rating
        return 0

    @property
    def result(self) -> Optional[RunResult]:
        return self._result

    @property
    def stars_total(self) -> int:
        return len(self.course.bonus_stars)

    @property
    def stars_collected(self) -> int:
        return len(self.collected_star_uids)

    @property
    def is_charging(self) -> bool:
        return self.state == STATE_CHARGING

    def snapshot(self) -> RunSnapshot:
        return RunSnapshot(
            score=self.score,
            combo=self.combo,
            combo_multiplier=self.combo_multiplier,
            hearts=self.hearts_left,
            checkpoint_index=self.checkpoint_index,
            checkpoint_count=len(self.course.checkpoints),
            elapsed=self.elapsed,
            perfect_count=self.perfect_count,
            direct_count=self.direct_count,
            stars_collected=self.stars_collected,
            stars_total=self.stars_total,
            jumps=self.jumps,
            falls=self.falls,
            state=self.state,
        )

    # -- 关卡/位置 -------------------------------------------------------------

    def _default_direction(self) -> int:
        target = self.next_target or self.course.goal or self.course.start
        return 1 if target.center_x >= self.current.center_x else -1

    def _reset_position(self) -> None:
        self.current = self.respawn_platform
        self.char_x = self.respawn_platform.rect.center_x
        self.char_feet = float(self.respawn_platform.rect.top)
        self.vx = 0.0
        self.vy = 0.0
        self.power = 0.0
        self.flight_time = 0.0
        self.missed = False
        self.intermediate_since_checkpoint = False
        self.direction = self._default_direction()
        self.state = STATE_READY

    def load_course(self, course: Course) -> None:
        """为同一 GameModel 装入新的一局，重置所有成绩。"""
        self.course = course
        self.current = course.start
        self.respawn_platform = course.start
        self.checkpoint_index = 0
        self.score = 0
        self.combo = 0
        self.perfect_count = 0
        self.direct_count = 0
        self.hearts = max(1, int(self.config.hearts))
        self.collected_star_uids.clear()
        self.elapsed = 0.0
        self.timer_started = False
        self.jumps = 0
        self.falls = 0
        self.flight_time = 0.0
        self.missed = False
        self._result = None
        self._reset_position()

    def replace_pending_checkpoints(
        self,
        platforms: Sequence[Platform],
        checkpoints: Sequence[Platform],
        bonus_stars: Sequence[Platform],
        *,
        current_platform: Optional[Platform] = None,
        respawn_platform: Optional[Platform] = None,
    ) -> None:
        """桌面变化时替换未访问路线，保留已取得成绩和心数。

        已完成检查点保留在 prefix 中；若 UI 找到了当前/复活平台的新元素，
        可以一起替换，避免继续引用已失效的旧 UIA/AX 元素。
        """
        if not checkpoints:
            raise LevelError("桌面布局变化后没有可用的剩余检查点。")
        if current_platform is not None:
            self.current = current_platform
            if self.state == STATE_READY:
                self.char_x = current_platform.rect.center_x
                self.char_feet = float(current_platform.rect.top)
        if respawn_platform is not None:
            self.respawn_platform = respawn_platform
        prefix = list(self.course.checkpoints[: self.checkpoint_index])
        self.course.platforms = list(platforms)
        self.course.checkpoints = prefix + list(checkpoints)
        self.course.bonus_stars = list(bonus_stars)
        if self.checkpoint_index > 0 and respawn_platform is None:
            self.respawn_platform = self.course.checkpoints[self.checkpoint_index - 1]
        elif self.checkpoint_index == 0 and respawn_platform is None:
            self.respawn_platform = self.course.start
        self.collected_star_uids &= {p.uid for p in bonus_stars}
        self.intermediate_since_checkpoint = True
        # 若当前站在平台上，只调整面向，不突然传送。
        target = self.next_target
        if target is not None:
            self.direction = 1 if target.center_x >= self.current.center_x else -1

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
        self.timer_started = True
        self.flight_time = 0.0
        self.missed = False
        return True

    def advance_clock(self, dt: float) -> None:
        """由 UI 每帧调用；失焦时 UI 不调用，因此暂停计时。"""
        if not self.timer_started:
            return
        if self.state in (STATE_READY, STATE_CHARGING, STATE_AIRBORNE):
            self.elapsed += clamp(float(dt), 0.0, MAX_DT)

    # -- 物理 -----------------------------------------------------------------

    def tick(self, dt: float) -> UpdateEvents:
        events = UpdateEvents(hearts_left=self.hearts_left)
        if self.state != STATE_AIRBORNE:
            return events

        dt = clamp(float(dt), 0.0, MAX_DT)
        if dt <= 0:
            return events

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
                    self.course.platforms,
                )
                if landing is not None:
                    return self._land_on(landing)

                if (
                    self.char_x < -FALL_MARGIN
                    or self.char_x > self.screen.right + FALL_MARGIN
                ):
                    self.missed = True

            if (
                self.char_feet > self.screen.bottom + FALL_MARGIN
                or self.flight_time > MAX_FLIGHT_SECONDS
            ):
                return self._finish_fall()

        return events

    # -- 落点与检查点 -----------------------------------------------------------

    def _land_on(self, landing: Landing) -> UpdateEvents:
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
        self.state = STATE_READY

        events = UpdateEvents(
            landed=platform,
            combo=self.combo,
            checkpoint_index=self.checkpoint_index,
            hearts_left=self.hearts_left,
        )
        target = self.next_target
        if target is not None and platform.uid == target.uid:
            old_index = self.checkpoint_index
            self.checkpoint_index += 1
            self.combo += 1
            perfect = abs(x - rect.center_x) <= rect.width * PERFECT_CENTER_RATIO
            direct = not self.intermediate_since_checkpoint
            multiplier = self.combo_multiplier
            raw_points = (
                CHECKPOINT_BASE_SCORE
                + CHECKPOINT_SCORE_STEP * old_index
                + (PERFECT_BONUS if perfect else 0)
                + (DIRECT_BONUS if direct else 0)
            )
            points = int(round(raw_points * multiplier))
            self.score += points
            if perfect:
                self.perfect_count += 1
            if direct:
                self.direct_count += 1
            self.respawn_platform = platform
            self.intermediate_since_checkpoint = False

            events.checkpoint = platform
            events.perfect = perfect
            events.direct = direct
            events.score_delta += points
            events.combo = self.combo
            events.checkpoint_index = self.checkpoint_index
            events.hearts_left = self.hearts_left

            if self.checkpoint_index >= len(self.course.checkpoints):
                finish_bonus = self._finalize_victory()
                events.score_delta += finish_bonus
                events.victory = True
            return events

        # 非当前检查点：可选星星拿分，否则只是普通落脚点。
        if any(star.uid == platform.uid for star in self.course.bonus_stars):
            if platform.uid not in self.collected_star_uids:
                self.collected_star_uids.add(platform.uid)
                self.score += STAR_BONUS
                events.star = platform
                events.score_delta += STAR_BONUS
        self.intermediate_since_checkpoint = True
        events.combo = self.combo
        events.checkpoint_index = self.checkpoint_index
        events.hearts_left = self.hearts_left
        return events

    def _make_result(self, completed: bool) -> RunResult:
        if completed:
            rating = 3 if self.falls == 0 else (2 if self.falls <= 1 else 1)
        else:
            rating = 0
        return RunResult(
            completed=completed,
            score=self.score,
            elapsed=self.elapsed,
            hearts_left=self.hearts_left,
            falls=self.falls,
            perfect_count=self.perfect_count,
            direct_count=self.direct_count,
            stars_collected=self.stars_collected,
            stars_total=self.stars_total,
            rating=rating,
        )

    def _finalize_victory(self) -> int:
        if self._result is not None:
            return 0
        hearts_bonus = 0 if self.relaxed else max(0, self.hearts) * FINISH_HEART_BONUS
        time_bonus = max(
            0,
            FINISH_TIME_BONUS_BASE - int(self.elapsed * FINISH_TIME_BONUS_RATE),
        )
        finish_bonus = hearts_bonus + time_bonus
        self.score += finish_bonus
        self.state = STATE_VICTORY
        self._result = self._make_result(True)
        return finish_bonus

    def _finish_fall(self) -> UpdateEvents:
        self.falls += 1
        self.combo = 0
        self.intermediate_since_checkpoint = False
        self.vx = 0.0
        self.vy = 0.0
        self.power = 0.0
        if not self.relaxed:
            self.hearts = max(0, self.hearts - 1)

        events = UpdateEvents(
            fell=True,
            combo=0,
            checkpoint_index=self.checkpoint_index,
            hearts_left=self.hearts_left,
        )
        if not self.relaxed and self.hearts <= 0:
            self.state = STATE_GAME_OVER
            self._result = self._make_result(False)
            events.game_over = True
            return events

        self._reset_position()
        return events

    def reset_to_respawn(self) -> None:
        """无惩罚回到最近检查点，用于布局刷新等非坠落场景。"""
        self.cancel_charge()
        self._reset_position()

    def force_land_for_test(
        self, platform: Platform, x: Optional[float] = None
    ) -> UpdateEvents:
        """测试辅助：直接完成一次落点判定，不参与正式流程。"""
        px = self.char_x if x is None else x
        return self._land_on(
            Landing(platform=platform, x=px, top=platform.rect.top, t=0.0)
        )

def startup_countdown(seconds: float, message: str = "Dango Circuit：请切换到要游玩的窗口。") -> None:
    """终端倒计时；给用户时间切换到目标窗口。"""
    if seconds <= 0:
        return
    print(message, flush=True)
    whole = max(0, int(math.ceil(seconds)))
    for remaining in range(whole, 0, -1):
        print(f"  {remaining}…", flush=True)
        time.sleep(1.0)
