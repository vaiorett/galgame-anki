"""內建 Galgame 記憶體文本自動 Hook 引擎（支援 Linux Wine/Proton 與 Windows 原生）。

核心能力：
1. 自動下載並管理開源 TextractorCLI（x86 / x64）。
2. 解析遊戲執行檔 PE 檔頭，自動判別 32 位元（PE32）或 64 位元（PE32+）。
3. 在 Linux Wine 環境下自動對齊相同 WINEPREFIX，透過 `wine tasklist` 取得遊戲真實 Windows PID。
4. 以正確的 UTF-16LE 雙位元組邊界（b"\\n\\x00"）串流解析 TextractorCLI 輸出，
   對打字機逐字輸出型引擎（如 KiriKiriZ）進行防抖拼接，並自動評分鎖定主對話線程。
5. 支援一鍵切換線程、注入自訂 H-Code（特殊碼），或一鍵以相同 WINEPREFIX 開啟 Textractor GUI 掃描。
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx
from PySide6.QtCore import QObject, QTimer, Signal

from galgame_anki.config import get_data_dir
from galgame_anki.core.hook_cleaner import (
    clean_galgame_hook_text,
    is_progressive_extension,
    merge_hook_stream_chunks,
)
from galgame_anki.core.tokenizer import is_japanese_text

TEXTRACTOR_ZIP_URL = (
    "https://github.com/Artikash/Textractor/releases/download/v5.2.0/"
    "Textractor-5.2.0-Zip-Version-English-Only.zip"
)

# 解析 TextractorCLI 行格式：[Handle:PID:Addr:Ctx:Ctx2:HookName:HookCode] Text
_TEXTRACTOR_LINE_RE = re.compile(
    r"^\[([^:]+):([^:]+):([^:]+):([^:]+):([^:]+):([^:]+):([^\]]+)\]\s?(.*)$"
)

# 優先給高分的常見 Galgame 引擎與繪字 Hook 名稱
_PREFERRED_HOOK_KEYWORDS = (
    "kirikiri",
    "krkr",
    "artemis",
    "bgi",
    "ethornell",
    "siglus",
    "qlie",
    "catsystem",
    "majiro",
    "yuris",
    "system4",
    "entis",
    "realive",
    "pal",
    "cmvs",
    "livemaker",
    "willplus",
    "renpy",
    "unity",
    "getglyphoutline",
    "exttextout",
    "textout",
    "drawtext",
    "lstrlen",
)

_IGNORED_THREAD_NAMES = {"console", "clipboard"}

_WINE_SYSTEM_EXES = {
    "services.exe",
    "winedevice.exe",
    "plugplay.exe",
    "svchost.exe",
    "rpcss.exe",
    "explorer.exe",
    "tabtip.exe",
    "conhost.exe",
    "start.exe",
    "wineboot.exe",
    "winemenubuilder.exe",
    "rundll32.exe",
    "msiexec.exe",
    "cmd.exe",
    "tasklist.exe",
    "textractorcli.exe",
    "textractor.exe",
    "winedbg.exe",
}

ProgressCallback = Callable[[str, int], None]


@dataclass
class HookTargetProcess:
    """可掛載的 Galgame 目標進程描述。"""

    name: str
    exe_name: str
    win_pid: int
    arch: str = "x86"  # x86 | x64
    exe_path: str = ""
    wine_prefix: str = ""
    wine_command: str = "wine"
    linux_pid: int | None = None


@dataclass
class HookLineEvent:
    handle: str
    pid: str
    address: str
    context: str
    context2: str
    hook_name: str
    hook_code: str
    text: str

    @property
    def thread_key(self) -> str:
        return f"{self.handle}:{self.hook_name}:{self.hook_code}"


@dataclass
class HookThreadInfo:
    thread_key: str
    handle: str
    hook_name: str
    hook_code: str
    last_text: str = ""
    message_count: int = 0
    score: float = 0.0
    last_updated: float = field(default_factory=time.time)

    def display_label(self) -> str:
        sample = self.last_text.replace("\n", " ").strip()
        if len(sample) > 22:
            sample = sample[:22] + "…"
        return f"{self.hook_name} [{self.handle}] — {sample}" if sample else f"{self.hook_name} [{self.handle}]"


def detect_pe_arch(exe_path: Path) -> str:
    """直接讀取 Windows PE 檔頭判別 x86 (32-bit) 或 x64 (64-bit)，零外部依賴。"""
    try:
        with exe_path.open("rb") as fh:
            if fh.read(2) != b"MZ":
                return "x86"
            fh.seek(0x3C)
            pe_offset = struct.unpack("<I", fh.read(4))[0]
            fh.seek(pe_offset)
            if fh.read(4) != b"PE\x00\x00":
                return "x86"
            machine = struct.unpack("<H", fh.read(2))[0]
            # 0x8664 = AMD64, 0xAA64 = ARM64, 0x014C = i386
            return "x64" if machine in (0x8664, 0xAA64) else "x86"
    except (OSError, struct.error):
        return "x86"


def get_textractor_root() -> Path:
    return get_data_dir() / "tools" / "textractor"


def find_textractor_binary(arch: str = "x86", gui: bool = False) -> Path | None:
    """尋找本機已下載的 TextractorCLI.exe 或 Textractor.exe。"""
    root = get_textractor_root()
    if not root.exists():
        return None
    exe_name = "Textractor.exe" if gui else "TextractorCLI.exe"
    arch_dir = "x64" if arch == "x64" else "x86"
    candidates = [
        root / "Textractor" / arch_dir / exe_name,
        root / arch_dir / exe_name,
    ]
    for path in candidates:
        if path.exists():
            return path
    for found in root.rglob(exe_name):
        if arch_dir in found.parts:
            return found
    return None


def is_textractor_installed() -> bool:
    return find_textractor_binary("x86") is not None and find_textractor_binary("x64") is not None


def ensure_textractor_installed(progress: ProgressCallback | None = None) -> Path:
    """若尚未安裝內建 TextractorCLI，自動從 GitHub 下載並解壓至資料目錄。"""
    if is_textractor_installed():
        if progress:
            progress("內建 Textractor Hook 引擎已就緒", 100)
        return get_textractor_root()

    root = get_textractor_root()
    root.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("正在下載開源 Textractor 5.2.0 Hook 引擎 (~18 MB)...", 5)

    with tempfile.TemporaryDirectory(prefix="galanki_tr_") as tmpdir:
        zip_path = Path(tmpdir) / "textractor.zip"
        with httpx.Client(follow_redirects=True, timeout=120.0, headers={"User-Agent": "GalAnki/0.1"}) as client:
            with client.stream("GET", TEXTRACTOR_ZIP_URL) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with zip_path.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=131072):
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if progress and total > 0:
                            pct = 10 + int(downloaded * 75 / total)
                            progress(
                                f"下載 Textractor ({downloaded / 1048576:.1f}/{total / 1048576:.1f} MB)...",
                                min(85, pct),
                            )

        if progress:
            progress("正在解壓 Textractor (x86 / x64)...", 90)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(root)

    if not is_textractor_installed():
        raise RuntimeError("解壓完成但找不到 TextractorCLI.exe，請重試下載。")
    if progress:
        progress("✓ Textractor Hook 引擎安裝完成", 100)
    return root


def parse_textractor_line(line: str) -> HookLineEvent | None:
    """解析單行 TextractorCLI UTF-16LE 輸出。"""
    cleaned = line.strip("\r\n")
    if not cleaned:
        return None
    m = _TEXTRACTOR_LINE_RE.match(cleaned)
    if not m:
        return None
    return HookLineEvent(
        handle=m.group(1),
        pid=m.group(2),
        address=m.group(3),
        context=m.group(4),
        context2=m.group(5),
        hook_name=m.group(6),
        hook_code=m.group(7),
        text=m.group(8),
    )


def merge_incremental_text(buffered: str, incoming: str) -> str:
    """合併同線程快速連續到達的文本片段（自動折疊漸進前綴重繪與重疊拼接）。"""
    return merge_hook_stream_chunks(buffered, incoming, dedup=True)


def score_hook_thread(hook_name: str, text: str, message_count: int) -> float:
    """評分各 Hook 線程像「真實 Galgame 日語台詞」的機率，供自動鎖定主對話線程。"""
    lower_name = hook_name.lower()
    if lower_name in _IGNORED_THREAD_NAMES:
        return -1000.0

    score = 0.0
    for kw in _PREFERRED_HOOK_KEYWORDS:
        if kw in lower_name:
            score += 35.0
            break

    stripped = text.strip()
    if not stripped:
        return -500.0

    length = len(stripped)
    jp_chars = len(re.findall(r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]", stripped))
    kana_chars = len(re.findall(r"[\u3040-\u309f\u30a0-\u30ff]", stripped))

    if jp_chars == 0:
        score -= 40.0
    else:
        jp_ratio = jp_chars / max(1, length)
        score += jp_ratio * 45.0
        # 真實日語台詞通常含有平假名/片假名（而不只是選單漢字或檔案路徑）
        if kana_chars >= 2:
            score += 20.0

    if 4 <= length <= 180:
        score += 18.0
    elif length == 1:
        score -= 10.0
    elif length > 260:
        score -= 25.0

    # 對話標點加分
    if any(p in stripped for p in ("。", "、", "「", "」", "『", "』", "！", "？", "…")):
        score += 15.0

    # 避免把檔案路徑 / DLL 名稱誤判為台詞
    if re.search(r"\.(xp3|dll|exe|ks|tjs|png|ogg|wav|bmp)\b", stripped, re.IGNORECASE):
        score -= 60.0

    score += min(10.0, message_count * 0.5)
    return score


def _build_wine_env(wine_prefix: str = "", locale: str = "ja_JP.UTF-8") -> dict[str, str]:
    env = os.environ.copy()
    env["WINEDEBUG"] = "-all"
    if wine_prefix:
        env["WINEPREFIX"] = wine_prefix
    if locale:
        env.setdefault("LANG", locale)
    return env


def list_wine_tasklist(
    wine_command: str = "wine",
    wine_prefix: str = "",
) -> list[tuple[str, int]]:
    """在指定 WINEPREFIX 執行 `wine tasklist /FO CSV /NH`，回傳 (exe_name, windows_pid) 清單。"""
    wine_bin = wine_command.strip() or shutil.which("wine") or "wine"
    env = _build_wine_env(wine_prefix=wine_prefix)
    try:
        res = subprocess.run(
            [wine_bin, "tasklist", "/FO", "CSV", "/NH"],
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if res.returncode != 0 or not res.stdout:
        return []

    text = res.stdout.decode("utf-8", errors="ignore")
    results: list[tuple[str, int]] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        exe_name = row[0].strip()
        pid_str = row[1].strip()
        if exe_name.lower().endswith(".exe") and pid_str.isdigit():
            results.append((exe_name, int(pid_str)))
    return results


def discover_wine_game_targets(
    default_wine_command: str = "wine",
) -> list[HookTargetProcess]:
    """掃描所有執行中的 Wine/Proton Galgame，自動推斷 WINEPREFIX、PE 架構與 Windows PID。"""
    targets: list[HookTargetProcess] = []
    seen_prefixes: dict[str, list[dict[str, str | int]]] = {}

    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cmdline", "environ"]):
            info = proc.info
            cmdline = info.get("cmdline") or []
            name = str(info.get("name") or "")
            env = info.get("environ") or {}

            exe_arg = ""
            if name.lower().endswith(".exe"):
                exe_arg = name
            for arg in cmdline:
                if arg.lower().endswith(".exe"):
                    exe_arg = arg
                    break
            if not exe_arg:
                continue

            exe_basename = Path(exe_arg.replace("\\", "/")).name
            if exe_basename.lower() in _WINE_SYSTEM_EXES:
                continue

            prefix = str(env.get("WINEPREFIX") or "")
            # 嘗試從 cmdline 找出實際 Linux 檔案路徑以判別 32/64 位元
            linux_exe_path = ""
            if Path(exe_arg).exists():
                linux_exe_path = exe_arg
            elif exe_arg.lower().startswith("z:"):
                converted = exe_arg[2:].replace("\\", "/")
                if Path(converted).exists():
                    linux_exe_path = converted

            seen_prefixes.setdefault(prefix, []).append(
                {
                    "linux_pid": int(info["pid"]),
                    "exe_basename": exe_basename,
                    "exe_path": linux_exe_path,
                }
            )
    except Exception:
        pass

    # 若沒從 psutil 發現特別前綴，也檢查預設 Wine Prefix
    if not seen_prefixes:
        seen_prefixes[""] = []

    for prefix, proc_infos in seen_prefixes.items():
        win_procs = list_wine_tasklist(wine_command=default_wine_command, wine_prefix=prefix)
        info_by_lower = {str(p["exe_basename"]).lower(): p for p in proc_infos}

        for exe_name, win_pid in win_procs:
            if exe_name.lower() in _WINE_SYSTEM_EXES:
                continue
            matched = info_by_lower.get(exe_name.lower())
            exe_path = str(matched["exe_path"]) if matched else ""
            arch = detect_pe_arch(Path(exe_path)) if exe_path else "x86"
            targets.append(
                HookTargetProcess(
                    name=Path(exe_name).stem,
                    exe_name=exe_name,
                    win_pid=win_pid,
                    arch=arch,
                    exe_path=exe_path,
                    wine_prefix=prefix,
                    wine_command=default_wine_command,
                    linux_pid=int(matched["linux_pid"]) if matched else None,
                )
            )

    return targets


class GameHookManager(QObject):
    """管理 TextractorCLI 背景掛載、UTF-16LE 串流解析、防抖拼接與自動選線程。"""

    dialogue_received = Signal(str, str)       # (cleaned_sentence, thread_key)
    dialogue_extended = Signal(str, str)       # (extended_sentence, thread_key) 原地延長更新
    threads_updated = Signal(list, str)        # (list[HookThreadInfo], selected_key_or_auto)
    console_logged = Signal(str)               # Textractor 系統日誌
    hook_state_changed = Signal(bool, str)     # (is_attached, status_label)

    # 跨執行緒橋接用內部訊號
    _raw_event_signal = Signal(object)         # HookLineEvent
    _process_exited_signal = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._attached_target: HookTargetProcess | None = None
        self._stop_flag = threading.Event()

        self._threads: dict[str, HookThreadInfo] = {}
        self._selected_thread_key: str = "auto"  # "auto" 或指定 thread_key
        self._pending_buffers: dict[str, str] = {}
        self._flush_timers: dict[str, QTimer] = {}
        self._last_emitted_by_thread: dict[str, str] = {}
        self._last_emitted_ts: dict[str, float] = {}
        self._last_snapshot_ts: float = 0.0
        self._last_status_label: str = ""
        self.dedup_repeated: bool = True

        self._raw_event_signal.connect(self._on_hook_event_main_thread)
        self._process_exited_signal.connect(self._on_process_exited)

    @property
    def is_attached(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and self._attached_target is not None

    @property
    def attached_target(self) -> HookTargetProcess | None:
        return self._attached_target

    @property
    def selected_thread_key(self) -> str:
        return self._selected_thread_key

    def set_selected_thread(self, thread_key: str) -> None:
        """設定指定 Hook 線程，或傳入 'auto' 恢復自動選擇最佳日語對話線程。"""
        self._selected_thread_key = thread_key or "auto"
        self._emit_threads_snapshot(force=True)
        if thread_key != "auto" and thread_key in self._threads:
            info = self._threads[thread_key]
            if info.last_text:
                self.dialogue_received.emit(info.last_text, thread_key)

    def attach_target(self, target: HookTargetProcess) -> None:
        """啟動對應架構的 TextractorCLI 並自動注入指定 Windows PID。"""
        self.detach()

        cli_path = find_textractor_binary(arch=target.arch, gui=False)
        if cli_path is None:
            raise FileNotFoundError(
                f"找不到 {target.arch} 版 TextractorCLI.exe，請先點擊「安裝/初始化 Hook 引擎」。"
            )

        if os.name == "nt":
            cmd = [str(cli_path)]
            env = os.environ.copy()
        else:
            wine_bin = target.wine_command.strip() or shutil.which("wine") or "wine"
            cmd = [wine_bin, str(cli_path)]
            env = _build_wine_env(wine_prefix=target.wine_prefix)

        self._stop_flag.clear()
        self._threads.clear()
        self._pending_buffers.clear()
        self._last_emitted_by_thread.clear()
        self._selected_thread_key = "auto"

        self._proc = subprocess.Popen(
            cmd,
            cwd=str(cli_path.parent),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._attached_target = target

        self._reader_thread = threading.Thread(
            target=self._read_stdout_loop,
            args=(self._proc,),
            daemon=True,
        )
        self._reader_thread.start()

        # 送出 attach 指令（UTF-16LE）
        self.send_command(f"attach -P{target.win_pid}")
        self.console_logged.emit(
            f"[GalAnki] 已啟動 {target.arch} TextractorCLI 並注入 {target.exe_name} (WinPID={target.win_pid})"
        )
        self.hook_state_changed.emit(True, f"🪝 已掛載：{target.name} ({target.arch})")

    def send_command(self, command: str) -> bool:
        """向執行中的 TextractorCLI 發送指令（如 attach / detach / 自訂 H-Code）。"""
        if self._proc is None or self._proc.poll() is not None or self._proc.stdin is None:
            return False
        try:
            payload = (command.strip() + "\r\n").encode("utf-16le")
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
            return True
        except OSError:
            return False

    def inject_hook_code(self, hook_code: str) -> bool:
        """向目前掛載的遊戲注入使用者提供的特殊碼（H-Code）。"""
        code = hook_code.strip()
        if not code or self._attached_target is None:
            return False
        cmd = f"{code} -P{self._attached_target.win_pid}"
        ok = self.send_command(cmd)
        if ok:
            self.console_logged.emit(f"[GalAnki] 已發送自訂 Hook 特殊碼：{cmd}")
        return ok

    def launch_textractor_gui(self, target: HookTargetProcess | None = None) -> None:
        """進階備援：以相同 WINEPREFIX 開啟完整 Textractor.exe 視窗（可用於記憶體搜碼）。"""
        tgt = target or self._attached_target
        arch = tgt.arch if tgt else "x86"
        gui_path = find_textractor_binary(arch=arch, gui=True)
        if gui_path is None:
            raise FileNotFoundError("尚未安裝 Textractor，請先點「一鍵安裝 Hook 引擎」。")

        if os.name == "nt":
            subprocess.Popen([str(gui_path)], cwd=str(gui_path.parent))
        else:
            wine_bin = (tgt.wine_command if tgt else "") or shutil.which("wine") or "wine"
            prefix = tgt.wine_prefix if tgt else ""
            env = _build_wine_env(wine_prefix=prefix)
            subprocess.Popen(
                [wine_bin, str(gui_path)],
                cwd=str(gui_path.parent),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

    def detach(self) -> None:
        self._stop_flag.set()
        for timer in list(self._flush_timers.values()):
            timer.stop()
            timer.deleteLater()
        self._flush_timers.clear()
        self._pending_buffers.clear()

        if self._proc is not None:
            if self._attached_target is not None:
                self.send_command(f"detach -P{self._attached_target.win_pid}")
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None

        was_attached = self._attached_target is not None
        self._attached_target = None
        if was_attached:
            self.hook_state_changed.emit(False, "未掛載遊戲")

    def _read_stdout_loop(self, proc: subprocess.Popen) -> None:
        """在背景執行緒安全讀取 UTF-16LE 位元組串流，嚴格以 b'\\n\\x00' 切行避免錯位。"""
        assert proc.stdout is not None
        buf = b""
        while not self._stop_flag.is_set():
            try:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1024)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk

            # 以偶數位元組邊界尋找 UTF-16LE 換行符 (0x0A 0x00)
            while True:
                sep_idx = _find_utf16le_newline(buf)
                if sep_idx == -1:
                    break
                raw_line = buf[:sep_idx]
                buf = buf[sep_idx + 2 :]
                # 剝離結尾 \r\x00
                if raw_line.endswith(b"\r\x00"):
                    raw_line = raw_line[:-2]
                if len(raw_line) % 2 == 1:
                    raw_line = raw_line[:-1]
                try:
                    decoded = raw_line.decode("utf-16le", errors="ignore")
                except Exception:
                    continue
                ev = parse_textractor_line(decoded)
                if ev is None:
                    continue
                # 直接在背景執行緒過濾超大腳本/資源雜訊，絕不灌爆主視窗 Qt 事件佇列！
                name_low = ev.hook_name.lower()
                if name_low == "clipboard":
                    continue
                if name_low != "console" and len(ev.text) > 3500:
                    continue
                if name_low != "console":
                    ev.text = clean_galgame_hook_text(ev.text, dedup=self.dedup_repeated)
                    if not ev.text:
                        continue
                self._raw_event_signal.emit(ev)

        rc = proc.poll()
        if not self._stop_flag.is_set():
            self._process_exited_signal.emit(rc if rc is not None else -1)

    def _on_process_exited(self, rc: int) -> None:
        self.console_logged.emit(f"[GalAnki] TextractorCLI 已結束 (code={rc})")
        self._attached_target = None
        self._proc = None
        self.hook_state_changed.emit(False, "Hook 已中斷")

    def _on_hook_event_main_thread(self, ev: HookLineEvent) -> None:
        name_lower = ev.hook_name.lower()
        if name_lower == "console":
            txt = ev.text.strip()
            if txt:
                self.console_logged.emit(txt)
                # 只在偵測到知名 VN 引擎 Hook（而非 40 個通用 Win32 API）時更新標題徽章，避免洗版卡頓
                low = txt.lower()
                if "inserting hook:" in low and self._attached_target:
                    hook_label = txt.split(":", 2)[-1].strip()
                    if any(kw in hook_label.lower() for kw in _PREFERRED_HOOK_KEYWORDS[:18]):
                        new_label = f"🪝 {self._attached_target.name} ({hook_label})"
                        if new_label != self._last_status_label:
                            self._last_status_label = new_label
                            self.hook_state_changed.emit(True, new_label)
            return

        if name_lower == "clipboard":
            return

        key = ev.thread_key
        prev = self._pending_buffers.get(key, "")
        self._pending_buffers[key] = merge_hook_stream_chunks(
            prev, ev.text, dedup=self.dedup_repeated
        )

        # 避免遊戲啟動瞬間產出數十條無關線程把記憶體與下拉選單灌爆
        if key not in self._threads and len(self._threads) < 60:
            self._threads[key] = HookThreadInfo(
                thread_key=key,
                handle=ev.handle,
                hook_name=ev.hook_name,
                hook_code=ev.hook_code,
            )

        timer = self._flush_timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._flush_thread_buffer(k))
            self._flush_timers[key] = timer
        timer.start(380)

    def _flush_thread_buffer(self, thread_key: str) -> None:
        raw = self._pending_buffers.pop(thread_key, "")
        cleaned = clean_galgame_hook_text(raw, dedup=self.dedup_repeated)
        if not cleaned or len(cleaned) > 400:
            return

        info = self._threads.get(thread_key)
        if info is None:
            return

        now = time.time()
        last_sent = self._last_emitted_by_thread.get(thread_key, "")
        last_ts = self._last_emitted_ts.get(thread_key, 0.0)

        # 若剛送出 3 秒內同一線程又補來更長的延伸（例如遊戲在「、」「……」停頓後繼續印字），
        # 自動合併並觸發原地更新，徹底解決少字問題！
        is_extension = False
        if last_sent and (now - last_ts) <= 3.0:
            if cleaned == last_sent or last_sent.startswith(cleaned):
                # 只是已顯示句子的殘影前綴，忽略即可
                return
            merged_with_prev = merge_hook_stream_chunks(
                last_sent, cleaned, dedup=self.dedup_repeated
            )
            if is_progressive_extension(last_sent, merged_with_prev):
                cleaned = merged_with_prev
                is_extension = True

        info.message_count += 1
        info.last_text = cleaned
        info.last_updated = now
        info.score = score_hook_thread(info.hook_name, cleaned, info.message_count)

        self._emit_threads_snapshot()

        if last_sent == cleaned:
            return

        active_key = self._resolve_active_thread_key()
        if thread_key == active_key and is_japanese_text(cleaned):
            self._last_emitted_by_thread[thread_key] = cleaned
            self._last_emitted_ts[thread_key] = now
            if is_extension:
                self.dialogue_extended.emit(cleaned, thread_key)
            else:
                self.dialogue_received.emit(cleaned, thread_key)

    def _resolve_active_thread_key(self) -> str | None:
        if self._selected_thread_key != "auto":
            return self._selected_thread_key
        if not self._threads:
            return None
        best = max(self._threads.values(), key=lambda t: (t.score, t.last_updated))
        return best.thread_key if best.score > 0 else None

    def _emit_threads_snapshot(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_snapshot_ts) < 0.45:
            return
        self._last_snapshot_ts = now
        ordered = sorted(
            self._threads.values(),
            key=lambda t: (-t.score, -t.last_updated),
        )
        self.threads_updated.emit(ordered, self._selected_thread_key)


def _find_utf16le_newline(buf: bytes) -> int:
    """只在偶數位元組邊界尋找 UTF-16LE 換行符 b'\\n\\x00'，防止破壞漢字編碼。"""
    start = 0
    while True:
        idx = buf.find(b"\n\x00", start)
        if idx == -1:
            return -1
        if idx % 2 == 0:
            return idx
        start = idx + 1
