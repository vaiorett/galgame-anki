"""Linux (KDE Wayland + Wine/Proton + PipeWire) 平台適配器實作。"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from galgame_anki.adapters.base import (
    DetectedGameProcess,
    EnvironmentDiagnostics,
    PlatformAdapter,
)
from galgame_anki.adapters.hook_engine import is_textractor_installed
from galgame_anki.adapters.ocr_engine import list_tesseract_japanese_langs
from galgame_anki.core.anki_client import is_ankiconnect_installed_locally

# 過濾掉 Wine 內建背景服務進程，只保留實際遊戲執行檔
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
}


class LinuxWaylandAdapter(PlatformAdapter):
    """專為 Linux KDE Wayland + Wine/Proton 最佳化的系統適配器。"""

    @property
    def platform_name(self) -> str:
        return "Linux (Wayland / Wine)"

    def diagnose_environment(self) -> EnvironmentDiagnostics:
        session = os.environ.get("XDG_SESSION_TYPE", "unknown")
        anki_installed = (
            shutil.which("anki") is not None
            or (Path.home() / ".var" / "app" / "net.ankiweb.Anki").exists()
        )
        jp_ocr_langs = list_tesseract_japanese_langs()
        tools = {
            "anki": anki_installed,
            "ankiconnect_addon": is_ankiconnect_installed_locally(),
            "wine": shutil.which("wine") is not None,
            "textractor_hook": is_textractor_installed(),
            "tesseract_jpn_ocr": "jpn" in jp_ocr_langs,
            "wl-paste": shutil.which("wl-paste") is not None,
            "spectacle": shutil.which("spectacle") is not None,
            "ffmpeg": shutil.which("ffmpeg") is not None,
        }
        notes: list[str] = []
        if tools["textractor_hook"]:
            notes.append("✓ 內建 TextractorCLI (x86/x64) 自動 Hook 引擎已就緒，啟動遊戲將自動注入擷取台詞。")
        else:
            notes.append("ℹ 尚未下載內建 TextractorCLI，首次啟動遊戲或開啟「🪝 Hook 管理」時會自動下載。")
        if tools["tesseract_jpn_ocr"] and tools["spectacle"]:
            notes.append("✓ 已偵測到 Tesseract 日語 OCR + Spectacle，支援「📷 一鍵框選對話框 OCR」兜底。")
        if session == "wayland" and tools["wl-paste"]:
            notes.append("✓ 已偵測到 Wayland + wl-paste，支援背景自動監聽 Wine 遊戲剪貼簿文本。")
        elif session == "wayland":
            notes.append("⚠ Wayland 環境建議安裝 wl-clipboard (wl-paste)，才能在遊戲全螢幕/失焦時監聽剪貼簿。")
        if tools["wine"]:
            notes.append("✓ 已偵測到 Wine，可直接從本工具以日區環境（ja_JP.UTF-8）一鍵啟動生肉 Galgame。")
        if tools["ffmpeg"]:
            notes.append("✓ 已偵測到 FFmpeg，支援遊戲解包純人聲轉碼 MP3 與 mpv 原聲／截圖擷取。")
        if not tools["ankiconnect_addon"]:
            notes.append("ℹ 尚未在本機 Anki 外掛目錄偵測到 AnkiConnect (2055492159)，請在 Anki 中安裝並重啟。")

        return EnvironmentDiagnostics(
            platform_name=self.platform_name,
            session_type=session,
            tools=tools,
            notes=notes,
        )

    def detect_running_games(self) -> list[DetectedGameProcess]:
        detected: list[DetectedGameProcess] = []
        try:
            import psutil

            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                info = proc.info
                cmdline = info.get("cmdline") or []
                joined = " ".join(cmdline)
                name = str(info.get("name") or "")

                exe_token = ""
                if name.lower().endswith(".exe"):
                    exe_token = name
                else:
                    for arg in cmdline:
                        if arg.lower().endswith(".exe"):
                            exe_token = arg
                            break

                if not exe_token:
                    continue
                base_exe = Path(exe_token.replace("\\", "/")).name
                if base_exe.lower() in _WINE_SYSTEM_EXES:
                    continue

                runner = "proton" if "proton" in joined.lower() else "wine"
                detected.append(
                    DetectedGameProcess(
                        pid=int(info["pid"]),
                        name=Path(base_exe).stem,
                        exe_path=exe_token,
                        runner=runner,
                    )
                )
        except Exception:
            pass
        return detected

    def launch_game_jp_locale(
        self,
        exe_path: Path,
        launcher_cmd: str = "",
        locale: str = "ja_JP.UTF-8",
    ) -> int:
        if not exe_path.exists():
            raise FileNotFoundError(f"找不到遊戲執行檔：{exe_path}")

        wine_bin = launcher_cmd.strip() or shutil.which("wine") or "wine"
        env = os.environ.copy()
        env["LANG"] = locale
        env["LC_ALL"] = locale
        env["LC_CTYPE"] = locale
        env["TZ"] = "Asia/Tokyo"

        proc = subprocess.Popen(
            [wine_bin, str(exe_path)],
            cwd=str(exe_path.parent),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc.pid

    def capture_screenshot_webp(self) -> tuple[bytes, str] | None:
        """透過 KDE Spectacle 擷取當前活動視窗，並轉為輕量 WebP（若可用）。"""
        spectacle = shutil.which("spectacle")
        if not spectacle:
            return None

        ts = int(time.time() * 1000)
        with tempfile.TemporaryDirectory(prefix="galanki_shot_") as tmpdir:
            png_path = Path(tmpdir) / f"gal_{ts}.png"
            try:
                res = subprocess.run(
                    [spectacle, "-b", "-a", "-n", "-o", str(png_path)],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return None

            if res.returncode != 0 or not png_path.exists():
                return None

            raw_png = png_path.read_bytes()
            try:
                from PIL import Image

                img = Image.open(io.BytesIO(raw_png))
                img.thumbnail((960, 540))
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=78)
                return buf.getvalue(), f"gal_{ts}.webp"
            except Exception:
                return raw_png, f"gal_{ts}.png"
