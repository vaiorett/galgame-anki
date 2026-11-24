"""本機媒體試聽與 mpv 影片原聲／畫面無損裁切工具（純 FFmpeg / ffplay / paplay，無系統錄音）。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

AUDIO_SAMPLE_RATE = 24000


def extract_video_audio_mp3(
    video_path: str,
    start_sec: float,
    end_sec: float,
    pad_sec: float = 0.25,
) -> tuple[bytes, str] | None:
    """針對 mpv 播放的本地影片，直接以 FFmpeg 精準擷取該句字幕時間軸的原聲 MP3。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path or not Path(video_path).exists():
        return None
    ss = max(0.0, start_sec - pad_sec)
    dur = max(0.4, min(18.0, (end_sec - start_sec) + pad_sec * 2))
    res = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ss:.3f}",
            "-t",
            f"{dur:.3f}",
            "-i",
            video_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            "-f",
            "mp3",
            "pipe:1",
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if res.returncode != 0 or not res.stdout:
        return None
    fname = f"gal_mpv_{int(time.time() * 1000)}.mp3"
    return res.stdout, fname


def extract_video_frame_webp(
    video_path: str,
    at_sec: float,
) -> tuple[bytes, str] | None:
    """針對 mpv 本地影片，直接以 FFmpeg 無損截取字幕當下的動畫畫面（WebP）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path or not Path(video_path).exists():
        return None
    ss = max(0.0, at_sec + 0.15)
    res = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ss:.3f}",
            "-i",
            video_path,
            "-vframes",
            "1",
            "-vf",
            "scale='min(960,iw)':-2",
            "-c:v",
            "libwebp",
            "-quality",
            "78",
            "-f",
            "webp",
            "pipe:1",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if res.returncode != 0 or not res.stdout:
        return None
    fname = f"gal_frame_{int(time.time() * 1000)}.webp"
    return res.stdout, fname


def play_mp3_bytes_async(mp3_bytes: bytes) -> None:
    """非同步試聽語音 MP3（使用 ffplay 或 paplay）。"""
    if not mp3_bytes:
        return

    def _play() -> None:
        with tempfile.TemporaryDirectory(prefix="galanki_play_") as tmpdir:
            path = Path(tmpdir) / "preview.mp3"
            path.write_bytes(mp3_bytes)
            ffplay = shutil.which("ffplay")
            if ffplay:
                subprocess.run(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                return
            paplay = shutil.which("paplay")
            ffmpeg = shutil.which("ffmpeg")
            if paplay and ffmpeg:
                wav_path = Path(tmpdir) / "preview.wav"
                subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), str(wav_path)],
                    check=False,
                )
                if wav_path.exists():
                    subprocess.run([paplay, str(wav_path)], check=False)

    threading.Thread(target=_play, daemon=True).start()
