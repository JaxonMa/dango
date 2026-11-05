#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程序化音效生成的轻量测试。"""

from __future__ import annotations

import unittest
import wave

import dango_audio


class AudioTests(unittest.TestCase):
    def test_muted_player_is_noop(self) -> None:
        player = dango_audio.SoundPlayer(muted=True)
        player.play("land")
        player.play("unknown")

    def test_generated_sound_is_valid_wav(self) -> None:
        path = dango_audio._ensure_sound("checkpoint")
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(path.exists())
        with wave.open(str(path), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getframerate(), dango_audio.SAMPLE_RATE)
            self.assertGreater(wav.getnframes(), 0)

    def test_unknown_sound_returns_none(self) -> None:
        self.assertIsNone(dango_audio._ensure_sound("does-not-exist"))


if __name__ == "__main__":
    unittest.main()
