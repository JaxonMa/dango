#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macos_app — Dango Jump 的 macOS PySide6 透明覆盖层。

实现要点：
    * Qt 无边框、全屏、置顶、逐像素透明窗口；
    * 透明像素天然穿透鼠标，不透明 HUD/卡片区域可点击；
    * 覆盖层获得键盘焦点时由 Qt 接收游戏键，失焦暂停；
    * AX 控件读取、权限提示、焦点归还和桌面扫描由 macos_ax 完成。
"""

from __future__ import annotations

import math
import random
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import AppKit
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QWidget

from dango_core import (
    APP_NAME,
    CHAR_SIZE,
    COL_AQUA_SOFT,
    COL_TEXT,
    FPS_MS,
    GRAVITY,
    GameModel,
    LIMIT_RETRIES,
    LevelError,
    MAX_DT,
    MAX_RETRIES,
    MONITOR_INTERVAL_MS,
    Particle,
    Rect,
    STATE_AIRBORNE,
    STATE_CHARGING,
    ScanError,
    ScanResult,
    UpdateEvents,
    clamp,
    generate_level,
    jump_distance,
    platform_display_name,
    startup_countdown,
    trajectory_points,
    truncate_text,
)
from macos_ax import (
    MacTargetApp,
    accessibility_trusted,
    activate_app,
    activate_current_app,
    get_frontmost_app,
    open_accessibility_settings,
    platform_is_live_ax,
    raise_window,
    refresh_target_window,
    scan_desktop,
    scan_target,
)


# =============================================================================
# Qt 颜色与常量
# =============================================================================


def qc(hex_color: str, alpha: int = 255) -> QColor:
    color = QColor(hex_color)
    color.setAlpha(alpha)
    return color


Q_INK = qc("#07111A")
Q_PANEL = qc("#0B1622", 238)
Q_PANEL_2 = qc("#111F2E", 242)
Q_AQUA = qc("#22D3EE")
Q_AQUA_SOFT = qc("#67E8F9")
Q_AQUA_DIM = qc("#155E75")
Q_AMBER = qc("#FBBF24")
Q_AMBER_SOFT = qc("#FDE68A")
Q_GREEN = qc("#34D399")
Q_RED = qc("#FB7185")
Q_TEXT = qc("#F8FAFC")
Q_MUTED = qc("#94A3B8")
Q_LINE = qc("#334155")
Q_CANDIDATE = qc("#3B4A5E")

# 菜单栏高度之外的顶部安全边距：保证团子和方向箭头完整可见。
MAC_TOP_SAFE_MARGIN = 44


@dataclass
class MacButton:
    action: str
    label: str
    rect: Rect


# =============================================================================
# Qt 覆盖层
# =============================================================================


class MacOverlay(QWidget):
    """macOS 全屏透明覆盖层和游戏外壳。"""

    def __init__(
        self,
        screen: Rect,
        available: Rect,
        scope: str,
        target_app: Optional[MacTargetApp],
        *,
        limit_retries: bool = LIMIT_RETRIES,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        super().__init__()
        self.screen = screen
        self.available = available
        self.scope = scope
        self.target_app = target_app
        self.limit_retries = limit_retries
        self.max_retries = max_retries

        self.running = True
        self.mode = "scanning"       # scanning / permission / playing / error / victory / game_over
        self.model: Optional[GameModel] = None
        self.scanning = False
        self.focus_lost = False
        self._suppress_focus = False
        self._space_down = False
        self._scan_reason = "正在读取当前窗口控件…"
        self._toast_text = ""
        self._toast_until = 0.0
        self._particles: list[Particle] = []
        self.card_buttons: list[MacButton] = []
        self._hover_action: Optional[str] = None
        self._last_tick = time.perf_counter()
        self._focus_grace_until = time.monotonic() + 0.8
        self._permission_prompted = False
        self._hidden_for_settings = False
        self._last_scan: Optional[ScanResult] = None
        self._window_title = "整个桌面" if scope == "desktop" else "当前窗口"
        self._static_cache: Optional[QPixmap] = None
        self._static_dirty = True

        self.setWindowTitle("Dango Jump")
        # 不使用 Qt.Tool：macOS 的 utility window 在 App 失焦时可能被系统
        # 自动隐藏，导致暂停提示也一起消失。普通无边框窗口会保持可见。
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setGeometry(screen.left, screen.top, screen.width, screen.height)

        self._font_brand = QFont("Menlo", 18)
        self._font_brand.setBold(True)
        self._font_title = QFont("PingFang SC", 28)
        self._font_title.setBold(True)
        self._font_h1 = QFont("PingFang SC", 20)
        self._font_h1.setBold(True)
        self._font_body = QFont("PingFang SC", 13)
        self._font_body_bold = QFont("PingFang SC", 13)
        self._font_body_bold.setBold(True)
        self._font_small = QFont("PingFang SC", 11)
        self._font_small_bold = QFont("PingFang SC", 11)
        self._font_small_bold.setBold(True)
        self._font_mono = QFont("Menlo", 11)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(FPS_MS)
        self._tick_timer.timeout.connect(self._tick)

        self._monitor_timer = QTimer(self)
        self._monitor_timer.setInterval(MONITOR_INTERVAL_MS)
        self._monitor_timer.timeout.connect(self._monitor_targets)

        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.timeout.connect(self._release_keyboard_focus)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(360)
        self._pulse_timer.timeout.connect(self._pulse_tick)

        self._last_repaint = 0.0
        self._paint_interval = 1.0 / 30.0

        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)

        self.show()
        self.raise_()
        self._activate_self()
        self._tick_timer.start()
        self._pulse_timer.start()
        QTimer.singleShot(160, self._initial_scan)

    # =========================================================================
    # 生命周期、焦点和激活
    # =========================================================================

    def _activate_self(self) -> None:
        if not self.running:
            return
        self._suppress_focus = False
        self.focus_lost = False
        if self._release_timer.isActive():
            self._release_timer.stop()
        self._focus_grace_until = time.monotonic() + 0.6
        try:
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.OtherFocusReason)
            activate_current_app()
        except Exception:
            pass
        QTimer.singleShot(320, self._verify_focus)
        self.update()

    def _verify_focus(self) -> None:
        if not self.running or self._suppress_focus:
            return
        if self.mode in ("victory", "game_over"):
            return
        if time.monotonic() < self._focus_grace_until:
            QTimer.singleShot(180, self._verify_focus)
            return
        if not self.isActiveWindow() or not self.hasFocus():
            self._set_focus_lost()

    def _set_focus_lost(self) -> None:
        if not self.running or self._suppress_focus:
            return
        if self.mode in ("victory", "game_over"):
            return
        if not self.focus_lost:
            self.focus_lost = True
            self._space_down = False
            if self.model is not None:
                self.model.cancel_charge()
            self.update()

    def _on_app_state(self, state: Any) -> None:
        if state == Qt.ApplicationActive:
            if self._hidden_for_settings:
                self._hidden_for_settings = False
                self._suppress_focus = False
                self.focus_lost = False
                self.show()
                self.raise_()
                self.activateWindow()
                self.setFocus(Qt.OtherFocusReason)
            self.focus_lost = False
            if self.mode == "permission" and accessibility_trusted() and not self.scanning:
                QTimer.singleShot(
                    180,
                    lambda: self._begin_scan(
                        reset_stats=self.model is None,
                        reason="辅助功能权限已开启，正在读取控件…",
                    ),
                )
            self.update()
        elif state == Qt.ApplicationInactive:
            self._set_focus_lost()

    def _open_permission_settings(self) -> None:
        """临时隐藏覆盖层，避免挡住系统设置；从 Dock 返回时自动恢复。"""
        self._suppress_focus = True
        self._hidden_for_settings = True
        self.hide()
        QTimer.singleShot(220, self._launch_settings)

    def _launch_settings(self) -> None:
        open_accessibility_settings()
        # 如果打开设置失败且本 App 仍是前台，避免窗口永久消失。
        QTimer.singleShot(1400, self._restore_if_still_frontmost)

    def _restore_if_still_frontmost(self) -> None:
        if not self.running or not self._hidden_for_settings:
            return
        try:
            front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            current = AppKit.NSRunningApplication.currentApplication()
            if (
                front is not None
                and current is not None
                and int(front.processIdentifier()) == int(current.processIdentifier())
            ):
                self._on_app_state(Qt.ApplicationActive)
        except Exception:
            pass

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self.focus_lost = False
                self.update()
            else:
                self._set_focus_lost()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self._set_focus_lost()

    def focusInEvent(self, event: Any) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.focus_lost = False
        self.update()

    def _release_keyboard_focus(self) -> None:
        """把键盘还给目标 App；覆盖层卡片继续显示。"""
        if not self.running:
            return
        self._suppress_focus = True
        self.focus_lost = False
        app = self.target_app
        if app is not None and app.pid:
            try:
                raise_window(app.window_element)
                activate_app(app.pid)
            except Exception:
                pass
        self.update()

    def _quit(self) -> None:
        if not self.running:
            return
        self.running = False
        self._tick_timer.stop()
        self._monitor_timer.stop()
        self._release_timer.stop()
        self._pulse_timer.stop()
        app = self.target_app
        if app is not None and app.pid:
            try:
                raise_window(app.window_element)
                activate_app(app.pid)
            except Exception:
                pass
        self.close()
        QApplication.quit()

    # =========================================================================
    # 扫描、权限和关卡
    # =========================================================================

    def _initial_scan(self) -> None:
        if not self.running:
            return
        self._activate_self()
        if not accessibility_trusted():
            self._show_permission_card()
            return
        self._begin_scan(
            reset_stats=True,
            reason="正在读取前台 App 控件…",
        )

    def _show_permission_card(self) -> None:
        if not self.running:
            return
        self.scanning = False
        self.mode = "permission"
        self._space_down = False
        self.card_buttons = []
        self.update()
        self._activate_self()
        if not self._permission_prompted:
            self._permission_prompted = True
            QTimer.singleShot(120, lambda: accessibility_trusted(prompt=True))

    def _begin_scan(self, *, reset_stats: bool, reason: str) -> None:
        if self.scanning or not self.running:
            return
        self.scanning = True
        self.mode = "scanning"
        self.focus_lost = False
        self._space_down = False
        self._scan_reason = reason
        self._monitor_timer.stop()
        self._release_timer.stop()
        self.card_buttons = []
        self.update()
        QTimer.singleShot(110, lambda: self._perform_scan(reset_stats))

    def _perform_scan(self, reset_stats: bool) -> None:
        if not self.running:
            self.scanning = False
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if not accessibility_trusted():
                self.scanning = False
                self._show_permission_card()
                return
            if self.scope == "desktop":
                result = scan_desktop(self.screen)
            else:
                if self.target_app is None:
                    raise ScanError("没有可读取的前台 App。", kind="window")
                if self.target_app.window_element is None:
                    refresh_target_window(self.target_app)
                result = scan_target(self.target_app, self.screen)
            result = self._filter_playable(result)
            level = generate_level(result.platforms)
        except ScanError as exc:
            self.scanning = False
            if str(exc.kind) == "permission":
                self._show_permission_card()
            else:
                self._show_error_card(str(exc))
            return
        except LevelError as exc:
            self.scanning = False
            self._show_error_card(str(exc))
            return
        except Exception as exc:
            self.scanning = False
            traceback.print_exc()
            self._show_error_card(f"读取控件时发生意外错误：{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

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
        self._invalidate_static()
        self._particles.clear()
        self._space_down = False
        self._toast_text = ""
        self._activate_self()
        self._monitor_timer.start()
        self.update()

    def _filter_playable(self, result: ScanResult) -> ScanResult:
        """去掉紧贴菜单栏的控件，避免角色和方向箭头被系统 UI 遮住。"""
        min_top = self.available.top + MAC_TOP_SAFE_MARGIN
        platforms = [p for p in result.platforms if p.rect.top >= min_top]
        if len(platforms) < 2:
            raise ScanError(
                "可用控件太少，或都紧贴 macOS 菜单栏。"
                "请换一个窗口，或按 D 扫描整个桌面。",
                kind="insufficient",
            )
        return ScanResult(
            platforms=platforms,
            nodes_seen=result.nodes_seen,
            skipped=result.skipped,
            elapsed=result.elapsed,
        )

    def _show_error_card(self, message: str) -> None:
        if not self.running:
            return
        self.scanning = False
        self.mode = "error"
        self._error_message = message
        self._space_down = False
        self._monitor_timer.stop()
        if self.model is not None:
            self.model.cancel_charge()
        self.card_buttons = []
        self._activate_self()
        self.update()

    def _action_retry(self) -> None:
        self._activate_self()
        if not accessibility_trusted():
            self._show_permission_card()
            return
        self._begin_scan(
            reset_stats=self.model is None,
            reason="正在重新读取当前控件…",
        )

    def _action_desktop(self) -> None:
        self.scope = "desktop"
        self._window_title = "整个桌面"
        self._activate_self()
        self._begin_scan(
            reset_stats=self.model is None,
            reason="正在扫描整个桌面控件…",
        )

    def _action_restart(self) -> None:
        self._suppress_focus = False
        self._activate_self()
        if not accessibility_trusted():
            self._show_permission_card()
            return
        self._begin_scan(
            reset_stats=True,
            reason="正在重新读取控件并生成新关卡…",
        )

    def _monitor_targets(self) -> None:
        if (
            not self.running
            or self.mode != "playing"
            or self.model is None
            or self.scanning
            or self.focus_lost
        ):
            return
        target = self.model.level.target
        current = self.model.current
        target_ok = platform_is_live_ax(target)
        current_ok = current.uid == target.uid or platform_is_live_ax(current)
        if not target_ok or not current_ok:
            self._begin_scan(
                reset_stats=False,
                reason="控件已消失或移动，正在重新生成关卡…",
            )

    # =========================================================================
    # 输入
    # =========================================================================

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self._quit()
            event.accept()
            return

        if self.mode == "permission":
            if key == Qt.Key_R:
                self._action_retry()
            elif key == Qt.Key_S:
                open_accessibility_settings()
            event.accept()
            return

        if self.mode == "error":
            if key == Qt.Key_R:
                self._action_retry()
            elif key == Qt.Key_D:
                self._action_desktop()
            event.accept()
            return

        if self.mode in ("victory", "game_over"):
            if key == Qt.Key_R:
                self._action_restart()
            event.accept()
            return

        if self.mode != "playing" or self.model is None:
            event.accept()
            return

        if self.focus_lost:
            if key in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right):
                self._activate_self()
            event.accept()
            return

        if key == Qt.Key_Left:
            self.model.set_direction(-1)
            self.update()
        elif key == Qt.Key_Right:
            self.model.set_direction(1)
            self.update()
        elif key == Qt.Key_Space:
            if not event.isAutoRepeat() and not self._space_down:
                self._space_down = True
                self.model.begin_charge(time.perf_counter())
                self.update()
        elif key == Qt.Key_R:
            self._begin_scan(
                reset_stats=True,
                reason="正在重新读取控件并生成新关卡…",
            )
        event.accept()

    def keyReleaseEvent(self, event: Any) -> None:  # noqa: N802
        if event.key() != Qt.Key_Space:
            event.accept()
            return
        if event.isAutoRepeat():
            event.accept()
            return
        if self._space_down:
            self._space_down = False
            if (
                self.mode == "playing"
                and self.model is not None
                and not self.focus_lost
            ):
                if self.model.release(time.perf_counter()):
                    self._invalidate_static()
                self.update()
        event.accept()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position()
        action = None
        for button in self.card_buttons:
            if button.rect.contains_point(pos.x(), pos.y()):
                action = button.action
                break

        self._activate_self()
        if action:
            self._dispatch_action(action)
            event.accept()
            return

        if self.mode == "playing":
            self.update()
        event.accept()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
        hover = None
        pos = event.position()
        for button in self.card_buttons:
            if button.rect.contains_point(pos.x(), pos.y()):
                hover = button.action
                break
        if hover != self._hover_action:
            self._hover_action = hover
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def _dispatch_action(self, action: str) -> None:
        if action == "perm_settings":
            self._open_permission_settings()
        elif action == "perm_retry":
            self._action_retry()
        elif action == "retry":
            self._action_retry()
        elif action == "desktop":
            self._action_desktop()
        elif action == "restart":
            self._action_restart()
        elif action == "quit":
            self._quit()

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
                animating = False
                if not self.focus_lost:
                    if self.model.state == STATE_CHARGING:
                        self.model.update_charge(now)
                        animating = True
                    elif self.model.state == STATE_AIRBORNE:
                        events = self.model.tick(dt)
                        self._handle_events(events)
                        animating = True
                if self._particles:
                    self._update_particles(dt)
                    animating = True
                # 空闲时不重绘整块全屏透明层，避免无意义的 GPU/CPU 合成。
                if animating and now - self._last_repaint >= self._paint_interval:
                    self._last_repaint = now
                    self.update()
        except Exception:
            traceback.print_exc()
            self._show_error_card("游戏运行发生异常，请按 R 重试。")

    def _pulse_tick(self) -> None:
        if self.running and self.mode == "playing" and not self.focus_lost:
            self.update()

    def _handle_events(self, events: UpdateEvents) -> None:
        if self.model is None:
            return
        if events.landed is not None:
            self._invalidate_static()
            self._spawn_particles(
                self.model.char_x,
                self.model.char_feet,
                9,
                COL_AQUA_SOFT,
            )
        if events.victory:
            self.mode = "victory"
            self._space_down = False
            self._monitor_timer.stop()
            self.card_buttons = []
            self.update()
            self._release_timer.start(320)
            return
        if events.game_over:
            self.mode = "game_over"
            self._space_down = False
            self._monitor_timer.stop()
            self.card_buttons = []
            self.update()
            self._release_timer.start(320)
            return
        if events.fell:
            self._invalidate_static()
            self.show_toast(
                "坠落！已回到起点。" if not self.model.limit_retries else "坠落！损失一次机会。",
                1.8,
            )

    def show_toast(self, text: str, seconds: float = 1.8) -> None:
        self._toast_text = text
        self._toast_until = time.perf_counter() + seconds
        self.update()
        QTimer.singleShot(int(seconds * 1000) + 40, self.update)

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
    # 绘制入口
    # =========================================================================

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        # 透明窗口没有系统背景，必须显式清除上一帧，否则会留下拖影。
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        try:
            if self.mode == "playing" and self.model is not None:
                self._paint_playing(painter)
            elif self.mode == "permission":
                self._paint_permission(painter)
            elif self.mode == "scanning":
                self._paint_scanning(painter)
            elif self.mode == "error":
                self._paint_error(painter)
            elif self.mode == "victory":
                self._paint_victory(painter)
            elif self.mode == "game_over":
                self._paint_game_over(painter)
        finally:
            painter.end()

    def _invalidate_static(self) -> None:
        self._static_dirty = True

    def _ensure_static_cache(self) -> None:
        if not self._static_dirty and self._static_cache is not None:
            return
        if self.model is None:
            self._static_cache = None
            return
        dpr = max(1.0, float(self.devicePixelRatioF()))
        image = QImage(
            int(self.width() * dpr),
            int(self.height() * dpr),
            QImage.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.scale(dpr, dpr)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        self._paint_candidate_outlines(painter)
        self._paint_hud(painter)
        self._paint_markers_static(painter)
        painter.end()
        self._static_cache = QPixmap.fromImage(image)
        self._static_cache.setDevicePixelRatio(dpr)
        self._static_dirty = False

    def _paint_playing(self, p: QPainter) -> None:
        assert self.model is not None
        self.card_buttons = []
        self._ensure_static_cache()
        if self._static_cache is not None:
            p.drawPixmap(0, 0, self._static_cache)
        self._paint_target_outline(p)
        self._paint_trajectory(p)
        self._paint_particles(p)
        self._paint_direction(p)
        self._paint_character(p)
        self._paint_charge_ui(p)
        self._paint_toast(p)
        if self.focus_lost:
            self._paint_focus_banner(p)

    # =========================================================================
    # 绘制辅助
    # =========================================================================

    def _rounded(self, p: QPainter, rect: QRectF, radius: float,
                 fill: QColor, outline: Optional[QColor] = None,
                 width: float = 1.0) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        # 显式设置 brush：PySide6 的 fillPath(path, QColor) 在部分版本会沿用
        # 当前 painter brush，导致面板取到上一个绘制颜色。
        p.save()
        p.setBrush(QBrush(fill))
        p.setPen(Qt.NoPen)
        p.drawPath(path)
        if outline is not None and width > 0:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(outline, width))
            p.drawPath(path)
        p.restore()

    def _pill(self, p: QPainter, rect: QRectF, fill: QColor,
              outline: Optional[QColor] = Q_LINE, radius: Optional[float] = None) -> None:
        radius = min(12.0, rect.height() / 2.0) if radius is None else radius
        self._rounded(p, rect, radius, fill, outline, 1.0)

    def _text(
        self,
        p: QPainter,
        rect: QRectF,
        text: str,
        font: QFont,
        color: QColor,
        flags: int = Qt.AlignCenter,
    ) -> None:
        p.setFont(font)
        p.setPen(color)
        p.drawText(rect, flags, text)

    def _text_width(self, p: QPainter, text: str, font: QFont) -> float:
        p.setFont(font)
        return float(QFontMetrics(font).horizontalAdvance(text))

    def _paint_candidate_outlines(self, p: QPainter) -> None:
        assert self.model is not None
        pen = QPen(Q_CANDIDATE, 1.0)
        pen.setDashPattern([1.0, 4.0])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for platform in self.model.level.platforms:
            r = platform.rect
            p.drawRect(QRectF(r.left, r.top, r.width, r.height))

    def _paint_markers_static(self, p: QPainter) -> None:
        if self.model is None:
            return
        level = self.model.level

        # 起点
        start = level.start.rect
        sx = max(8, start.left + 4)
        sy = max(self.available.top + 26, start.top - 24)
        label_rect = QRectF(sx - 2, sy - 13, 52, 26)
        self._pill(p, label_rect, Q_PANEL, Q_AQUA_DIM, 8)
        self._text(p, label_rect, "起点", self._font_small_bold, Q_AQUA)
        triangle = QPolygonF(
            [
                QPointF(sx + 24, start.top - 1),
                QPointF(sx + 17, start.top - 11),
                QPointF(sx + 31, start.top - 11),
            ]
        )
        p.setBrush(Q_AQUA)
        p.setPen(QPen(Q_INK, 1.0))
        p.drawPolygon(triangle)

        # 当前平台
        cur = self.model.current.rect
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(Q_AQUA, 2.0))
        p.drawRect(QRectF(cur.left, cur.top, cur.width, cur.height))

        # 目标标签（目标描边是动态闪烁的，不放进静态缓存）
        target = level.target.rect
        label_text = f"目标 · {platform_display_name(level.target)}"
        label_text = truncate_text(label_text, 32)
        width = min(380.0, max(160.0, self._text_width(p, label_text, self._font_small_bold) + 34.0))
        cx = clamp(target.center_x, 12 + width / 2, self.screen.right - 12 - width / 2)
        ly = target.top - 26 if target.top > self.available.top + 50 else target.bottom + 26
        label = QRectF(cx - width / 2, ly - 15, width, 30)
        self._pill(p, label, Q_PANEL, Q_AMBER, 9)
        self._text(p, label, label_text, self._font_small_bold, Q_AMBER_SOFT)

    def _paint_target_outline(self, p: QPainter) -> None:
        if self.model is None:
            return
        target = self.model.level.target.rect
        pulse = int(time.perf_counter() * 2.5) % 2
        color = Q_AMBER if pulse == 0 else Q_AMBER_SOFT
        outer = QRectF(target.left - 3, target.top - 3, target.width + 6, target.height + 6)
        pen = QPen(color, 3.0)
        pen.setDashPattern([6.0, 4.0])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(outer)
        inner = QRectF(target.left + 2, target.top + 2, target.width - 4, target.height - 4)
        pen2 = QPen(Q_AMBER_SOFT, 1.0)
        pen2.setDashPattern([2.0, 6.0])
        p.setPen(pen2)
        p.drawRect(inner)

    def _paint_hud(self, p: QPainter) -> None:
        if self.model is None:
            return
        w = self.screen.width
        top = self.available.top + 12
        h = 62

        # 品牌
        brand = QRectF(20, top, 310, h)
        self._pill(p, brand, Q_PANEL, Q_LINE, 12)
        self._text(
            p,
            QRectF(38, top + 6, 270, 28),
            APP_NAME,
            self._font_brand,
            Q_AQUA,
            Qt.AlignLeft | Qt.AlignVCenter,
        )
        scope_text = self._scope_label()
        self._text(
            p,
            QRectF(38, top + 32, 274, 20),
            truncate_text(scope_text, 38),
            self._font_small,
            Q_MUTED,
            Qt.AlignLeft | Qt.AlignVCenter,
        )

        # 目标
        tx1 = max(350, w / 2 - 245)
        tx2 = min(w - 330, w / 2 + 245)
        if tx2 - tx1 < 280:
            tx1 = w / 2 - 190
            tx2 = w / 2 + 190
        target_panel = QRectF(tx1, top, tx2 - tx1, h)
        self._pill(p, target_panel, Q_PANEL, Q_AMBER, 12)
        self._text(
            p,
            QRectF(tx1, top + 5, tx2 - tx1, 20),
            "当前目标",
            self._font_small,
            Q_AMBER,
        )
        self._text(
            p,
            QRectF(tx1 + 8, top + 25, tx2 - tx1 - 16, 28),
            platform_display_name(self.model.level.target),
            self._font_body_bold,
            Q_TEXT,
        )

        # 统计
        stats = QRectF(w - 310, top, 290, h)
        self._pill(p, stats, Q_PANEL, Q_LINE, 12)
        stat_text = f"跳跃 {self.model.jumps}  ·  坠落 {self.model.falls}"
        if self.model.limit_retries:
            stat_text += f"  ·  剩余 {self.model.remaining_retries}"
        self._text(
            p,
            QRectF(stats.left() + 14, stats.top() + 6, stats.width() - 28, 24),
            stat_text,
            self._font_small_bold,
            Q_TEXT,
            Qt.AlignLeft | Qt.AlignVCenter,
        )
        self._text(
            p,
            QRectF(stats.left() + 14, stats.top() + 32, stats.width() - 28, 20),
            "R 新关卡 · Esc 退出",
            self._font_small,
            Q_MUTED,
            Qt.AlignLeft | Qt.AlignVCenter,
        )

    def _scope_label(self) -> str:
        if self.scope == "desktop":
            return "范围：整个桌面"
        if self.target_app is None:
            return "窗口：未知"
        title = self.target_app.name
        if self.target_app.window_title:
            title = f"{title} — {truncate_text(self.target_app.window_title, 28)}"
        return f"窗口：{title}"

    def _paint_character(self, p: QPainter) -> None:
        if self.model is None:
            return
        x = self.model.char_x
        feet = self.model.char_feet
        if feet < -60 or feet > self.screen.bottom + 60:
            return
        body = QRectF(x - CHAR_SIZE / 2, feet - CHAR_SIZE, CHAR_SIZE, CHAR_SIZE)
        p.setBrush(Q_AQUA)
        p.setPen(QPen(Q_INK, 2.0))
        p.drawRoundedRect(body, 10.0, 10.0)
        # 高光
        p.setBrush(qc("#ECFEFF", 220))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(body.left() + 6, body.top() + 6, 7, 4))
        # 眼睛
        eye_shift = 1.4 * self.model.direction
        p.setBrush(Q_INK)
        for ex in (x - 5.3, x + 5.3):
            p.drawEllipse(QRectF(ex - 1.8 + eye_shift, feet - 19, 3.6, 5))
            p.setBrush(Q_TEXT)
            p.drawEllipse(QRectF(ex - 1.4 + eye_shift, feet - 18, 1.2, 1.4))
            p.setBrush(Q_INK)
        # 腮红
        p.setBrush(qc("#FB7185", 150))
        p.drawEllipse(QRectF(x - 13, feet - 13, 5, 2.5))
        p.drawEllipse(QRectF(x + 8, feet - 13, 5, 2.5))

    def _paint_direction(self, p: QPainter) -> None:
        if self.model is None:
            return
        x = self.model.char_x
        feet = self.model.char_feet
        y = feet - CHAR_SIZE - 10
        for direction in (-1, 1):
            active = self.model.direction == direction
            tip_x = clamp(x + direction * 44, 14, self.screen.right - 14)
            if direction < 0:
                poly = QPolygonF(
                    [QPointF(tip_x, y), QPointF(tip_x + 12, y - 8), QPointF(tip_x + 12, y + 8)]
                )
            else:
                poly = QPolygonF(
                    [QPointF(tip_x, y), QPointF(tip_x - 12, y - 8), QPointF(tip_x - 12, y + 8)]
                )
            p.setBrush(Q_AQUA if active else Q_PANEL)
            p.setPen(QPen(Q_AQUA if active else Q_AQUA_DIM, 2.0 if active else 1.0))
            p.drawPolygon(poly)
        if self.model.state == STATE_CHARGING:
            rect = QRectF(x + self.model.direction * 62 - 30, y - 10, 60, 20)
            self._text(p, rect, "蓄力", self._font_small_bold, Q_AQUA_SOFT)

    def _paint_trajectory(self, p: QPainter) -> None:
        if self.model is None or self.model.state != STATE_CHARGING:
            return
        points = trajectory_points(
            self.model.char_x,
            self.model.char_feet,
            self.model.power,
            self.model.direction,
        )
        p.setPen(Qt.NoPen)
        for index, (px, py) in enumerate(points):
            if not (0 <= px <= self.screen.right and 0 <= py <= self.screen.bottom):
                continue
            radius = max(1.0, 3.2 * (1.0 - index / len(points)))
            p.setBrush(Q_AQUA_DIM if index % 2 else Q_AQUA_SOFT)
            p.drawEllipse(QRectF(px - radius, py - radius, radius * 2, radius * 2))

    def _paint_charge_ui(self, p: QPainter) -> None:
        if self.model is None:
            return
        w = self.screen.width
        bottom = self.available.bottom
        panel_w = 520
        x1 = w / 2 - panel_w / 2
        y1 = bottom - 106
        x2 = x1 + panel_w
        y2 = bottom - 36
        charging = self.model.state == STATE_CHARGING
        full = charging and self.model.power >= 0.999
        outline = Q_AMBER if full else Q_LINE
        self._pill(p, QRectF(x1, y1, x2 - x1, y2 - y1), Q_PANEL, outline, 14)

        if charging:
            percent = int(round(self.model.power * 100))
            distance = int(round(jump_distance(self.model.power)))
            label = f"蓄力 {percent:>3d}%   ·   预计 {distance:>4d} px"
            self._text(
                p,
                QRectF(x1 + 18, y1 + 8, x2 - x1 - 36, 22),
                label,
                self._font_small_bold,
                Q_AMBER_SOFT if percent >= 100 else Q_TEXT,
                Qt.AlignLeft | Qt.AlignVCenter,
            )
            bar = QRectF(x1 + 18, y1 + 38, x2 - x1 - 36, 18)
            self._rounded(p, bar, 5, Q_INK, Q_LINE, 1)
            fill_w = bar.width() * self.model.power
            if fill_w > 1:
                self._rounded(
                    p,
                    QRectF(bar.left(), bar.top(), fill_w, bar.height()),
                    5,
                    Q_AMBER if full else Q_AQUA,
                    None,
                    0,
                )
            p.setPen(QPen(Q_PANEL, 1.0))
            for tick in (0.25, 0.5, 0.75):
                tx = bar.left() + bar.width() * tick
                p.drawLine(QPointF(tx, bar.top() + 2), QPointF(tx, bar.bottom() - 2))
        else:
            self._text(
                p,
                QRectF(x1 + 12, y1 + 6, x2 - x1 - 24, y2 - y1 - 12),
                "←  →  切换方向    ·    长按空格蓄力    ·    松开起跳",
                self._font_body,
                Q_TEXT,
            )

    def _paint_particles(self, p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        for particle in self._particles:
            ratio = max(0.0, particle.life / particle.max_life)
            radius = max(0.7, particle.size * ratio)
            p.setBrush(QColor(particle.color))
            p.drawEllipse(
                QRectF(particle.x - radius, particle.y - radius, radius * 2, radius * 2)
            )

    def _paint_toast(self, p: QPainter) -> None:
        now = time.perf_counter()
        if not self._toast_text or now > self._toast_until:
            return
        text = truncate_text(self._toast_text, 50)
        width = min(660.0, max(260.0, self._text_width(p, text, self._font_small_bold) + 56.0))
        x = self.screen.width / 2
        y = self.available.top + 92
        pill = QRectF(x - width / 2, y - 20, width, 40)
        self._pill(p, pill, Q_PANEL_2, Q_AQUA_DIM, 12)
        self._text(p, pill, text, self._font_small_bold, Q_AQUA_SOFT)

    def _paint_focus_banner(self, p: QPainter) -> None:
        cx = self.available.center_x
        cy = self.available.center_y
        panel = QRectF(cx - 255, cy - 76, 510, 152)
        self._pill(p, panel, Q_PANEL, Q_RED, 16)
        self._text(
            p,
            QRectF(panel.left(), panel.top() + 20, panel.width(), 34),
            "已失去键盘焦点",
            self._font_h1,
            Q_RED,
        )
        self._text(
            p,
            QRectF(panel.left(), panel.top() + 64, panel.width(), 26),
            "点击游戏区域重新聚焦 · Esc 退出",
            self._font_body,
            Q_TEXT,
        )
        self._text(
            p,
            QRectF(panel.left(), panel.top() + 101, panel.width(), 20),
            "游戏已暂停",
            self._font_small,
            Q_MUTED,
        )

    # =========================================================================
    # 卡片
    # =========================================================================

    def _card_geometry(self, height: float = 380.0) -> QRectF:
        cx = self.available.left + self.available.width / 2
        cy = self.available.top + self.available.height / 2
        w = min(720.0, self.available.width - 80.0)
        h = min(height, self.available.height - 70.0)
        return QRectF(cx - w / 2, cy - h / 2, w, h)

    def _paint_card_shell(self, p: QPainter, accent: QColor, height: float = 380.0) -> QRectF:
        card = self._card_geometry(height)
        shadow = QRectF(card.left() + 9, card.top() + 11, card.width(), card.height())
        self._rounded(p, shadow, 22, qc("#000000", 180), None, 0)
        self._rounded(p, card, 22, Q_PANEL, accent, 2)
        p.setPen(QPen(accent, 3.0))
        p.drawLine(
            QPointF(card.left() + 24, card.top() + 2),
            QPointF(card.right() - 24, card.top() + 2),
        )
        self._text(
            p,
            QRectF(card.left() + 26, card.top() + 10, 260, 24),
            APP_NAME,
            self._font_mono,
            Q_AQUA,
            Qt.AlignLeft | Qt.AlignVCenter,
        )
        self._text(
            p,
            QRectF(card.right() - 300, card.top() + 10, 274, 24),
            "桌面即关卡 · 控件即平台",
            self._font_small,
            Q_MUTED,
            Qt.AlignRight | Qt.AlignVCenter,
        )
        if self.focus_lost:
            hint = QRectF(card.left() + 18, card.top() + 42, card.width() - 36, 24)
            self._text(
                p,
                hint,
                "键盘焦点已丢失 · 点击此处恢复",
                self._font_small,
                Q_RED,
                Qt.AlignCenter,
            )
        return card

    def _paint_buttons(
        self,
        p: QPainter,
        buttons: Sequence[tuple[str, str]],
        *,
        y: float,
        card: QRectF,
    ) -> None:
        count = max(1, len(buttons))
        gap = 12.0
        button_w = min(210.0, (card.width() - 80.0 - gap * (count - 1)) / count)
        total = button_w * count + gap * (count - 1)
        start_x = card.center().x() - total / 2
        self.card_buttons = []
        for index, (action, label) in enumerate(buttons):
            x1 = start_x + index * (button_w + gap)
            x2 = x1 + button_w
            rect = Rect(int(x1), int(y - 24), int(x2), int(y + 24))
            hover = self._hover_action == action
            if action == "quit":
                fill = Q_PANEL_2
                outline = Q_LINE
            elif action == "desktop":
                fill = Q_PANEL_2
                outline = Q_AMBER
            else:
                fill = Q_AQUA_DIM if not hover else qc("#1A8098")
                outline = Q_AQUA
            self._rounded(
                p,
                QRectF(rect.left, rect.top, rect.width, rect.height),
                12,
                fill,
                outline,
                1,
            )
            self._text(
                p,
                QRectF(rect.left, rect.top, rect.width, rect.height),
                label,
                self._font_small_bold,
                Q_TEXT,
            )
            self.card_buttons.append(MacButton(action=action, label=label, rect=rect))

    def _paint_scanning(self, p: QPainter) -> None:
        self.card_buttons = []
        card = self._paint_card_shell(p, Q_AQUA_DIM)
        self._text(
            p,
            QRectF(card.left(), card.top() + 100, card.width(), 38),
            "正在读取控件",
            self._font_title,
            Q_AQUA,
        )
        self._text(
            p,
            QRectF(card.left() + 30, card.top() + 150, card.width() - 60, 30),
            truncate_text(self._scan_reason, 44),
            self._font_body,
            Q_TEXT,
        )
        scope = "整个桌面" if self.scope == "desktop" else self._scope_label()
        self._text(
            p,
            QRectF(card.left() + 30, card.top() + 196, card.width() - 60, 24),
            f"范围：{truncate_text(scope, 46)}",
            self._font_small,
            Q_MUTED,
        )
        self._text(
            p,
            QRectF(card.left() + 30, card.top() + 236, card.width() - 60, 24),
            "Accessibility 遍历可能需要 1–3 秒 · Esc 退出",
            self._font_small,
            Q_MUTED,
        )
        p.setPen(Qt.NoPen)
        for i in range(3):
            p.setBrush(Q_AQUA if i == 0 else Q_AQUA_DIM)
            p.drawEllipse(QRectF(card.center().x() - 20 + i * 18, card.top() + 290, 6, 6))

    def _paint_permission(self, p: QPainter) -> None:
        self.card_buttons = []
        card = self._paint_card_shell(p, Q_AMBER, height=440)
        self._text(
            p,
            QRectF(card.left(), card.top() + 92, card.width(), 40),
            "需要辅助功能权限",
            self._font_title,
            Q_AMBER,
        )
        body = (
            "macOS 需要允许本程序读取其他 App 的 UI 控件。\n"
            "请到“系统设置 → 隐私与安全性 → 辅助功能”，\n"
            "勾选启动本程序的 Terminal / iTerm / VS Code / Python，\n"
            "然后回到这里点击“重新检查”。\n\n"
            "点击“打开系统设置”会暂时隐藏覆盖层；授权后点击 Dock 中的\n"
            "Python 图标返回，覆盖层会自动恢复。\n\n"
            "不需要屏幕录制或输入监控权限。"
        )
        self._text(
            p,
            QRectF(card.left() + 44, card.top() + 146, card.width() - 88, 130),
            body,
            self._font_body,
            Q_TEXT,
            Qt.AlignLeft | Qt.TextWordWrap,
        )
        self._paint_buttons(
            p,
            [
                ("perm_settings", "打开系统设置"),
                ("perm_retry", "重新检查"),
                ("quit", "退出"),
            ],
            y=card.bottom() - 88,
            card=card,
        )
        self._text(
            p,
            QRectF(card.left(), card.bottom() - 42, card.width(), 20),
            "授权后按 R 或点击“重新检查”",
            self._font_small,
            Q_MUTED,
        )

    def _paint_error(self, p: QPainter) -> None:
        self.card_buttons = []
        card = self._paint_card_shell(p, Q_RED, height=420)
        self._text(
            p,
            QRectF(card.left(), card.top() + 88, card.width(), 38),
            "无法读取当前窗口控件",
            self._font_h1,
            Q_RED,
        )
        message = getattr(self, "_error_message", "请重试或扫描整个桌面。")
        self._text(
            p,
            QRectF(card.left() + 44, card.top() + 140, card.width() - 88, 100),
            truncate_text(message, 180),
            self._font_body,
            Q_TEXT,
            Qt.AlignLeft | Qt.TextWordWrap,
        )
        self._text(
            p,
            QRectF(card.left() + 44, card.top() + 250, card.width() - 88, 24),
            "提示：目标 App 不要最小化；部分 Electron/Java App 暴露的控件较少。",
            self._font_small,
            Q_MUTED,
            Qt.AlignLeft | Qt.TextWordWrap,
        )
        self._paint_buttons(
            p,
            [
                ("retry", "R  重试"),
                ("desktop", "D  扫描整个桌面"),
                ("quit", "Esc  退出"),
            ],
            y=card.bottom() - 74,
            card=card,
        )

    def _paint_victory(self, p: QPainter) -> None:
        self.card_buttons = []
        card = self._paint_card_shell(p, Q_GREEN, height=390)
        rng = random.Random(7)
        p.setPen(Qt.NoPen)
        for _ in range(28):
            x = rng.uniform(card.left() + 40, card.right() - 40)
            y = rng.uniform(card.top() + 50, card.bottom() - 50)
            color = rng.choice((Q_AQUA, Q_AMBER, Q_GREEN, Q_TEXT))
            size = rng.uniform(2, 5)
            p.setBrush(color)
            p.drawEllipse(QRectF(x, y, size, size))
        assert self.model is not None
        self._text(
            p,
            QRectF(card.left(), card.top() + 92, card.width(), 52),
            "落点命中！",
            self._font_title,
            Q_GREEN,
        )
        self._text(
            p,
            QRectF(card.left() + 20, card.top() + 154, card.width() - 40, 28),
            f"你落到了目标控件 · {platform_display_name(self.model.level.target)}",
            self._font_body_bold,
            Q_TEXT,
        )
        stats = (
            f"跳跃 {self.model.jumps} 次   ·   坠落 {self.model.falls} 次"
            f"   ·   剩余机会 {self.model.remaining_retries if self.model.limit_retries else '无限'}"
        )
        self._text(
            p,
            QRectF(card.left(), card.top() + 194, card.width(), 24),
            stats,
            self._font_small,
            Q_MUTED,
        )
        self._paint_buttons(
            p,
            [("restart", "再玩一局"), ("quit", "退出")],
            y=card.top() + 272,
            card=card,
        )
        self._text(
            p,
            QRectF(card.left() + 20, card.bottom() - 52, card.width() - 40, 22),
            "键盘焦点已释放；点击按钮继续，点击卡片后也可用 R / Esc。",
            self._font_small,
            Q_MUTED,
        )

    def _paint_game_over(self, p: QPainter) -> None:
        self.card_buttons = []
        card = self._paint_card_shell(p, Q_AMBER, height=380)
        assert self.model is not None
        self._text(
            p,
            QRectF(card.left(), card.top() + 98, card.width(), 50),
            "机会用尽",
            self._font_title,
            Q_AMBER,
        )
        self._text(
            p,
            QRectF(card.left(), card.top() + 162, card.width(), 28),
            f"本局跳跃 {self.model.jumps} 次，坠落 {self.model.falls} 次。",
            self._font_body,
            Q_TEXT,
        )
        self._paint_buttons(
            p,
            [("restart", "再玩一局"), ("quit", "退出")],
            y=card.top() + 264,
            card=card,
        )
        self._text(
            p,
            QRectF(card.left() + 20, card.bottom() - 50, card.width() - 40, 22),
            "键盘焦点已释放；点击按钮继续，点击卡片后也可用 R / Esc。",
            self._font_small,
            Q_MUTED,
        )


# =============================================================================
# macOS 启动入口
# =============================================================================


def _screen_rects(screen: Any) -> tuple[Rect, Rect]:
    geo = screen.geometry()
    avail = screen.availableGeometry()
    full = Rect(geo.x(), geo.y(), geo.x() + geo.width(), geo.y() + geo.height())
    usable = Rect(
        avail.x(),
        avail.y(),
        avail.x() + avail.width(),
        avail.y() + avail.height(),
    )
    return full, usable


def run_macos(args: Any) -> int:
    if sys.platform != "darwin":
        print("macOS 后端只能在 macOS 上运行。")
        return 2
    try:
        if not args.desktop:
            startup_countdown(max(0.0, min(30.0, float(args.delay))))

        target_app = get_frontmost_app()
        if args.desktop:
            scope = "desktop"
            if target_app is None:
                target_app = MacTargetApp(pid=0, name="整个桌面")
            print("范围：整个桌面", flush=True)
        else:
            scope = "window"
            if target_app is None:
                print("无法获取前台 App，请重试或使用 --desktop。")
                return 1
            window_title = target_app.window_title or "当前窗口"
            print(f"目标 App：{target_app.name} — {window_title}", flush=True)

        app = QApplication(sys.argv[:1])
        app.setApplicationName("Dango Jump")
        app.setApplicationDisplayName("Dango Jump")
        app.setQuitOnLastWindowClosed(True)

        primary = app.primaryScreen()
        if primary is None:
            print("无法读取主屏。")
            return 1
        full, usable = _screen_rects(primary)

        overlay = MacOverlay(
            full,
            usable,
            scope,
            target_app,
            limit_retries=bool(args.limit_retries),
            max_retries=max(1, int(args.max_retries)),
        )
        overlay.show()
        overlay.raise_()
        overlay._activate_self()
        return int(app.exec())
    except KeyboardInterrupt:
        print("\n已退出。")
        return 0
    except Exception as exc:
        traceback.print_exc()
        print(f"启动失败：{exc}")
        return 1
