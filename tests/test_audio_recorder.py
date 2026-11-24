"""測試 PipeWire 環形音訊緩衝、能量 VAD 靜音裁切與 FFmpeg MP3 轉碼。"""

from __future__ import annotations

import array
import math
import time
import unittest

from galgame_anki.adapters.audio_recorder import (
    SAMPLE_RATE,
    AudioRingRecorder,
    encode_pcm_to_mp3,
    trim_silence_vad,
)


def _synth_pcm(duration_sec: float, freq_hz: float = 0.0, amp: int = 0) -> bytes:
    n = int(SAMPLE_RATE * duration_sec)
    buf = array.array("h")
    for i in range(n):
        if amp == 0 or freq_hz == 0.0:
            buf.append(0)
        else:
            val = int(amp * math.sin(2.0 * math.pi * freq_hz * i / SAMPLE_RATE))
            buf.append(val)
    return buf.tobytes()


class TestAudioRecorder(unittest.TestCase):
    def test_vad_trims_leading_and_trailing_silence(self) -> None:
        # 1.0s 靜音 + 0.6s 人聲模擬 + 1.0s 靜音 = 2.6s
        pcm = (
            _synth_pcm(1.0, 0, 0)
            + _synth_pcm(0.6, 440.0, 6000)
            + _synth_pcm(1.0, 0, 0)
        )
        trimmed = trim_silence_vad(pcm, pad_ms=160)
        self.assertTrue(trimmed)
        # 裁切後應顯著短於 2.6s，且大於 0.6s 原始語音長度
        self.assertLess(len(trimmed), len(pcm) * 0.65)
        self.assertGreater(len(trimmed), int(SAMPLE_RATE * 0.6 * 2))

    def test_vad_rejects_pure_silence(self) -> None:
        silent = _synth_pcm(1.0, 0, 0)
        self.assertEqual(trim_silence_vad(silent), b"")

    def test_ring_buffer_extract_mp3_via_ffmpeg(self) -> None:
        rec = AudioRingRecorder(max_seconds=10)
        t0 = time.time() - 2.0
        # 寫入時間區間內的靜音 + 語音 + 靜音
        rec.inject_pcm_for_test(t0 - 0.2, _synth_pcm(0.4, 0, 0))
        rec.inject_pcm_for_test(t0 + 0.3, _synth_pcm(0.5, 520.0, 7000))
        rec.inject_pcm_for_test(t0 + 0.9, _synth_pcm(0.5, 0, 0))

        clip = rec.extract_mp3_clip(t_start=t0, t_end=t0 + 1.2)
        self.assertIsNotNone(clip)
        assert clip is not None
        mp3_bytes, filename = clip
        self.assertTrue(filename.endswith(".mp3"))
        self.assertGreater(len(mp3_bytes), 500)
        # MP3 檔頭通常為 ID3 或 MPEG frame sync (0xFF 0xFB/0xF3)
        self.assertTrue(mp3_bytes.startswith(b"ID3") or mp3_bytes[0] == 0xFF)

    def test_encode_pcm_to_mp3_direct(self) -> None:
        mp3 = encode_pcm_to_mp3(_synth_pcm(0.4, 440.0, 5000))
        self.assertGreater(len(mp3), 300)


if __name__ == "__main__":
    unittest.main()
