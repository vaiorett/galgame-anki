"""跨平台適配器層（Linux KDE Wayland + Wine 優先，完整預留 Windows Adapter 介面）。"""

from __future__ import annotations

import os

from galgame_anki.adapters.base import PlatformAdapter
from galgame_anki.adapters.linux_wayland import LinuxWaylandAdapter
from galgame_anki.adapters.windows import WindowsPlatformAdapter


def create_platform_adapter() -> PlatformAdapter:
    if os.name == "nt":
        return WindowsPlatformAdapter()
    return LinuxWaylandAdapter()


__all__ = [
    "PlatformAdapter",
    "LinuxWaylandAdapter",
    "WindowsPlatformAdapter",
    "create_platform_adapter",
]
