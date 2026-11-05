#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dango Jump — Windows 10/11 backend (Tk + UI Automation)."""

from __future__ import annotations

import math
import os
import random
import sys
import time
import traceback
from collections import deque
from typing import Any, Optional, Sequence

from dango_core import (
    APP_NAME,
    CANDIDATE_CONTROL_TYPES,
    CHAR_SIZE,
    COL_AMBER,
    COL_AMBER_SOFT,
    COL_AQUA,
    COL_AQUA_DIM,
    COL_AQUA_SOFT,
    COL_GREEN,
    COL_INK,
    COL_LINE,
    COL_MUTED,
    COL_PANEL,
    COL_PANEL_2,
    COL_RED,
    COL_TEXT,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_H1,
    FONT_SMALL,
    FONT_SMALL_BOLD,
    FPS_MS,
    GRAVITY,
    GameModel,
    LIMIT_RETRIES,
    LevelError,
    MAX_DT,
    MAX_RETRIES,
    MONITOR_INTERVAL_MS,
    Particle,
    Platform,
    RawControl,
    Rect,
    SCAN_MAX_DEPTH,
    SCAN_MAX_NODES,
    SCAN_TIMEOUT_SECONDS,
    STATE_AIRBORNE,
    STATE_CHARGING,
    ScanError,
    ScanResult,
    TARGET_MOVE_TOLERANCE,
    TRANSPARENT_KEY,
    UpdateEvents,
    clamp,
    filter_platforms,
    generate_level,
    jump_distance,
    platform_display_name,
    point_in_rect,
    rects_differ,
    rounded_rect_points,
    startup_countdown,
    trajectory_points,
    truncate_text,
)


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
# Windows 启动入口
# =============================================================================


def _countdown(seconds: float) -> None:
    startup_countdown(seconds)


def run_windows(args: Any) -> int:
    """由 main.py 调用的 Windows 启动入口。"""
    try:
        enable_dpi_awareness()
        screen = primary_screen_rect()

        if args.desktop:
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
            import tkinter  # noqa: F401
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
