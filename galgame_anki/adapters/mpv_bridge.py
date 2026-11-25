"""mpv 影片播放器字幕與時間軸原聲即時橋接器（完整支援 Flatpak io.mpv.Mpv 與系統原生 mpv）。

功能：
1. 自動偵測系統原生 `mpv` 與 Flatpak `io.mpv.Mpv`（無需重複安裝系統套件）。
2. 自動將 `input-ipc-server=/tmp/galanki-mpv.sock` 寫入對應的 `mpv.conf`
   （Flatpak 為 `~/.var/app/io.mpv.Mpv/config/mpv/mpv.conf`，原生為 `~/.config/mpv/mpv.conf`）。
3. 即時監聽 `sub-text`、`sub-start`、`sub-end`、`path`、`media-title`，並支援透過 IPC 一鍵隱藏 mpv 原生字幕，
   讓 GalAnki 覆蓋層直接取代原字幕（絕不看兩份字幕）！
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

FLATPAK_MPV_APP_ID = "io.mpv.Mpv"
DEFAULT_MPV_SOCKET = "/tmp/galanki-mpv.sock"
DEFAULT_MPV_SOCKETS = (DEFAULT_MPV_SOCKET, "/tmp/mpvsocket")


@dataclass
class MpvSubtitleEvent:
    text: str
    media_title: str = ""
    video_path: str = ""
    sub_start: float | None = None
    sub_end: float | None = None
    audio_stream_index: int | None = None
    external_audio_path: str = ""
    chinese_subtitle: str = ""


# 同一個 mpv 指令讀取整組屬性，避免 sub-text 比 sub-start/sub-end 先抵達。
_SNAPSHOT_PROPERTIES = ("sub-start", "sub-end", "path", "working-directory", "media-title",
                        "current-tracks/audio/ff-index", "current-tracks/audio/external-filename",
                        "current-tracks/sub2/lang", "secondary-sub-start", "secondary-sub-end",
                        "secondary-sub-text", "sub-text")
_SNAPSHOT_FORMAT = "\x1f".join("${=" + name + ":}" for name in _SNAPSHOT_PROPERTIES)


def _subtitle_snapshot(value: str) -> MpvSubtitleEvent | None:
    fields = value.split("\x1f", len(_SNAPSHOT_PROPERTIES) - 1)
    if len(fields) != len(_SNAPSHOT_PROPERTIES):
        return None
    start, end, path, cwd, title, index, external, lang, sub2_start, sub2_end, translation, text = fields
    if not text.strip():
        return None
    if path and cwd and "://" not in path:
        path = str(Path(cwd) / path)
    def seconds(raw: str) -> float | None:
        try:
            return float(raw)
        except ValueError:
            return None
    event = MpvSubtitleEvent(text.strip(), title, path, seconds(start), seconds(end))
    event.audio_stream_index = int(index) if index.isdigit() else None
    event.external_audio_path = str(Path(cwd) / external) if external else ""
    if lang.lower().split("-")[0] in {"zh", "chi", "zho", "cmn"}:
        other_start, other_end = seconds(sub2_start), seconds(sub2_end)
        if None not in (event.sub_start, event.sub_end, other_start, other_end):
            if abs(other_start - event.sub_start) <= 0.35 and abs(other_end - event.sub_end) <= 0.35:
                event.chinese_subtitle = translation.strip()
    return event


def _is_flatpak_mpv_installed() -> bool:
    """快速無阻塞偵測系統或使用者目錄是否已安裝 Flatpak 版 io.mpv.Mpv。"""
    if shutil.which("flatpak") is None:
        return False
    # 1. 快速檔案系統檢查（零子進程延遲）
    candidates = (
        Path("/var/lib/flatpak/app") / FLATPAK_MPV_APP_ID,
        Path.home() / ".local" / "share" / "flatpak" / "app" / FLATPAK_MPV_APP_ID,
        Path.home() / ".var" / "app" / FLATPAK_MPV_APP_ID,
    )
    if any(p.exists() for p in candidates):
        return True
    # 2. 備援：查詢 flatpak info
    try:
        res = subprocess.run(
            ["flatpak", "info", FLATPAK_MPV_APP_ID],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_mpv_backend() -> dict[str, Any]:
    """偵測本機可用的 mpv 執行環境（支援系統原生 mpv 與 Flatpak io.mpv.Mpv）。"""
    native_bin = shutil.which("mpv")
    has_flatpak = _is_flatpak_mpv_installed()

    if native_bin:
        cfg_paths = [Path.home() / ".config" / "mpv" / "mpv.conf"]
        if has_flatpak:
            cfg_paths.append(Path.home() / ".var" / "app" / FLATPAK_MPV_APP_ID / "config" / "mpv" / "mpv.conf")
        return {
            "installed": True,
            "mode": "native",
            "label": "系統原生 mpv" + ("（同時偵測到 Flatpak io.mpv.Mpv）" if has_flatpak else ""),
            "cmd": [native_bin],
            "config_paths": cfg_paths,
        }

    if has_flatpak:
        return {
            "installed": True,
            "mode": "flatpak",
            "label": f"Flatpak mpv ({FLATPAK_MPV_APP_ID})",
            "cmd": ["flatpak", "run", FLATPAK_MPV_APP_ID],
            "config_paths": [
                Path.home() / ".var" / "app" / FLATPAK_MPV_APP_ID / "config" / "mpv" / "mpv.conf",
                Path.home() / ".config" / "mpv" / "mpv.conf",
            ],
        }

    return {
        "installed": False,
        "mode": "none",
        "label": "尚未偵測到 mpv",
        "cmd": [],
        "config_paths": [Path.home() / ".config" / "mpv" / "mpv.conf"],
    }


def is_mpv_installed() -> bool:
    return bool(detect_mpv_backend()["installed"])


def discover_configured_mpv_sockets() -> list[str]:
    """從原生與 Flatpak mpv.conf 自動讀取既有 input-ipc-server 路徑，搭配預設 Socket 清單。"""
    sockets: list[str] = list(DEFAULT_MPV_SOCKETS)
    conf_files = (
        Path.home() / ".var" / "app" / FLATPAK_MPV_APP_ID / "config" / "mpv" / "mpv.conf",
        Path.home() / ".config" / "mpv" / "mpv.conf",
    )
    for cf in conf_files:
        if not cf.exists():
            continue
        try:
            for ln in cf.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = ln.strip()
                if stripped.startswith("input-ipc-server="):
                    val = stripped.split("=", 1)[1].strip().strip("\"'")
                    if val and val not in sockets:
                        sockets.insert(0, val)
        except OSError:
            pass
    return sockets


def _patch_single_mpv_conf(cfg_file: Path, socket_path: str, hide_native_sub: bool = False) -> Path:
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    existing = cfg_file.read_text(encoding="utf-8", errors="ignore") if cfg_file.exists() else ""
    lines = existing.splitlines()
    has_ipc = any(ln.strip().startswith("input-ipc-server=") for ln in lines)
    has_sub_vis = any(ln.strip().startswith("sub-visibility=") for ln in lines)

    additions: list[str] = []
    if not has_ipc:
        additions.append(f"input-ipc-server={socket_path}")
    if hide_native_sub and not has_sub_vis:
        additions.append("sub-visibility=no")

    if additions:
        suffix = "\n" if existing and not existing.endswith("\n") else ""
        block = "# GalAnki 字幕自動連線設定\n" + "\n".join(additions) + "\n"
        cfg_file.write_text(f"{existing}{suffix}{block}", encoding="utf-8")
    return cfg_file


def install_mpv_ipc_config(
    socket_path: str = DEFAULT_MPV_SOCKET,
    hide_native_sub: bool = False,
) -> list[Path]:
    """一鍵為系統原生 mpv 與 Flatpak io.mpv.Mpv 寫入 input-ipc-server（幂等安全不破壞既有設定）。"""
    info = detect_mpv_backend()
    written: list[Path] = []
    for cfg_file in info["config_paths"]:
        written.append(_patch_single_mpv_conf(cfg_file, socket_path, hide_native_sub=hide_native_sub))
    return written


def launch_mpv_video(
    video_path: Path,
    socket_path: str = DEFAULT_MPV_SOCKET,
    hide_native_sub: bool = True,
) -> int:
    """以 IPC 模式一鍵透過原生 mpv 或 Flatpak io.mpv.Mpv 開啟影片檔（預設隱藏 mpv 原字幕避免雙字幕）。"""
    info = detect_mpv_backend()
    if not info["installed"] or not info["cmd"]:
        raise FileNotFoundError(
            "系統尚未安裝 mpv（可安裝 Flatpak io.mpv.Mpv 或執行 sudo dnf install mpv）。"
        )
    if not video_path.exists():
        raise FileNotFoundError(f"找不到影片檔：{video_path}")

    cmd = list(info["cmd"]) + [f"--input-ipc-server={socket_path}"]
    if hide_native_sub:
        cmd.append("--sub-visibility=no")
    cmd.append(str(video_path))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def parse_mpv_ipc_message(
    msg: dict,
    state: dict[str, object],
) -> MpvSubtitleEvent | None:
    """解析單筆 mpv JSON IPC 屬性變更事件；僅在字幕實質變化時回傳 MpvSubtitleEvent。"""
    if msg.get("event") != "property-change":
        return None
    name = msg.get("name")
    data = msg.get("data")

    if name == "media-title":
        state["media_title"] = str(data or "")
        return None
    if name == "path":
        state["video_path"] = str(data or "")
        return None
    if name == "sub-start":
        state["sub_start"] = float(data) if isinstance(data, (int, float)) else None
        return None
    if name == "sub-end":
        state["sub_end"] = float(data) if isinstance(data, (int, float)) else None
        return None
    if name == "pause":
        state["paused"] = bool(data)
        return None
    if name == "speed":
        if isinstance(data, (int, float)):
            state["speed"] = float(data)
        return None
    if name == "sub-text":
        text = str(data or "").strip()
        if not text:
            if state.get("sub_active"):
                state["sub_active"] = False
                state["sub_just_cleared"] = True
            return None
        state["sub_active"] = True
        state["sub_just_cleared"] = False
        if text == state.get("last_sub"):
            return None
        state["last_sub"] = text
        return MpvSubtitleEvent(
            text=text,
            media_title=str(state.get("media_title") or ""),
            video_path=str(state.get("video_path") or ""),
            sub_start=state.get("sub_start") if isinstance(state.get("sub_start"), float) else None,
            sub_end=state.get("sub_end") if isinstance(state.get("sub_end"), float) else None,
        )
    return None


class MpvSubtitleBridge(QObject):
    """背景自動探索並監聽原生／Flatpak mpv IPC Socket 的跨執行緒字幕與沉浸控制橋接器。"""

    navigation_failed = Signal(str)
    loop_changed = Signal(bool)
    subtitle_received = Signal(object)     # MpvSubtitleEvent
    connection_changed = Signal(bool, str) # (connected, status_msg)

    def __init__(
        self,
        socket_paths: tuple[str, ...] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._explicit_sockets = socket_paths
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._active_sock: socket.socket | None = None
        self._sock_lock = threading.Lock()

        # AJATT mpv 進階沉浸狀態（sub-transition 無字幕自動倍速 ＋ 句尾自動暫停 ＋ 單鍵重播）
        self._auto_speedup: bool = False
        self._fast_speed: float = 2.0
        self._normal_speed: float = 1.0
        self._auto_pause_sub_end: bool = False
        self._sped_up: bool = False
        self._speed_timer: threading.Timer | None = None
        self._last_sub_start: float | None = None
        self._last_sub_end: float | None = None
        self._paused: bool = False
        self._sentence_loop = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def send_ipc_command(self, command: list[Any], request_id: int = 0) -> bool:
        """透過 JSON IPC 向執行中的 mpv 傳送控制指令。"""
        raw = (json.dumps({"command": command, "request_id": request_id}) + "\n").encode("utf-8")
        with self._sock_lock:
            sock = self._active_sock
            if sock is None:
                return False
            try:
                sock.sendall(raw)
                return True
            except OSError:
                return False

    def set_native_subtitle_visible(self, visible: bool) -> bool:
        """透過 IPC 即時切換 mpv 原生字幕顯示/隱藏（讓覆蓋層成為唯一字幕，絕不看 2 份字幕！）。"""
        return self.send_ipc_command(["set_property", "sub-visibility", bool(visible)])

    def set_speed(self, speed: float) -> bool:
        clamped = max(0.5, min(4.0, float(speed)))
        return self.send_ipc_command(["set_property", "speed", clamped])

    def set_pause(self, paused: bool) -> bool:
        return self.send_ipc_command(["set_property", "pause", bool(paused)])

    def toggle_pause(self) -> bool:
        return self.send_ipc_command(["cycle", "pause"])

    def step_subtitle(self, offset: int) -> bool:
        self.stop_sentence_loop()
        self._cancel_speed_timer()
        if self._sped_up:
            self.set_speed(self._normal_speed)
            self._sped_up = False
        return self.send_ipc_command(["sub-seek", offset], request_id=91)

    def stop_sentence_loop(self) -> None:
        if self._sentence_loop:
            self.send_ipc_command(["set_property", "ab-loop-b", "no"])
            self.send_ipc_command(["set_property", "ab-loop-a", "no"])
            self._sentence_loop = False
            self.loop_changed.emit(False)

    def loop_subtitle(self, start: float, end: float) -> bool:
        if start < 0 or end <= start or not self._connected:
            return False
        self._cancel_speed_timer()
        self._sentence_loop = True
        if (self.send_ipc_command(["set_property", "ab-loop-a", start])
                and self.send_ipc_command(["set_property", "ab-loop-b", end])
                and self.replay_current_subtitle(start)):
            self.loop_changed.emit(True)
            return True
        self.stop_sentence_loop()
        return False

    def seek_to(self, sec: float) -> bool:
        return self.send_ipc_command(["seek", max(0.0, float(sec)), "absolute", "exact"])

    def replay_current_subtitle(self, sub_start: float | None = None) -> bool:
        """單鍵跳回目前字幕開頭並恢復正常倍速重聽一次（類似 mpvacious 重播）。"""
        target = sub_start if sub_start is not None else self._last_sub_start
        if target is None:
            return False
        self._cancel_speed_timer()
        if self._sped_up:
            self.set_speed(self._normal_speed)
            self._sped_up = False
        ok = self.seek_to(max(0.0, target - 0.05))
        if ok:
            self.set_pause(False)
        return ok

    def configure_immersion(
        self,
        auto_speedup: bool,
        fast_speed: float = 2.0,
        auto_pause_sub_end: bool = False,
    ) -> None:
        """即時更新無字幕倍速快進（sub-transition）與句尾自動暫停設定。"""
        self._auto_speedup = bool(auto_speedup)
        self._fast_speed = max(1.2, min(4.0, float(fast_speed)))
        self._auto_pause_sub_end = bool(auto_pause_sub_end)
        if not self._auto_speedup:
            self._cancel_speed_timer()
            if self._sped_up:
                self.set_speed(self._normal_speed)
                self._sped_up = False

    def _cancel_speed_timer(self) -> None:
        t = self._speed_timer
        self._speed_timer = None
        if t is not None:
            t.cancel()

    def _on_sub_active(self, sub_start: float | None, sub_end: float | None) -> None:
        self._cancel_speed_timer()
        if sub_start is not None:
            self._last_sub_start = sub_start
        if sub_end is not None:
            self._last_sub_end = sub_end
        if self._sped_up:
            self.set_speed(self._normal_speed)
            self._sped_up = False

    def _on_sub_cleared(self) -> None:
        if self._sentence_loop:
            return
        if self._auto_pause_sub_end and not self._paused:
            self._cancel_speed_timer()
            self.set_pause(True)
            return
        if self._auto_speedup and not self._paused:
            self._cancel_speed_timer()

            def _engage_fast() -> None:
                if self._auto_speedup and self._connected and not self._paused:
                    if self.set_speed(self._fast_speed):
                        self._sped_up = True

            t = threading.Timer(0.35, _engage_fast)
            t.daemon = True
            self._speed_timer = t
            t.start()

    def start(self) -> None:
        if os.name == "nt":
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_sentence_loop()
        self._cancel_speed_timer()
        if self._sped_up and self._connected:
            self.set_speed(self._normal_speed)
            self._sped_up = False
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            sock_path = self._find_active_socket()
            if not sock_path:
                time.sleep(1.5)
                continue
            self._run_session(sock_path)
            time.sleep(1.0)

    def _find_active_socket(self) -> str | None:
        candidates = (
            list(self._explicit_sockets)
            if self._explicit_sockets is not None
            else discover_configured_mpv_sockets()
        )
        for path in candidates:
            if Path(path).exists():
                return path
        return None

    def _run_session(self, sock_path: str) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        try:
            sock.connect(sock_path)
        except OSError:
            sock.close()
            return

        with self._sock_lock:
            self._active_sock = sock
        self._connected = True
        self.connection_changed.emit(True, "mpv 已連線")

        observe_cmds = [
            {"command": ["observe_property", 1, "sub-text"]},
            {"command": ["observe_property", 2, "media-title"]},
            {"command": ["observe_property", 3, "path"]},
            {"command": ["observe_property", 4, "sub-start"]},
            {"command": ["observe_property", 5, "sub-end"]},
            {"command": ["observe_property", 6, "pause"]},
            {"command": ["observe_property", 7, "speed"]},
        ]
        try:
            for cmd in observe_cmds:
                sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        except OSError:
            with self._sock_lock:
                self._active_sock = None
            sock.close()
            self._connected = False
            self.connection_changed.emit(False, "mpv 未連線")
            return

        state: dict[str, object] = {}
        pending_text = ""
        snapshot_id = 100
        buf = b""
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    if not raw_line.strip():
                        continue
                    try:
                        msg = json.loads(raw_line.decode("utf-8", errors="ignore"))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(msg, dict):
                        if msg.get("request_id") == 91:
                            if msg.get("error") == "success":
                                self.set_pause(False)
                            else:
                                self.navigation_failed.emit("mpv 在這個位置找不到可跳轉的字幕。")
                            continue
                        if msg.get("event") in ("start-file", "end-file"):
                            self.stop_sentence_loop()
                            self._last_sub_start = self._last_sub_end = None
                            pending_text = ""
                        if msg.get("event") == "property-change" and msg.get("name") == "sub-text":
                            pending_text = str(msg.get("data") or "").strip()
                            if pending_text:
                                snapshot_id += 1
                                request = {"command": ["expand-text", _SNAPSHOT_FORMAT], "request_id": snapshot_id}
                                try:
                                    with self._sock_lock:
                                        sock.sendall((json.dumps(request) + "\n").encode())
                                except OSError:
                                    return
                                state["sub_active"] = True
                                continue
                        if msg.get("request_id") == snapshot_id and pending_text:
                            ev = _subtitle_snapshot(str(msg.get("data") or ""))
                            if ev is None:
                                ev = MpvSubtitleEvent(pending_text, str(state.get("media_title") or ""),
                                                      str(state.get("video_path") or ""))
                            if ev.text != pending_text:
                                continue
                            pending_text = ""
                        else:
                            ev = parse_mpv_ipc_message(msg, state)
                        self._paused = bool(state.get("paused", False))
                        if ev is not None:
                            self._on_sub_active(ev.sub_start, ev.sub_end)
                            try:
                                self.subtitle_received.emit(ev)
                            except RuntimeError:
                                break
                        elif state.pop("sub_just_cleared", False):
                            self._on_sub_cleared()
        finally:
            self._cancel_speed_timer()
            self._sped_up = False
            self._sentence_loop = False
            self.loop_changed.emit(False)
            with self._sock_lock:
                self._active_sock = None
            sock.close()
            self._connected = False
            try:
                self.connection_changed.emit(False, "mpv 未連線")
            except RuntimeError:
                pass
