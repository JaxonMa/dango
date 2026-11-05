#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地最高分/设置的纯文件测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import dango_progress as progress_mod


class ProgressTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "progress.json"
            progress = progress_mod.load_progress(path)
            self.assertEqual(progress.best_score, 0)
            self.assertIsNone(progress.best_time)
            self.assertFalse(progress.muted)

    def test_roundtrip_and_record_best(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.json"
            progress = progress_mod.Progress(best_score=100, plays=1)
            self.assertTrue(progress_mod.save_progress(progress, path))
            loaded = progress_mod.load_progress(path)
            self.assertEqual(loaded.best_score, 100)

            updated, new_best = progress_mod.record_result(
                loaded,
                score=250,
                elapsed=12.5,
                stars=3,
                completed=True,
            )
            self.assertTrue(new_best)
            self.assertEqual(updated.best_score, 250)
            self.assertEqual(updated.best_time, 12.5)
            self.assertEqual(updated.best_stars, 3)
            self.assertEqual(updated.plays, 2)

            slower, new_best2 = progress_mod.record_result(
                updated,
                score=50,
                elapsed=20.0,
                stars=1,
                completed=True,
            )
            self.assertFalse(new_best2)
            self.assertEqual(slower.best_score, 250)
            self.assertEqual(slower.best_time, 12.5)

    def test_failed_run_keeps_best_time_and_updates_play_count(self) -> None:
        progress = progress_mod.Progress(best_score=100, best_time=8.0, plays=3)
        updated, new_best = progress_mod.record_result(
            progress,
            score=120,
            elapsed=2.0,
            stars=0,
            completed=False,
        )
        self.assertTrue(new_best)
        self.assertEqual(updated.best_score, 120)
        self.assertEqual(updated.best_time, 8.0)
        self.assertEqual(updated.plays, 4)

    def test_corrupt_file_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.json"
            path.write_text("{not-json", encoding="utf-8")
            progress = progress_mod.load_progress(path)
            self.assertEqual(progress.best_score, 0)
            self.assertEqual(progress.plays, 0)

    def test_muted_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.json"
            progress = progress_mod.Progress(muted=True)
            progress_mod.save_progress(progress, path)
            self.assertTrue(progress_mod.load_progress(path).muted)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], progress_mod.PROGRESS_VERSION)


if __name__ == "__main__":
    unittest.main()
