"""免 OBS 遊戲語音自動擷取與 VAD 靜音裁切引擎（Linux PipeWire / PulseAudio 優先，預留 Windows WASAPI）。

運作方式：
1. 在背景持續以極低資源（24kHz 16-bit mono，每秒僅 48KB）透過 `parec` 或 `pw-record`
   錄製系統輸出監聽（或單獨鎖定 Wine/遊戲音訊節點）寫入 45 秒記憶體環形緩衝區。
2. 每句台詞到達時記錄時間戳 `t0`；一鍵製卡或點擊「🔊 試聽」時，從環形緩衝區切出該句對應音訊段。
3. 使用純 Python 自適應 RMS 能量 VAD 自動切除頭尾靜音並保留 180ms 自然緩衝，
   再由本機 FFmpeg 壓縮為高相容性 `.mp3`（64kbps），直接寫入 Anki `SentenceAudio` 欄位。
"""

from __future__ import annotations

import array
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16le (2 bytes per sample)
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480 samples
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH      # 960 bytes


@dataclass
class AudioChunk:
    timestamp: float  # 該片段結束時的 Unix 時間戳
    pcm: bytes


def _rms_s16le(pcm: bytes) -> float:
    """計算 16-bit little-endian PCM 片段的均方根能量 (0.0 ~ 32768.0)。"""
    n_samples = len(pcm) // 2
    if n_samples == 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[: n_samples * 2])
    total = sum(s * s for s in samples)
    return math.sqrt(total / n_samples)


def trim_silence_vad(
    pcm: bytes,
    sample_rate: int = SAMPLE_RATE,
    pad_ms: int = 200,
    min_speech_ms: int = 100,
    abs_floor_rms: float = 70.0,
) -> bytes:
    """純 Python 自適應能量 VAD：自動切除頭尾靜音，若整段完全靜音則回傳空 bytes。"""
    frame_bytes = (sample_rate * FRAME_MS // 1000) * SAMPLE_WIDTH
    if len(pcm) < frame_bytes * 3:
        return b""

    frames: list[bytes] = []
    energies: list[float] = []
    for offset in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        fr = pcm[offset : offset + frame_bytes]
        frames.append(fr)
        energies.append(_rms_s16le(fr))

    if not energies:
        return b""

    peak = max(energies)
    if peak < abs_floor_rms:
        # 整段都沒有明顯聲音（例如無配音旁白或已靜音）
        return b""

    # 以最低 20% 能量估計環境底噪，兼顧有微弱 BGM 時仍可抓出語音起訖
    sorted_e = sorted(energies)
    noise_floor = sorted_e[max(0, len(sorted_e) // 5)]
    threshold = max(abs_floor_rms, noise_floor * 1.45, peak * 0.09)

    active_indices = [i for i, e in enumerate(energies) if e >= threshold]
    if not active_indices:
        return b""

    # 要求至少達到最小有效發聲時長
    if len(active_indices) * FRAME_MS < min_speech_ms:
        return b""

    pad_frames = pad_ms // FRAME_MS
    start_idx = max(0, active_indices[0] - pad_frames)
    end_idx = min(len(frames), active_indices[-1] + 1 + pad_frames)
    return b"".join(frames[start_idx:end_idx])


def encode_pcm_to_mp3(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """透過本機 FFmpeg 將 s16le PCM 轉碼為輕量、全平台 Anki 相容的 64kbps MP3。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，無法編碼語音為 MP3。")

    res = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(CHANNELS),
            "-i",
            "pipe:0",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=pcm,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if res.returncode != 0 or not res.stdout:
        err = res.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"FFmpeg 語音轉碼失敗：{err}")
    return res.stdout


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
            str(SAMPLE_RATE),
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
    """非同步試聽已裁切好的語音（使用 ffplay 或 paplay）。"""
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


def find_default_monitor_source() -> str | None:
    """透過 pactl 找出系統預設輸出裝置的 Monitor 來源（免任何手動設定）。"""
    pactl = shutil.which("pactl")
    if not pactl:
        return None
    try:
        res = subprocess.run(
            [pactl, "get-default-sink"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        sink = res.stdout.strip()
        if res.returncode == 0 and sink:
            return f"{sink}.monitor"
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def find_game_pipewire_node(game_hint: str = "") -> str | None:
    """嘗試尋找 Wine / Galgame 獨立音訊串流節點（若找到可只錄遊戲不錄其他軟體聲音）。"""
    pactl = shutil.which("pactl")
    if not pactl:
        return None
    try:
        res = subprocess.run(
            [pactl, "list", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None

    blocks = re.split(r"(?=Sink Input #\d+)", res.stdout)
    hint_low = game_hint.lower().strip()
    fallback_serial: str | None = None

    for blk in blocks:
        if "Sink Input #" not in blk:
            continue
        serial_m = re.search(r'object\.serial\s*=\s*"(\d+)"', blk)
        if not serial_m:
            continue
        serial = serial_m.group(1)
        blk_low = blk.lower()
        if hint_low and hint_low in blk_low:
            return serial
        if any(k in blk_low for k in ("wine", ".exe", "proton", "fmod", "kirikiri")):
            fallback_serial = serial

    return fallback_serial


class AudioRingRecorder(QObject):
    """跨平台音訊環形緩衝錄音器（Linux PipeWire/PulseAudio 立即可用，預留 Windows WASAPI）。"""

    state_changed = Signal(bool, str)  # (recording, status_text)

    def __init__(self, max_seconds: int = 240, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._max_chunks = max(100, max_seconds * 1000 // 50)
        self._chunks: deque[AudioChunk] = deque(maxlen=self._max_chunks)
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._source_label: str = ""

    @property
    def is_recording(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def source_label(self) -> str:
        return self._source_label

    def start(self, game_hint: str = "") -> bool:
        """啟動背景環形緩衝錄音（採用 parec s16le @DEFAULT_MONITOR@，自動跟隨耳機/喇叭切換並支援 4 分鐘回溯！）。"""
        with self._lifecycle_lock:
            self.stop(emit_signal=False)

            if os.name == "nt":
                self.state_changed.emit(False, "Windows 錄音介面待掛載 WASAPI")
                return False

            cmd: list[str] | None = None
            label = ""

            parec = shutil.which("parec")
            # 使用 parec --format=s16le -d @DEFAULT_MONITOR@：
            # 1) 徹底避免 pw-record 輸出 Sun .au (big-endian) 造成白噪音；
            # 2) 自動跟隨耳機/喇叭切換，絕不因換輸出裝置變靜音；
            # 3) 同時覆蓋 Galgame 與 mpv 動畫聲優原聲！
            if parec:
                cmd = [
                    parec,
                    "--format=s16le",
                    f"--rate={SAMPLE_RATE}",
                    f"--channels={CHANNELS}",
                    "--latency-msec=50",
                    "-d",
                    "@DEFAULT_MONITOR@",
                ]
                label = "全媒體原聲監聽（4 分鐘環形緩衝）"

            if not cmd:
                self.state_changed.emit(False, "找不到 parec（請安裝 pulseaudio-utils / pipewire-pulse）")
                return False

            self._stop_flag.clear()
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                self.state_changed.emit(False, f"錄音啟動失敗：{exc}")
                return False

            self._source_label = label
            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(self._proc,),
                daemon=True,
            )
            self._thread.start()
            self.state_changed.emit(True, f"🎙 原聲緩衝中（{label}）")
            return True

    def stop(self, emit_signal: bool = True) -> None:
        with self._lifecycle_lock:
            self._stop_flag.set()
            proc = self._proc
            self._proc = None
            if proc is not None:
                try:
                    proc.terminate()
                except OSError:
                    pass
                try:
                    proc.kill()
                except OSError:
                    pass
            if emit_signal:
                self.state_changed.emit(False, "語音未錄製")

    def _capture_loop(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        chunk_bytes = (SAMPLE_RATE * 50 // 1000) * SAMPLE_WIDTH  # 50ms = 2400 bytes
        # 若 pw-record 輸出 WAV header (前 44 bytes 開頭為 RIFF)，自動跳過
        first_chunk = True
        while not self._stop_flag.is_set():
            try:
                data = proc.stdout.read(chunk_bytes)
            except OSError:
                break
            if not data:
                break
            if first_chunk:
                first_chunk = False
                if data.startswith(b"RIFF") and len(data) > 44:
                    data = data[44:]
            now = time.time()
            with self._lock:
                self._chunks.append(AudioChunk(timestamp=now, pcm=data))

    def inject_pcm_for_test(self, timestamp: float, pcm: bytes) -> None:
        """供單元測試注入已知時間戳的 PCM 資料。"""
        with self._lock:
            self._chunks.append(AudioChunk(timestamp=timestamp, pcm=pcm))

    def extract_mp3_clip(
        self,
        t_start: float,
        t_end: float | None = None,
        pre_pad_sec: float = 0.35,
        max_duration_sec: float = 11.0,
    ) -> tuple[bytes, str] | None:
        """從環形緩衝區切出 [t_start - pre_pad, end] 並經 VAD 裁靜音後轉成 MP3。"""
        now = time.time()
        win_start = t_start - pre_pad_sec
        if t_end is not None and t_end > t_start:
            win_end = min(t_end + 0.15, t_start + max_duration_sec)
        else:
            win_end = min(now, t_start + max_duration_sec)

        with self._lock:
            selected = [
                c.pcm for c in self._chunks if win_start <= c.timestamp <= win_end + 0.08
            ]
        if not selected:
            return None

        raw_pcm = b"".join(selected)
        trimmed = trim_silence_vad(raw_pcm)
        if not trimmed:
            # 若自適應門檻過嚴但確有足夠音訊，保留適度片段以免漏掉輕聲台詞
            if _rms_s16le(raw_pcm) < 60.0:
                return None
            trimmed = raw_pcm

        mp3 = encode_pcm_to_mp3(trimmed)
        filename = f"gal_voice_{int(t_start * 1000)}.mp3"
        return mp3, filename

    def capture_manual_window(self, duration_sec: float = 4.5) -> tuple[bytes, str] | None:
        """手動即時補錄一段桌面音訊（方便在 Galgame 點重播語音鈕補抓最清晰的聲優原聲）。"""
        if not self.is_recording:
            self.start()
        t0 = time.time()
        time.sleep(max(1.0, min(12.0, duration_sec)))
        t1 = time.time()
        return self.extract_mp3_clip(t_start=t0, t_end=t1, pre_pad_sec=0.1, max_duration_sec=duration_sec + 0.5)
