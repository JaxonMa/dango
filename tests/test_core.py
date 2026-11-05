#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dango Jump 的纯逻辑单元测试（不依赖 Windows、Tk 或 Pillow）。"""

from __future__ import annotations

import random
import unittest

import dango_core as game
import windows_app


def make_platform(
    uid: int,
    rect: game.Rect,
    name: str = "控件",
    control_type: str = "ButtonControl",
) -> game.Platform:
    return game.Platform(
        uid=uid,
        rect=rect,
        source_rect=rect,
        name=name,
        control_type=control_type,
        element=None,
    )


def raw(
    rect: game.Rect,
    control_type: str = "ButtonControl",
    *,
    name: str = "控件",
    enabled: bool = True,
    offscreen: bool = False,
    pid: int = 1234,
) -> game.RawControl:
    return game.RawControl(
        rect=rect,
        source_rect=rect,
        name=name,
        control_type=control_type,
        enabled=enabled,
        offscreen=offscreen,
        process_id=pid,
    )


class JumpMathTests(unittest.TestCase):
    def test_charge_power_clamps(self) -> None:
        self.assertEqual(game.charge_power(-1.0), 0.0)
        self.assertAlmostEqual(game.charge_power(game.CHARGE_SECONDS / 2), 0.5)
        self.assertEqual(game.charge_power(game.CHARGE_SECONDS * 5), 1.0)

    def test_distance_mapping(self) -> None:
        self.assertEqual(game.jump_distance(0.0), game.JUMP_DISTANCE_MIN)
        self.assertEqual(game.jump_distance(1.0), game.JUMP_DISTANCE_MAX)
        self.assertGreater(game.jump_distance(0.75), game.jump_distance(0.25))
        self.assertEqual(game.jump_distance(-5.0), game.JUMP_DISTANCE_MIN)
        self.assertEqual(game.jump_distance(5.0), game.JUMP_DISTANCE_MAX)

    def test_initial_velocity_direction(self) -> None:
        vx_right, vy = game.initial_velocity(0.5, 1)
        vx_left, vy_left = game.initial_velocity(0.5, -1)
        self.assertGreater(vx_right, 0)
        self.assertLess(vx_left, 0)
        self.assertAlmostEqual(abs(vx_right), abs(vx_left))
        self.assertLess(vy, 0)
        self.assertAlmostEqual(vy, vy_left)
        self.assertAlmostEqual(vy, -game.GRAVITY * game.JUMP_FLIGHT_SECONDS / 2)

    def test_trajectory_rises_then_falls(self) -> None:
        points = game.trajectory_points(100, 500, 0.5, 1, duration=0.9, samples=20)
        ys = [p[1] for p in points]
        self.assertLess(min(ys), 500)
        self.assertGreater(ys[-1], min(ys))

    def test_jump_apex_positive(self) -> None:
        self.assertGreater(game.jump_apex(), 0)


class FilterAndLevelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = game.Rect(0, 0, 1920, 1080)

    def test_filter_removes_small_offscreen_disabled_and_wrong_type(self) -> None:
        controls = [
            raw(game.Rect(100, 100, 200, 140), name="normal"),
            raw(game.Rect(300, 100, 310, 110), name="too_small"),
            raw(game.Rect(1915, 100, 2000, 140), name="partly_offscreen"),
            raw(game.Rect(400, 100, 500, 140), enabled=False, name="disabled"),
            raw(game.Rect(500, 100, 600, 140), offscreen=True, name="offscreen"),
            raw(game.Rect(600, 100, 700, 140), control_type="TextControl", name="text"),
            raw(game.Rect(800, 100, 900, 140), pid=999999, name="normal2"),
        ]
        result = game.filter_platforms(controls, self.screen, own_pid=999999)
        names = {p.name for p in result}
        self.assertEqual(names, {"normal"})

    def test_filter_keeps_visible_clipped_control(self) -> None:
        controls = [raw(game.Rect(50, 50, 150, 100), name="a"), raw(game.Rect(1800, 0, 2000, 80), name="b")]
        result = game.filter_platforms(controls, self.screen, own_pid=42)
        self.assertEqual(len(result), 2)
        clipped_b = next(p for p in result if p.name == "b")
        self.assertEqual(clipped_b.rect.right, 1920)

    def test_filter_rejects_control_whose_top_is_offscreen(self) -> None:
        controls = [raw(game.Rect(100, -20, 220, 60), name="top_clipped")]
        result = game.filter_platforms(controls, self.screen, own_pid=42)
        self.assertEqual(result, [])

    def test_filter_deduplicates_nested_controls(self) -> None:
        outer = raw(game.Rect(100, 100, 340, 200), name="outer")
        inner = raw(game.Rect(120, 120, 220, 160), name="inner")
        result = game.filter_platforms([outer, inner], self.screen, own_pid=42)
        self.assertEqual([p.name for p in result], ["inner"])

    def test_filter_assigns_unique_uids(self) -> None:
        controls = [
            raw(game.Rect(100, 100, 200, 140), name="a"),
            raw(game.Rect(300, 100, 400, 140), name="b"),
        ]
        result = game.filter_platforms(controls, self.screen, own_pid=42)
        self.assertEqual(len({p.uid for p in result}), 2)

    def test_generate_level_requires_two(self) -> None:
        only = make_platform(0, game.Rect(100, 100, 200, 140))
        with self.assertRaises(game.LevelError):
            game.generate_level([only])

    def test_generate_level_uses_far_start(self) -> None:
        left = make_platform(0, game.Rect(100, 500, 200, 540), "left")
        right = make_platform(1, game.Rect(900, 500, 1000, 540), "right")
        level = game.generate_level([left, right], rng=random.Random(1))
        self.assertNotEqual(level.start.uid, level.target.uid)
        self.assertGreaterEqual(abs(level.start.center_x - level.target.center_x), game.START_DISTANCE_THRESHOLD)

    def test_generate_level_relaxes_distance_threshold(self) -> None:
        left = make_platform(0, game.Rect(100, 500, 200, 540), "left")
        right = make_platform(1, game.Rect(250, 500, 350, 540), "right")
        level = game.generate_level([left, right], rng=random.Random(2))
        self.assertNotEqual(level.start.uid, level.target.uid)
        self.assertLess(abs(level.start.center_x - level.target.center_x), game.START_DISTANCE_THRESHOLD)

    def test_generate_level_rejects_unreachable_layout(self) -> None:
        far = [
            make_platform(0, game.Rect(0, 500, 100, 540)),
            make_platform(1, game.Rect(1500, 500, 1600, 540)),
            make_platform(2, game.Rect(3000, 500, 3100, 540)),
        ]
        with self.assertRaises(game.LevelError):
            game.generate_level(far, rng=random.Random(3))

    def test_direct_reach_rejects_too_high_target(self) -> None:
        low = make_platform(0, game.Rect(100, 700, 200, 740))
        high = make_platform(1, game.Rect(150, 100, 250, 140))
        self.assertFalse(game.can_directly_reach(low, high))
        self.assertTrue(game.can_directly_reach(high, low))

    def test_direct_reach_rejects_too_far(self) -> None:
        left = make_platform(0, game.Rect(0, 500, 100, 540))
        right = make_platform(1, game.Rect(1800, 500, 1900, 540))
        self.assertFalse(game.can_directly_reach(left, right))


class CollisionTests(unittest.TestCase):
    def test_find_landing_picks_first_top_edge(self) -> None:
        high = make_platform(0, game.Rect(50, 140, 250, 180))
        low = make_platform(1, game.Rect(50, 200, 250, 240))
        hit = game.find_landing(100, 100, 100, 260, [low, high])
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.platform.uid, 0)
        self.assertAlmostEqual(hit.top, 140)

    def test_find_landing_ignores_upward_motion(self) -> None:
        platform = make_platform(0, game.Rect(50, 140, 250, 180))
        hit = game.find_landing(100, 200, 100, 100, [platform])
        self.assertIsNone(hit)

    def test_find_landing_requires_horizontal_overlap(self) -> None:
        platform = make_platform(0, game.Rect(300, 140, 400, 180))
        hit = game.find_landing(100, 100, 100, 200, [platform])
        self.assertIsNone(hit)

    def test_find_landing_catches_fast_tunneling(self) -> None:
        platform = make_platform(0, game.Rect(0, 500, 400, 520))
        # 一帧从 200 掉到 900，仍应在这块控件上边缘截住。
        hit = game.find_landing(100, 200, 100, 900, [platform])
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.top, 500)


class GameModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = game.Rect(0, 0, 1920, 1080)
        self.start = make_platform(0, game.Rect(100, 500, 200, 540), "start")
        self.target = make_platform(1, game.Rect(400, 500, 500, 540), "target")
        self.other = make_platform(2, game.Rect(250, 500, 350, 540), "other")
        self.level = game.Level(self.start, self.target, [self.start, self.other, self.target])

    def test_initial_state_on_start(self) -> None:
        model = game.GameModel(self.level, self.screen)
        self.assertEqual(model.state, game.STATE_READY)
        self.assertEqual(model.current.uid, self.start.uid)
        self.assertEqual(model.char_feet, self.start.rect.top)
        self.assertAlmostEqual(model.char_x, self.start.rect.center_x)
        self.assertEqual(model.direction, 1)

    def test_force_land_on_other_platform_is_not_victory(self) -> None:
        model = game.GameModel(self.level, self.screen)
        model.force_land_for_test(self.other)
        self.assertEqual(model.state, game.STATE_READY)
        self.assertEqual(model.current.uid, self.other.uid)

    def test_force_land_on_target_wins(self) -> None:
        model = game.GameModel(self.level, self.screen)
        model.force_land_for_test(self.target)
        self.assertEqual(model.state, game.STATE_VICTORY)
        self.assertEqual(model.current.uid, self.target.uid)

    def test_direction_and_charge_state(self) -> None:
        model = game.GameModel(self.level, self.screen)
        self.assertTrue(model.set_direction(-1))
        self.assertEqual(model.direction, -1)
        now = 100.0
        self.assertTrue(model.begin_charge(now))
        model.update_charge(now + game.CHARGE_SECONDS / 2)
        self.assertAlmostEqual(model.power, 0.5)
        self.assertFalse(model.begin_charge(now))
        self.assertTrue(model.release(now + game.CHARGE_SECONDS))
        self.assertEqual(model.state, game.STATE_AIRBORNE)
        self.assertEqual(model.jumps, 1)

    def test_short_jump_lands_on_intermediate_control(self) -> None:
        # 起点中心 x=150，中间控件中心 x=300，距离 150px。
        power = (150.0 - game.JUMP_DISTANCE_MIN) / (
            game.JUMP_DISTANCE_MAX - game.JUMP_DISTANCE_MIN
        )
        model = game.GameModel(self.level, self.screen)
        now = 10.0
        model.begin_charge(now)
        model.charge_started = now - power * game.CHARGE_SECONDS
        model.release(now)
        for _ in range(240):
            model.tick(1 / 60)
            if model.state != game.STATE_AIRBORNE:
                break
        self.assertEqual(model.state, game.STATE_READY)
        self.assertEqual(model.current.uid, self.other.uid)

    def test_exact_jump_lands_on_target_and_wins(self) -> None:
        # 起点中心 x=150，目标中心 x=450，距离 300px。
        power = (300.0 - game.JUMP_DISTANCE_MIN) / (
            game.JUMP_DISTANCE_MAX - game.JUMP_DISTANCE_MIN
        )
        model = game.GameModel(self.level, self.screen)
        now = 10.0
        model.begin_charge(now)
        model.charge_started = now - power * game.CHARGE_SECONDS
        model.release(now)
        for _ in range(240):
            model.tick(1 / 60)
            if model.state != game.STATE_AIRBORNE:
                break
        self.assertEqual(model.state, game.STATE_VICTORY)
        self.assertEqual(model.current.uid, self.target.uid)

    def test_fall_resets_to_start_and_counts(self) -> None:
        model = game.GameModel(self.level, self.screen)
        model.state = game.STATE_AIRBORNE
        model.missed = True
        model.char_feet = self.screen.bottom + game.FALL_MARGIN + 10
        model.flight_time = 1.0
        events = model.tick(0.016)
        self.assertTrue(events.fell)
        self.assertEqual(model.falls, 1)
        self.assertEqual(model.state, game.STATE_READY)
        self.assertEqual(model.current.uid, self.start.uid)
        self.assertAlmostEqual(model.char_feet, self.start.rect.top)

    def test_limited_retries_go_to_game_over(self) -> None:
        model = game.GameModel(
            self.level,
            self.screen,
            limit_retries=True,
            max_retries=2,
        )
        for expected_falls in (1, 2):
            model.state = game.STATE_AIRBORNE
            model.missed = True
            model.char_feet = self.screen.bottom + game.FALL_MARGIN + 10
            model.flight_time = 1.0
            events = model.tick(0.016)
            self.assertTrue(events.fell)
            self.assertEqual(model.falls, expected_falls)
        self.assertTrue(events.game_over)
        self.assertEqual(model.state, game.STATE_GAME_OVER)
        self.assertEqual(model.remaining_retries, 0)

    def test_load_level_preserves_stats_when_requested(self) -> None:
        model = game.GameModel(self.level, self.screen)
        model.jumps = 7
        model.falls = 2
        new_level = game.Level(self.other, self.start, [self.start, self.other, self.target])
        model.load_level(new_level, preserve_stats=True)
        self.assertEqual(model.jumps, 7)
        self.assertEqual(model.falls, 2)
        self.assertEqual(model.current.uid, self.other.uid)


class RectAndMonitorTests(unittest.TestCase):
    def test_rect_overlap_ratio(self) -> None:
        a = game.Rect(0, 0, 100, 100)
        b = game.Rect(50, 0, 150, 100)
        self.assertAlmostEqual(a.overlap_ratio(b), 0.5)
        self.assertEqual(a.intersect(b), game.Rect(50, 0, 100, 100))

    def test_rects_differ_tolerance(self) -> None:
        a = game.Rect(0, 0, 100, 100)
        self.assertFalse(game.rects_differ(a, game.Rect(3, 0, 103, 100), 6))
        self.assertTrue(game.rects_differ(a, game.Rect(20, 0, 120, 100), 6))

    def test_platform_is_live_without_element(self) -> None:
        platform = make_platform(0, game.Rect(0, 0, 100, 100))
        self.assertTrue(windows_app.platform_is_live(platform))

    def test_platform_is_live_detects_missing_element(self) -> None:
        class FakeRect:
            left = 0
            top = 0
            right = 100
            bottom = 100

        class FakeElement:
            IsOffscreen = False
            BoundingRectangle = FakeRect()

        platform = make_platform(0, game.Rect(0, 0, 100, 100))
        platform.element = FakeElement()
        self.assertTrue(windows_app.platform_is_live(platform))

        class MovedElement(FakeElement):
            class BoundingRectangle:
                left = 500
                top = 500
                right = 600
                bottom = 600

        platform.element = MovedElement()
        self.assertFalse(windows_app.platform_is_live(platform))


if __name__ == "__main__":
    unittest.main()
