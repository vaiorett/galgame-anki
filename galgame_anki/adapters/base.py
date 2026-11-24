"""跨平台適配器抽象介面（方便未來無痛擴充 Windows 原生引擎）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DetectedGameProcess:
    pid: int
    name: str
    exe_path: str = ""
    runner: str = "native"  # wine | proton | native


@dataclass
class EnvironmentDiagnostics:
    platform_name: str
    session_type: str
    tools: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class PlatformAdapter(ABC):
    """跨平台系統能力介面：遊戲日區啟動、進程偵測、環境診斷與可選截圖。"""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """回傳平台識別名稱。"""

    @abstractmethod
    def diagnose_environment(self) -> EnvironmentDiagnostics:
        """檢查系統必備工具（Wine、Wayland 剪貼簿、Anki、FFmpeg 等）。"""

    @abstractmethod
    def detect_running_games(self) -> list[DetectedGameProcess]:
        """偵測目前執行中的 Galgame 進程。"""

    @abstractmethod
    def launch_game_jp_locale(
        self,
        exe_path: Path,
        launcher_cmd: str = "",
        locale: str = "ja_JP.UTF-8",
    ) -> int:
        """以日語區域環境一鍵啟動生肉 Galgame，回傳子進程 PID。"""

    @abstractmethod
    def capture_screenshot_webp(self) -> tuple[bytes, str] | None:
        """（可選）擷取目前遊戲畫面並回傳 (webp_bytes, filename)。不支援或失敗時回傳 None。"""
