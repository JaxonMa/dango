#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macos_ax — macOS 辅助功能（Accessibility）控件读取。

依赖 PyObjC 的 ApplicationServices/AppKit。所有坐标均为 macOS 主屏
左上角原点的逻辑点（points），与 PySide6 的 QScreen.geometry() 一致。
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import AppKit
import ApplicationServices as AS

from dango_core import (
    MAC_CANDIDATE_CONTROL_TYPES,
    MONITOR_INTERVAL_MS,
    RawControl,
    Rect,
    ScanError,
    ScanResult,
    TARGET_MOVE_TOLERANCE,
    filter_platforms,
    rects_differ,
)

# AX 遍历参数
AX_SCAN_TIMEOUT_SECONDS = 2.5
AX_SCAN_DESKTOP_TIMEOUT_SECONDS = 3.5
AX_SCAN_MAX_DEPTH = 20
AX_SCAN_MAX_NODES = 5000

# 这些角色表示容器/静态内容，不作为落脚控件。
AX_SECURE_SUBROLE = "AXSecureTextField"

AX_ACCESSIBILITY_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)


@dataclass
class MacTargetApp:
    """启动时捕获的前台 App 与窗口。"""

    pid: int
    name: str
    bundle_id: str = ""
    window_title: str = ""
    app_element: Any = field(default=None, repr=False, compare=False)
    window_element: Any = field(default=None, repr=False, compare=False)


class AXNode:
    """轻量包装 AXUIElement，降低调用失败时的异常噪音。"""

    __slots__ = ("element",)

    def __init__(self, element: Any) -> None:
        self.element = element

    def attr(self, name: str, default: Any = None) -> Any:
        try:
            err, value = AS.AXUIElementCopyAttributeValue(self.element, name, None)
            return value if err == 0 else default
        except Exception:
            return default

    def children(self) -> list["AXNode"]:
        value = self.attr(AS.kAXChildrenAttribute, None)
        if not value:
            return []
        result: list[AXNode] = []
        try:
            for child in value:
                if child is not None:
                    result.append(AXNode(child))
        except Exception:
            return []
        return result

    def frame(self) -> Optional[Rect]:
        pos = self.attr(AS.kAXPositionAttribute, None)
        size = self.attr(AS.kAXSizeAttribute, None)
        if pos is None or size is None:
            return None
        try:
            ok_pos, point = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
            ok_size, cg_size = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
        except Exception:
            return None
        if not ok_pos or not ok_size:
            return None
        try:
            left = int(round(float(point.x)))
            top = int(round(float(point.y)))
            right = left + int(round(float(cg_size.width)))
            bottom = top + int(round(float(cg_size.height)))
        except Exception:
            return None
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right, bottom)


# 给测试/类型检查用的最小协议（鸭子类型）
AXNodeLike = Any


# =============================================================================
# 权限与 App 捕获
# =============================================================================


def accessibility_trusted(prompt: bool = False) -> bool:
    """返回当前进程是否有辅助功能权限。"""
    try:
        if prompt:
            return bool(
                AS.AXIsProcessTrustedWithOptions(
                    {AS.kAXTrustedCheckOptionPrompt: True}
                )
            )
        return bool(AS.AXIsProcessTrusted())
    except Exception:
        return False


def open_accessibility_settings() -> bool:
    try:
        url = AppKit.NSURL.URLWithString_(AX_ACCESSIBILITY_URL)
        return bool(AppKit.NSWorkspace.sharedWorkspace().openURL_(url))
    except Exception:
        return False


def _ax_attr(element: Any, attr: str, default: Any = None) -> Any:
    try:
        err, value = AS.AXUIElementCopyAttributeValue(element, attr, None)
        return value if err == 0 else default
    except Exception:
        return default


def _app_element(pid: int) -> Any:
    return AS.AXUIElementCreateApplication(int(pid))


def _window_title(window: Any) -> str:
    if window is None:
        return ""
    value = _ax_attr(window, AS.kAXTitleAttribute, "")
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _windows_for_app(app_element: Any) -> list[Any]:
    value = _ax_attr(app_element, AS.kAXWindowsAttribute, None)
    if not value:
        return []
    result: list[Any] = []
    try:
        for item in value:
            if item is not None:
                result.append(item)
    except Exception:
        return []
    return result


def _focused_window(app_element: Any) -> Any:
    window = _ax_attr(app_element, AS.kAXFocusedWindowAttribute, None)
    if window is not None:
        return window
    windows = _windows_for_app(app_element)
    return windows[0] if windows else None


def get_frontmost_app() -> Optional[MacTargetApp]:
    """捕获当前前台 App。无权限时仍能拿到 App 元数据，但窗口为空。"""
    try:
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        return None
    if app is None:
        return None
    try:
        pid = int(app.processIdentifier())
        name = str(app.localizedName() or "未命名 App")
        bundle = str(app.bundleIdentifier() or "")
    except Exception:
        return None
    target = MacTargetApp(pid=pid, name=name, bundle_id=bundle)
    refresh_target_window(target)
    return target


def refresh_target_window(target: MacTargetApp) -> None:
    """补齐/刷新目标 App 的 AX window；需要辅助功能权限。"""
    if not accessibility_trusted():
        return
    try:
        app_element = target.app_element or _app_element(target.pid)
        target.app_element = app_element
        window = _focused_window(app_element)
        if window is None:
            windows = _windows_for_app(app_element)
            window = windows[0] if windows else None
        target.window_element = window
        title = _window_title(window)
        if title:
            target.window_title = title
    except Exception:
        target.window_element = None


def running_app(pid: int) -> Any:
    try:
        return AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
    except Exception:
        return None


def activate_app(pid: int) -> bool:
    """把键盘焦点还给目标 App。"""
    app = running_app(pid)
    if app is None:
        return False
    try:
        return bool(
            app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        )
    except Exception:
        return False


def activate_current_app() -> bool:
    """激活当前 Python/Qt 进程（用于覆盖层重新获取键盘）。"""
    try:
        app = AppKit.NSRunningApplication.currentApplication()
        return bool(
            app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        )
    except Exception:
        return False


def raise_window(window_element: Any) -> None:
    if window_element is None:
        return
    try:
        AS.AXUIElementPerformAction(window_element, AS.kAXRaiseAction)
    except Exception:
        pass


def running_regular_apps(exclude_pid: int) -> list[MacTargetApp]:
    """列出桌面扫描候选 App（regular 且未隐藏）。"""
    result: list[MacTargetApp] = []
    try:
        apps = AppKit.NSWorkspace.sharedWorkspace().runningApplications()
    except Exception:
        return result
    for app in apps:
        try:
            pid = int(app.processIdentifier())
            if pid <= 0 or pid == exclude_pid:
                continue
            if int(app.activationPolicy()) != int(AppKit.NSApplicationActivationPolicyRegular):
                continue
            if bool(app.isHidden()):
                continue
            result.append(
                MacTargetApp(
                    pid=pid,
                    name=str(app.localizedName() or "未命名 App"),
                    bundle_id=str(app.bundleIdentifier() or ""),
                )
            )
        except Exception:
            continue
    return result


# =============================================================================
# AX 树扫描
# =============================================================================


def _read_frame(node: AXNodeLike) -> Optional[Rect]:
    try:
        return node.frame()
    except Exception:
        return None


def _node_children(node: AXNodeLike) -> list[AXNodeLike]:
    try:
        children = node.children()
        return list(children) if children else []
    except Exception:
        return []


def _node_name(node: AXNodeLike) -> str:
    for attr in (
        AS.kAXTitleAttribute,
        AS.kAXDescriptionAttribute,
        AS.kAXPlaceholderValueAttribute,
    ):
        try:
            value = node.attr(attr, None)
        except Exception:
            value = None
        if value:
            text = str(value).strip()
            if text:
                return text
    return ""


def _node_enabled(node: AXNodeLike) -> bool:
    try:
        value = node.attr(AS.kAXEnabledAttribute, None)
    except Exception:
        value = None
    if value is None:
        return True
    try:
        return bool(value)
    except Exception:
        return True


def _node_hidden(node: AXNodeLike) -> bool:
    try:
        value = node.attr("AXHidden", None)
    except Exception:
        value = None
    if value is None:
        return False
    try:
        return bool(value)
    except Exception:
        return False


def _node_subrole(node: AXNodeLike) -> str:
    try:
        value = node.attr(AS.kAXSubroleAttribute, "")
        return str(value or "")
    except Exception:
        return ""


def scan_accessible_roots(
    roots: Sequence[AXNodeLike],
    screen: Rect,
    *,
    own_pid: int,
    deadline: float,
    max_depth: int = AX_SCAN_MAX_DEPTH,
    max_nodes: int = AX_SCAN_MAX_NODES,
    started: Optional[float] = None,
) -> ScanResult:
    """遍历给定 AX 根节点，返回过滤后的平台。

    该函数只依赖 node.attr()/children()/frame()，因此可用 Fake 节点测试。
    """
    started = time.monotonic() if started is None else started
    raw_controls: list[RawControl] = []
    nodes_seen = 0
    skipped = 0

    for root in roots:
        queue: deque[tuple[AXNodeLike, int]] = deque([(root, 0)])
        while queue:
            if time.monotonic() > deadline:
                raise ScanError(
                    f"读取 macOS 控件超时（超过 {AX_SCAN_TIMEOUT_SECONDS:.1f} 秒），"
                    "请重试或按 D 扫描整个桌面。",
                    kind="timeout",
                )
            node, depth = queue.popleft()
            nodes_seen += 1
            if nodes_seen > max_nodes:
                break

            role = ""
            try:
                role = str(node.attr(AS.kAXRoleAttribute, "") or "")
            except Exception:
                skipped += 1

            if role in MAC_CANDIDATE_CONTROL_TYPES:
                subrole = _node_subrole(node)
                if subrole == AX_SECURE_SUBROLE or role == "AXSecureTextField":
                    skipped += 1
                else:
                    frame = _read_frame(node)
                    if frame is not None:
                        enabled = _node_enabled(node)
                        hidden = _node_hidden(node)
                        name = _node_name(node)
                        raw_controls.append(
                            RawControl(
                                rect=frame,
                                source_rect=frame,
                                name=name,
                                control_type=role,
                                enabled=enabled,
                                offscreen=hidden,
                                process_id=0,
                                element=node,
                            )
                        )
                    else:
                        skipped += 1

            if depth < max_depth:
                for child in _node_children(node):
                    queue.append((child, depth + 1))

    elapsed = time.monotonic() - started
    platforms = filter_platforms(
        raw_controls,
        screen,
        own_pid=own_pid,
        allowed_types=MAC_CANDIDATE_CONTROL_TYPES,
    )
    return ScanResult(
        platforms=platforms,
        nodes_seen=nodes_seen,
        skipped=skipped,
        elapsed=elapsed,
    )


def _scan_roots(
    roots: Sequence[AXNodeLike],
    screen: Rect,
    *,
    own_pid: int,
    timeout: float,
    title: str = "",
) -> ScanResult:
    started = time.monotonic()
    result = scan_accessible_roots(
        roots,
        screen,
        own_pid=own_pid,
        deadline=started + timeout,
        started=started,
    )
    if len(result.platforms) < 2:
        area = title or "当前范围"
        raise ScanError(
            f"{area}可用控件不足（找到 {len(result.platforms)} 个）。"
            "请换一个控件更多的窗口，或按 D 扫描整个桌面。",
            kind="insufficient",
        )
    return result


# =============================================================================
# 前台窗口 / 桌面扫描入口
# =============================================================================


def scan_target(
    target: MacTargetApp,
    screen: Rect,
    *,
    own_pid: Optional[int] = None,
    timeout: float = AX_SCAN_TIMEOUT_SECONDS,
) -> ScanResult:
    """扫描启动时捕获的前台窗口。"""
    if not accessibility_trusted():
        raise ScanError(
            "需要 macOS 辅助功能权限才能读取控件。\n"
            "请在“系统设置 → 隐私与安全性 → 辅助功能”中允许 "
            "Terminal / iTerm / VS Code（或启动本程序的 App），然后重试。",
            kind="permission",
        )
    own_pid = os.getpid() if own_pid is None else own_pid
    if target.window_element is None:
        refresh_target_window(target)
    window = target.window_element
    if window is None:
        raise ScanError(
            "无法读取前台窗口。请确认目标 App 未最小化、未退出，然后重试。",
            kind="window",
        )
    title = target.window_title or target.name
    roots = [AXNode(window)]
    return _scan_roots(roots, screen, own_pid=own_pid, timeout=timeout, title=title)


def scan_desktop(
    screen: Rect,
    *,
    own_pid: Optional[int] = None,
    timeout: float = AX_SCAN_DESKTOP_TIMEOUT_SECONDS,
) -> ScanResult:
    """扫描整个桌面上的 regular App 窗口。"""
    if not accessibility_trusted():
        raise ScanError(
            "需要 macOS 辅助功能权限才能读取控件。\n"
            "请在“系统设置 → 隐私与安全性 → 辅助功能”中允许启动本程序的 App，然后重试。",
            kind="permission",
        )
    own_pid = os.getpid() if own_pid is None else own_pid
    roots: list[AXNode] = []
    for app in running_regular_apps(own_pid):
        try:
            app_element = _app_element(app.pid)
            for window in _windows_for_app(app_element):
                roots.append(AXNode(window))
        except Exception:
            continue
    if not roots:
        raise ScanError("没有找到可读取的 App 窗口。", kind="insufficient")
    return _scan_roots(
        roots,
        screen,
        own_pid=own_pid,
        timeout=timeout,
        title="整个桌面",
    )


def platform_is_live_ax(
    platform: Any, tolerance: int = TARGET_MOVE_TOLERANCE
) -> bool:
    """检查 AX 控件是否仍存在、未移动、未被隐藏。"""
    node = getattr(platform, "element", None)
    if node is None:
        return True
    try:
        if _node_hidden(node):
            return False
        if not _node_enabled(node):
            return False
        frame = _read_frame(node)
        if frame is None:
            return False
        return not rects_differ(platform.source_rect, frame, tolerance)
    except Exception:
        return False


__all__ = [
    "AX_SCAN_TIMEOUT_SECONDS",
    "MacTargetApp",
    "AXNode",
    "accessibility_trusted",
    "open_accessibility_settings",
    "get_frontmost_app",
    "refresh_target_window",
    "activate_app",
    "activate_current_app",
    "raise_window",
    "scan_target",
    "scan_desktop",
    "scan_accessible_roots",
    "platform_is_live_ax",
    "MONITOR_INTERVAL_MS",
]
