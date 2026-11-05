#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dango Circuit 的纯逻辑单元测试（不依赖 Windows、Tk、Qt 或 PyObjC）。"""

from __future__ import annotations

import random
import unittest

import dango_core as game


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


def make_course(rects: list[game.Rect], star_uids: set[int] | None = None) -> game.Course:
    platforms = [make_platform(i, rect, f"p{i}") for i, rect in enumerate(rects)]
    stars = [p for p in platforms if star_uids and p.uid in star_uids]
    return game.Course(
        start=platforms[0],
        checkpoints=platforms[1:],
        bonus_stars=stars,
        platforms=platforms,
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


class FilterAndCourseTests(unittest.TestCase):
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
        self.assertEqual({p.name for p in result}, {"normal"})

    def test_filter_keeps_visible_clipped_control(self) -> None:
        controls = [
            raw(game.Rect(50, 50, 150, 100), name="a"),
            raw(game.Rect(1800, 0, 2000, 80), name="b"),
        ]
        result = game.filter_platforms(controls, self.screen, own_pid=42)
        self.assertEqual(len(result), 2)
        self.assertEqual(next(p for p in result if p.name == "b").rect.right, 1920)

    def test_filter_deduplicates_nested_controls(self) -> None:
        controls = [
            raw(game.Rect(100, 100, 340, 200), name="outer"),
            raw(game.Rect(120, 120, 220, 160), name="inner"),
        ]
        result = game.filter_platforms(controls, self.screen, own_pid=42)
        self.assertEqual([p.name for p in result], ["inner"])

    def test_filter_allowed_types_for_macos(self) -> None:
        controls = [
            raw(game.Rect(100, 100, 200, 140), control_type="AXButton", name="mac"),
            raw(game.Rect(300, 100, 400, 140), control_type="ButtonControl", name="win"),
        ]
        result = game.filter_platforms(
            controls,
            self.screen,
            own_pid=42,
            allowed_types={"AXButton"},
        )
        self.assertEqual([p.name for p in result], ["mac"])

    def test_generate_course_has_reachable_distinct_checkpoints(self) -> None:
        rects = [
            game.Rect(100, 250, 220, 290),
            game.Rect(450, 300, 580, 340),
            game.Rect(850, 220, 980, 260),
            game.Rect(1200, 380, 1340, 420),
            game.Rect(700, 500, 840, 540),
            game.Rect(300, 650, 430, 690),
            game.Rect(1450, 650, 1580, 690),
            game.Rect(1000, 780, 1130, 820),
        ]
        platforms = [make_platform(i, r, f"p{i}") for i, r in enumerate(rects)]
        course = game.generate_course(
            platforms,
            game.RunConfig(checkpoint_count=3, star_count=2, seed=1),
        )
        self.assertGreaterEqual(len(course.checkpoints), 2)
        self.assertLessEqual(len(course.checkpoints), 3)
        ids = [course.start.uid] + [p.uid for p in course.checkpoints]
        self.assertEqual(len(ids), len(set(ids)))
        chain = [course.start] + course.checkpoints
        for left, right in zip(chain, chain[1:]):
            self.assertTrue(game.can_directly_reach(left, right))
        star_ids = {p.uid for p in course.bonus_stars}
        self.assertTrue(star_ids.isdisjoint(ids))
        self.assertLessEqual(len(star_ids), 2)

    def test_generate_course_requires_enough_controls(self) -> None:
        only_two = [
            make_platform(0, game.Rect(100, 100, 200, 140)),
            make_platform(1, game.Rect(300, 100, 400, 140)),
        ]
        with self.assertRaises(game.LevelError):
            game.generate_course(only_two)

    def test_generate_course_shortens_when_requested_too_long(self) -> None:
        rects = [
            game.Rect(100, 200, 200, 240),
            game.Rect(400, 260, 500, 300),
            game.Rect(700, 320, 800, 360),
            game.Rect(1000, 380, 1100, 420),
        ]
        platforms = [make_platform(i, r, f"p{i}") for i, r in enumerate(rects)]
        course = game.generate_course(
            platforms,
            game.RunConfig(checkpoint_count=7, star_count=0, seed=2),
        )
        self.assertTrue(course.shortened)
        self.assertGreaterEqual(len(course.checkpoints), 2)
        self.assertLess(len(course.checkpoints), 7)

    def test_generate_course_rejects_unreachable_layout(self) -> None:
        platforms = [
            make_platform(0, game.Rect(0, 500, 100, 540)),
            make_platform(1, game.Rect(1500, 500, 1600, 540)),
            make_platform(2, game.Rect(3000, 500, 3100, 540)),
        ]
        with self.assertRaises(game.LevelError):
            game.generate_course(platforms)

    def test_generate_remaining_checkpoints(self) -> None:
        rects = [
            game.Rect(100, 200, 200, 240),
            game.Rect(400, 260, 500, 300),
            game.Rect(700, 320, 800, 360),
            game.Rect(1000, 380, 1100, 420),
            game.Rect(300, 500, 400, 540),
        ]
        platforms = [make_platform(i, r, f"p{i}") for i, r in enumerate(rects)]
        checkpoints = game.generate_remaining_checkpoints(
            platforms,
            platforms[0],
            2,
            random.Random(4),
            excluded_uids={platforms[0].uid},
        )
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(len({p.uid for p in checkpoints}), 2)
        self.assertNotIn(platforms[0].uid, {p.uid for p in checkpoints})
        self.assertTrue(game.can_directly_reach(platforms[0], checkpoints[0]))
        self.assertTrue(game.can_directly_reach(checkpoints[0], checkpoints[1]))

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
        self.assertIsNone(game.find_landing(100, 200, 100, 100, [platform]))

    def test_find_landing_requires_horizontal_overlap(self) -> None:
        platform = make_platform(0, game.Rect(300, 140, 400, 180))
        self.assertIsNone(game.find_landing(100, 100, 100, 200, [platform]))

    def test_find_landing_catches_fast_tunneling(self) -> None:
        platform = make_platform(0, game.Rect(0, 500, 400, 520))
        hit = game.find_landing(100, 200, 100, 900, [platform])
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.top, 500)


class GameModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = game.Rect(0, 0, 1920, 1080)
        self.start = make_platform(0, game.Rect(100, 500, 200, 540), "start")
        self.cp1 = make_platform(1, game.Rect(400, 500, 500, 540), "cp1")
        self.cp2 = make_platform(2, game.Rect(700, 500, 800, 540), "cp2")
        self.other = make_platform(3, game.Rect(250, 650, 350, 690), "other")
        self.star = make_platform(4, game.Rect(550, 650, 650, 690), "star")
        self.course = game.Course(
            start=self.start,
            checkpoints=[self.cp1, self.cp2],
            bonus_stars=[self.star],
            platforms=[self.start, self.cp1, self.cp2, self.other, self.star],
        )

    def model(self, **kwargs) -> game.GameModel:
        return game.GameModel(self.course, self.screen, game.RunConfig(**kwargs))

    def test_initial_state(self) -> None:
        model = self.model()
        self.assertEqual(model.state, game.STATE_READY)
        self.assertEqual(model.current.uid, self.start.uid)
        self.assertEqual(model.next_target.uid, self.cp1.uid)
        self.assertEqual(model.hearts, game.START_HEARTS)
        self.assertEqual(model.score, 0)
        self.assertEqual(model.snapshot().checkpoint_count, 2)

    def test_checkpoint_advance_scores_and_updates_target(self) -> None:
        model = self.model()
        events = model.force_land_for_test(self.cp1, self.cp1.rect.center_x)
        self.assertEqual(model.state, game.STATE_READY)
        self.assertEqual(model.checkpoint_index, 1)
        self.assertEqual(model.next_target.uid, self.cp2.uid)
        self.assertIsNotNone(events.checkpoint)
        assert events.checkpoint is not None
        self.assertEqual(events.checkpoint.uid, self.cp1.uid)
        self.assertTrue(events.perfect)
        self.assertTrue(events.direct)
        self.assertGreater(events.score_delta, 0)
        self.assertEqual(model.combo, 1)
        self.assertAlmostEqual(model.combo_multiplier, 1.0)
        self.assertEqual(model.respawn_platform.uid, self.cp1.uid)

    def test_star_is_optional_and_only_collected_once(self) -> None:
        model = self.model()
        events = model.force_land_for_test(self.star, self.star.rect.center_x)
        self.assertIsNotNone(events.star)
        self.assertEqual(model.stars_collected, 1)
        self.assertEqual(model.score, game.STAR_BONUS)
        events2 = model.force_land_for_test(self.star, self.star.rect.center_x)
        self.assertIsNone(events2.star)
        self.assertEqual(model.score, game.STAR_BONUS)

    def test_intermediate_landing_breaks_direct_bonus(self) -> None:
        model = self.model()
        model.force_land_for_test(self.other, self.other.rect.center_x)
        events = model.force_land_for_test(self.cp1, self.cp1.rect.center_x)
        self.assertFalse(events.direct)
        self.assertTrue(events.perfect)

    def test_combo_multiplier_applies_to_second_checkpoint(self) -> None:
        model = self.model()
        first = model.force_land_for_test(self.cp1, self.cp1.rect.center_x)
        second = model.force_land_for_test(self.cp2, self.cp2.rect.center_x)
        self.assertEqual(first.combo, 1)
        self.assertEqual(second.combo, 2)
        self.assertAlmostEqual(model.combo_multiplier, 1.2)
        self.assertGreater(second.score_delta, 0)

    def test_final_checkpoint_completes_run(self) -> None:
        model = self.model()
        model.force_land_for_test(self.cp1, self.cp1.rect.center_x)
        events = model.force_land_for_test(self.cp2, self.cp2.rect.center_x)
        self.assertTrue(events.victory)
        self.assertEqual(model.state, game.STATE_VICTORY)
        self.assertIsNotNone(model.result)
        assert model.result is not None
        self.assertTrue(model.result.completed)
        self.assertEqual(model.result.rating, 3)
        self.assertGreater(model.score, 0)
        self.assertIsNone(model.next_target)

    def test_fall_costs_heart_and_respawns_at_checkpoint(self) -> None:
        model = self.model()
        model.force_land_for_test(self.cp1, self.cp1.rect.center_x)
        model.state = game.STATE_AIRBORNE
        model.missed = True
        model.char_feet = self.screen.bottom + game.FALL_MARGIN + 10
        model.flight_time = 1.0
        events = model.tick(0.016)
        self.assertTrue(events.fell)
        self.assertEqual(model.hearts, game.START_HEARTS - 1)
        self.assertEqual(model.checkpoint_index, 1)
        self.assertEqual(model.current.uid, self.cp1.uid)
        self.assertEqual(model.combo, 0)
        self.assertAlmostEqual(model.char_feet, self.cp1.rect.top)

    def test_three_falls_end_run(self) -> None:
        model = self.model()
        events = game.UpdateEvents()
        for expected in (1, 2, 3):
            model.state = game.STATE_AIRBORNE
            model.missed = True
            model.char_feet = self.screen.bottom + game.FALL_MARGIN + 10
            model.flight_time = 1.0
            events = model.tick(0.016)
            self.assertTrue(events.fell)
            self.assertEqual(model.falls, expected)
        self.assertTrue(events.game_over)
        self.assertEqual(model.state, game.STATE_GAME_OVER)
        self.assertIsNotNone(model.result)
        assert model.result is not None
        self.assertEqual(model.result.rating, 0)

    def test_relaxed_mode_does_not_lose_hearts(self) -> None:
        model = self.model(relaxed=True)
        for _ in range(4):
            model.state = game.STATE_AIRBORNE
            model.missed = True
            model.char_feet = self.screen.bottom + game.FALL_MARGIN + 10
            model.flight_time = 1.0
            events = model.tick(0.016)
            self.assertTrue(events.fell)
        self.assertIsNone(model.hearts_left)
        self.assertNotEqual(model.state, game.STATE_GAME_OVER)

    def test_advance_clock_starts_on_first_jump(self) -> None:
        model = self.model()
        model.advance_clock(1.0)
        self.assertEqual(model.elapsed, 0.0)
        now = 10.0
        model.begin_charge(now)
        model.charge_started = now - game.CHARGE_SECONDS * 0.5
        model.release(now)
        model.advance_clock(0.5)
        self.assertGreater(model.elapsed, 0.0)

    def test_replace_pending_checkpoints_preserves_run(self) -> None:
        model = self.model()
        model.force_land_for_test(self.cp1, self.cp1.rect.center_x)
        old_score = model.score
        old_hearts = model.hearts
        new_cp = make_platform(9, game.Rect(900, 500, 1000, 540), "new")
        new_course_platforms = self.course.platforms + [new_cp]
        model.replace_pending_checkpoints(
            new_course_platforms,
            [new_cp],
            [],
        )
        self.assertEqual(model.checkpoint_index, 1)
        self.assertEqual(model.next_target.uid, new_cp.uid)
        self.assertEqual(model.score, old_score)
        self.assertEqual(model.hearts, old_hearts)
        self.assertEqual(len(model.course.checkpoints), 2)

    def test_load_course_resets_results(self) -> None:
        model = self.model()
        model.force_land_for_test(self.cp1, self.cp1.rect.center_x)
        new_course = make_course(
            [
                game.Rect(100, 300, 200, 340),
                game.Rect(500, 300, 600, 340),
                game.Rect(900, 300, 1000, 340),
            ],
        )
        model.load_course(new_course)
        self.assertEqual(model.score, 0)
        self.assertEqual(model.checkpoint_index, 0)
        self.assertEqual(model.current.uid, new_course.start.uid)
        self.assertEqual(model.hearts, game.START_HEARTS)
        self.assertIsNone(model.result)

    def test_short_jump_lands_on_non_checkpoint_platform(self) -> None:
        # 起点中心 x=150，普通控件中心 x=300，距离 150px。
        long_course = game.Course(
            start=self.start,
            checkpoints=[self.cp2],
            bonus_stars=[],
            platforms=[self.start, self.other, self.cp2],
        )
        power = (150.0 - game.JUMP_DISTANCE_MIN) / (
            game.JUMP_DISTANCE_MAX - game.JUMP_DISTANCE_MIN
        )
        model = game.GameModel(long_course, self.screen)
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
        self.assertEqual(model.checkpoint_index, 0)

    def test_exact_jump_to_checkpoint_wins_one_stop_course(self) -> None:
        short_course = game.Course(
            start=self.start,
            checkpoints=[self.cp2],
            bonus_stars=[],
            platforms=[self.start, self.cp2],
        )
        # 起点中心 x=150，目标中心 x=750，距离 600px。
        power = (600.0 - game.JUMP_DISTANCE_MIN) / (
            game.JUMP_DISTANCE_MAX - game.JUMP_DISTANCE_MIN
        )
        model = game.GameModel(short_course, self.screen)
        now = 10.0
        model.begin_charge(now)
        model.charge_started = now - power * game.CHARGE_SECONDS
        model.release(now)
        for _ in range(240):
            model.tick(1 / 60)
            if model.state != game.STATE_AIRBORNE:
                break
        self.assertEqual(model.state, game.STATE_VICTORY)
        self.assertTrue(model.result is not None and model.result.completed)


class RectTests(unittest.TestCase):
    def test_rect_overlap_ratio(self) -> None:
        a = game.Rect(0, 0, 100, 100)
        b = game.Rect(50, 0, 150, 100)
        self.assertAlmostEqual(a.overlap_ratio(b), 0.5)
        self.assertEqual(a.intersect(b), game.Rect(50, 0, 100, 100))

    def test_rects_differ_tolerance(self) -> None:
        a = game.Rect(0, 0, 100, 100)
        self.assertFalse(game.rects_differ(a, game.Rect(3, 0, 103, 100), 6))
        self.assertTrue(game.rects_differ(a, game.Rect(20, 0, 120, 100), 6))


if __name__ == "__main__":
    unittest.main()
