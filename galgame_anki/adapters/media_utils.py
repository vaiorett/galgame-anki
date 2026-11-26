"""本機媒體試聽與 mpv 影片原聲／畫面無損裁切工具（純 FFmpeg / ffplay / paplay，無系統錄音）。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

AUDIO_SAMPLE_RATE = 24000

_PLAY_LOCK = threading.Lock()
_ACTIVE_PLAYER: subprocess.Popen | None = None


def stop_active_audio_preview() -> None:
    """立即停止目前正在播放的試聽音訊進程，避免快速連點重疊爆音。"""
    global _ACTIVE_PLAYER
    with _PLAY_LOCK:
        proc = _ACTIVE_PLAYER
        _ACTIVE_PLAYER = None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except (OSError, subprocess.SubprocessError):
            try:
                proc.kill()
            except OSError:
                pass


def extract_video_audio_mp3(
    video_path: str,
    start_sec: float,
    end_sec: float,
    pad_sec: float = 0.25,
    audio_stream_index: int | None = None,
) -> tuple[bytes, str] | None:
    """針對 mpv 播放的本地影片，直接以 FFmpeg 精準擷取該句字幕時間軸的原聲 MP3。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path or not Path(video_path).exists():
        return None
    ss = max(0.0, start_sec - pad_sec)
    dur = max(0.4, min(18.0, (end_sec - start_sec) + pad_sec * 2))

    def _run_extract(stream_idx: int | None) -> bytes | None:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ss:.3f}",
            "-i",
            video_path,
            "-t",
            f"{dur:.3f}",
            *(["-map", f"0:{stream_idx}"] if stream_idx is not None else []),
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
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if res.returncode == 0 and res.stdout:
            return res.stdout
        return None

    out = _run_extract(audio_stream_index)
    if out is None and audio_stream_index is not None:
        out = _run_extract(None)
    if not out:
        return None
    fname = f"gal_mpv_{int(time.time() * 1000)}.mp3"
    return out, fname


def extract_video_frame_webp(
    video_path: str,
    at_sec: float,
) -> tuple[bytes, str] | None:
    """針對 mpv 本地影片，直接以 FFmpeg 無損截取字幕當下的動畫畫面（WebP）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path or not Path(video_path).exists():
        return None
    ss = max(0.0, at_sec + 0.15)
    try:
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
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0 or not res.stdout:
        return None
    fname = f"gal_frame_{int(time.time() * 1000)}.webp"
    return res.stdout, fname


def play_mp3_bytes_async(mp3_bytes: bytes, on_error: Callable[[str], None] | None = None) -> None:
    """非同步試聽語音 MP3（自動取消前一次播放並限制逾時，使用 ffplay 或 paplay）。"""
    if not mp3_bytes:
        return

    def _run_tracked(cmd: list[str]) -> bool:
        global _ACTIVE_PLAYER
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with _PLAY_LOCK:
                _ACTIVE_PLAYER = proc
            rc = proc.wait(timeout=25)
            return rc == 0
        except (OSError, subprocess.SubprocessError):
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except (OSError, subprocess.SubprocessError):
                    pass
            return False
        finally:
            with _PLAY_LOCK:
                if _ACTIVE_PLAYER is proc:
                    _ACTIVE_PLAYER = None

    def _play() -> None:
        stop_active_audio_preview()
        with tempfile.TemporaryDirectory(prefix="galanki_play_") as tmpdir:
            path = Path(tmpdir) / "preview.mp3"
            path.write_bytes(mp3_bytes)
            ffplay = shutil.which("ffplay")
            if ffplay and _run_tracked([ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]):
                return
            paplay = shutil.which("paplay")
            ffmpeg = shutil.which("ffmpeg")
            if paplay and ffmpeg:
                wav_path = Path(tmpdir) / "preview.wav"
                try:
                    subprocess.run(
                        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), str(wav_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    wav_path = Path("")
                if wav_path and wav_path.exists() and _run_tracked([paplay, str(wav_path)]):
                    return
            if on_error:
                on_error("試聽失敗：請確認系統音訊輸出及 ffplay 或 paplay 是否可用")

    threading.Thread(target=_play, daemon=True).start()
