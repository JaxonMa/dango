#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macOS AX 扫描的 Fake 节点测试（不需要辅助功能权限）。"""

from __future__ import annotations

import importlib.util
import sys
import time
import unittest

import dango_core as core

HAS_MACOS_AX = (
    sys.platform == "darwin"
    and importlib.util.find_spec("ApplicationServices") is not None
    and importlib.util.find_spec("AppKit") is not None
)

if HAS_MACOS_AX:
    import macos_ax


class FakeNode:
    def __init__(
        self,
        role: str = "AXGroup",
        frame: core.Rect | None = None,
        *,
        title: str = "",
        description: str = "",
        enabled: bool = True,
        hidden: bool = False,
        subrole: str = "",
        children: list["FakeNode"] | None = None,
    ) -> None:
        self.role = role
        self._frame = frame
        self.title = title
        self.description = description
        self.enabled = enabled
        self.hidden = hidden
        self.subrole = subrole
        self._children = children or []

    def attr(self, name: str, default=None):
        if name == "AXRole":
            return self.role
        if name == "AXSubrole":
            return self.subrole
        if name == "AXTitle":
            return self.title
        if name == "AXDescription":
            return self.description
        if name == "AXPlaceholderValue":
            return ""
        if name == "AXEnabled":
            return self.enabled
        if name == "AXHidden":
            return self.hidden
        return default

    def children(self):
        return list(self._children)

    def frame(self):
        return self._frame


@unittest.skipUnless(HAS_MACOS_AX, "macOS PyObjC not available")
class MacAXScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = core.Rect(0, 0, 1470, 956)

    def scan(self, node: FakeNode) -> core.ScanResult:
        now = time.monotonic()
        return macos_ax.scan_accessible_roots(
            [node],
            self.screen,
            own_pid=999999,
            deadline=now + 5,
            started=now,
        )

    def test_scans_button_and_text_field(self) -> None:
        root = FakeNode(
            children=[
                FakeNode("AXButton", core.Rect(100, 100, 220, 140), title="确定"),
                FakeNode("AXTextField", core.Rect(300, 100, 500, 140), title="搜索"),
                FakeNode("AXStaticText", core.Rect(100, 200, 300, 240), title="静态文本"),
            ]
        )
        result = self.scan(root)
        names = {(p.name, p.control_type) for p in result.platforms}
        self.assertEqual(
            names,
            {("确定", "AXButton"), ("搜索", "AXTextField")},
        )

    def test_small_disabled_hidden_and_secure_are_removed(self) -> None:
        root = FakeNode(
            children=[
                FakeNode("AXButton", core.Rect(100, 100, 110, 110), title="small"),
                FakeNode(
                    "AXButton",
                    core.Rect(200, 100, 300, 140),
                    title="disabled",
                    enabled=False,
                ),
                FakeNode(
                    "AXButton",
                    core.Rect(350, 100, 450, 140),
                    title="hidden",
                    hidden=True,
                ),
                FakeNode(
                    "AXTextField",
                    core.Rect(500, 100, 700, 140),
                    title="password",
                    subrole="AXSecureTextField",
                ),
                FakeNode("AXButton", core.Rect(750, 100, 850, 140), title="ok"),
            ]
        )
        result = self.scan(root)
        self.assertEqual([p.name for p in result.platforms], ["ok"])

    def test_offscreen_and_top_clipped_are_removed(self) -> None:
        root = FakeNode(
            children=[
                FakeNode("AXButton", core.Rect(1400, 100, 1600, 140), title="right_clip"),
                FakeNode("AXButton", core.Rect(100, -20, 220, 60), title="top_clip"),
                FakeNode("AXButton", core.Rect(300, 100, 400, 140), title="ok"),
            ]
        )
        result = self.scan(root)
        # 右边裁剪后仍有 70pt 宽，应保留；上边缘在屏幕外的应剔除。
        self.assertEqual({p.name for p in result.platforms}, {"right_clip", "ok"})

    def test_navigation_children_are_traversed(self) -> None:
        leaf = FakeNode("AXButton", core.Rect(300, 300, 400, 340), title="deep")
        mid = FakeNode("AXGroup", children=[leaf])
        root = FakeNode("AXWindow", children=[mid])
        result = self.scan(root)
        self.assertEqual([p.name for p in result.platforms], ["deep"])

    def test_timeout_raises_scan_error(self) -> None:
        root = FakeNode("AXWindow", children=[])
        with self.assertRaises(core.ScanError) as ctx:
            macos_ax.scan_accessible_roots(
                [root],
                self.screen,
                own_pid=1,
                deadline=time.monotonic() - 1,
            )
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_platform_is_live_detects_movement_disable_and_hide(self) -> None:
        node = FakeNode("AXButton", core.Rect(100, 100, 220, 140), title="ok")
        result = self.scan(FakeNode(children=[node]))
        self.assertEqual(len(result.platforms), 1)
        platform = result.platforms[0]
        platform.element = node
        self.assertTrue(macos_ax.platform_is_live_ax(platform))

        node._frame = core.Rect(500, 500, 620, 540)
        self.assertFalse(macos_ax.platform_is_live_ax(platform))

        node._frame = core.Rect(100, 100, 220, 140)
        node.enabled = False
        self.assertFalse(macos_ax.platform_is_live_ax(platform))

        node.enabled = True
        node.hidden = True
        self.assertFalse(macos_ax.platform_is_live_ax(platform))

    def test_accessible_roots_handles_duplicates(self) -> None:
        root = FakeNode(
            children=[
                FakeNode("AXButton", core.Rect(100, 100, 300, 180), title="outer"),
                FakeNode("AXButton", core.Rect(110, 110, 200, 150), title="inner"),
            ]
        )
        result = self.scan(root)
        self.assertEqual([p.name for p in result.platforms], ["inner"])


if __name__ == "__main__":
    unittest.main()
