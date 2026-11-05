#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 参数与 RunConfig 映射测试。"""

from __future__ import annotations

import unittest

import main


class CliTests(unittest.TestCase):
    def test_new_run_options(self) -> None:
        args = main.parse_args(
            [
                "--hearts",
                "4",
                "--checkpoints",
                "3",
                "--stars",
                "1",
                "--seed",
                "7",
                "--relaxed",
                "--mute",
            ]
        )
        self.assertEqual(args.run_config.hearts, 4)
        self.assertEqual(args.run_config.checkpoint_count, 3)
        self.assertEqual(args.run_config.star_count, 1)
        self.assertEqual(args.run_config.seed, 7)
        self.assertTrue(args.run_config.relaxed)
        self.assertTrue(args.mute)

    def test_values_are_clamped(self) -> None:
        args = main.parse_args(["--hearts", "99", "--checkpoints", "99", "--stars", "99"])
        self.assertEqual(args.run_config.hearts, 9)
        self.assertEqual(args.run_config.checkpoint_count, 7)
        self.assertEqual(args.run_config.star_count, 4)

    def test_legacy_retry_flags_map_to_hearts(self) -> None:
        args = main.parse_args(["--limit-retries", "--max-retries", "5"])
        self.assertEqual(args.run_config.hearts, 5)


if __name__ == "__main__":
    unittest.main()
