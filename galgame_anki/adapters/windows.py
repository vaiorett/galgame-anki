"""Windows 平台適配器預留實作（支援 Locale Emulator / 未來擴充 LunaHook DLL、WASAPI 與 WGC）。"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from galgame_anki.adapters.base import (
    DetectedGameProcess,
    EnvironmentDiagnostics,
    PlatformAdapter,
)
from galgame_anki.core.anki_client import is_ankiconnect_installed_locally


class WindowsPlatformAdapter(PlatformAdapter):
    """Windows 原生適配器介面（核心邏輯與 Linux 共用，系統底層由此擴充）。"""

    @property
    def platform_name(self) -> str:
        return "Windows (Native)"

    def diagnose_environment(self) -> EnvironmentDiagnostics:
        tools = {
            "anki": shutil.which("anki") is not None,
            "ankiconnect_addon": is_ankiconnect_installed_locally(),
            "leproc": shutil.which("LEProc.exe") is not None,
            "ffmpeg": shutil.which("ffmpeg") is not None,
        }
        notes = [
            "✓ Windows 原生介面已就緒：可透過 WebSocket (ws://127.0.0.1:6677) 或剪貼簿直接接收 LunaHook / Textractor / Agent 文本。",
            "ℹ 後續可在此適配器中直接掛載 LunaHook DLL、WASAPI Process Loopback 與 Windows Graphics Capture。",
        ]
        return EnvironmentDiagnostics(
            platform_name=self.platform_name,
            session_type="win32",
            tools=tools,
            notes=notes,
        )

    def detect_running_games(self) -> list[DetectedGameProcess]:
        return []

    def launch_game_jp_locale(
        self,
        exe_path: Path,
        launcher_cmd: str = "",
        locale: str = "ja_JP.UTF-8",
    ) -> int:
        if not exe_path.exists():
            raise FileNotFoundError(f"找不到遊戲執行檔：{exe_path}")
        if launcher_cmd.strip():
            try:
                parts = shlex.split(launcher_cmd.strip(), posix=False) or [launcher_cmd.strip()]
            except ValueError:
                parts = [launcher_cmd.strip()]
            cmd = [*parts, str(exe_path)]
        else:
            le = shutil.which("LEProc.exe")
            cmd = [le, str(exe_path)] if le else [str(exe_path)]
        proc = subprocess.Popen(cmd, cwd=str(exe_path.parent))
        return proc.pid

    def capture_screenshot_webp(self) -> tuple[bytes, str] | None:
        # 預留給 Windows Graphics Capture / Pillow ImageGrab 實作
        return None
