"""應用程式設定管理模組。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def get_config_dir() -> Path:
    """回傳跨平台設定目錄。"""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        base = Path(xdg_config)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    path = base / "galgame-anki"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """回傳跨平台資料目錄（存放詞典 SQLite 與暫存媒體）。"""
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        base = Path(xdg_data)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    path = base / "galgame-anki"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class AppConfig:
    """持久化設定資料結構。"""

    # Anki 設定
    anki_url: str = "http://127.0.0.1:8765"
    deck_name: str = "Galgame日語"
    note_type: str = "GalImmersion-OneClick"
    stage_mode: str = "lv2"  # lv1 | lv2 | lv3
    daily_card_limit: int = 20
    auto_sync_known_words: bool = True
    auto_launch_anki: bool = True  # 啟動 GalAnki 時自動在背景啟動 Anki 並最小化

    # 詞典顯示模式: auto (跟隨 stage_mode) | bilingual | monolingual
    dict_mode: str = "auto"

    # 文本接收來源
    ws_enabled: bool = True
    ws_host: str = "127.0.0.1"
    ws_port: int = 6677
    clipboard_watch: bool = True
    okular_companion_enabled: bool = False
    dedup_repeated_chars: bool = True
    ignore_non_japanese: bool = True

    # 遊戲與啟動器 (Linux Wine / Proton & Windows)
    current_game_name: str = ""
    wine_command: str = "wine"
    wine_locale: str = "ja_JP.UTF-8"
    recent_games: list[dict[str, str]] = field(default_factory=list)

    # 媒體擷取與 Refold 精煉製卡（預設一鍵直寫 Anki＋右下角可撤銷；按住 Shift 點擊可先編輯）
    capture_audio_on_mine: bool = True
    capture_screenshot_on_mine: bool = True
    quick_forge_before_mine: bool = False
    active_voice_pack_id: str = ""                # 可手動指定目前優先使用的人聲索引包（空字串＝自動依遊戲配對）
    # mpv 動畫進階沉浸（sub-transition 無字幕自動倍速 ＋ 精讀句尾暫停）
    mpv_auto_speedup: bool = False
    mpv_fast_speed: float = 2.0
    mpv_auto_pause_sub_end: bool = False

    # 啟動預設直接顯示主視窗，覆蓋層僅於手動開啟時顯示（勾選後才啟動即收進系統匣）
    start_in_tray_overlay: bool = False
    ui_theme: str = "sakura"  # sakura (櫻霧輕透) | starlight (星夜琉璃)
    overlay_opacity: float = 0.90
    overlay_font_size: int = 23
    overlay_furigana_in_lv1: bool = True
    overlay_x: int = -1
    overlay_y: int = -1
    overlay_width: int = 820
    overlay_height: int = 128
    overlay_locked: bool = False
    overlay_hover_lookup: bool = True
    overlay_always_on_top: bool = True     # 預設真實置頂（於 KDE Wayland 透過 KWin 原生 keepAbove 確保永不掉到遊戲／mpv 後方）
    overlay_replace_mode: bool = True      # 預設開啟「遮蓋替換模式」：不透明蓋住遊戲原對話文字，不看 2 份字幕！
    overlay_backdrop: str = "vn_dark"      # vn_dark (深黑對話框) | vn_paper (暖白對話框) | cinema_black (純黑字幕條)
    overlay_text_align: str = "left"       # left (左對齊) | center (居中對齊)
    overlay_blind_listen: bool = False     # 選用「先聽後看／盲聽模式」：預設關閉；開啟後隱藏台詞直到滑鼠懸停或按 ↺ 重聽
    overlay_guide_dismissed: bool = False
    first_run_completed: bool = False

    @classmethod
    def config_path(cls) -> Path:
        return get_config_dir() / "config.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        target = path or cls.config_path()
        if not target.exists():
            return cls()
        try:
            raw: Any = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in raw.items() if k in valid_fields}
        return cls(**filtered)

    def save(self, path: Path | None = None) -> Path:
        target = path or self.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), ensure_ascii=False, indent=2)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
        return target

    def effective_dict_preference(self) -> str:
        """依階段模式決定預設優先顯示雙語或單語詞典（登樓撤梯）。"""
        if self.dict_mode in ("bilingual", "monolingual"):
            return self.dict_mode
        return "monolingual" if self.stage_mode == "lv3" else "bilingual"

    def add_recent_game(self, name: str, exe_path: str) -> None:
        self.current_game_name = name
        self.recent_games = [
            g for g in self.recent_games if g.get("path") != exe_path
        ]
        self.recent_games.insert(0, {"name": name, "path": exe_path})
        self.recent_games = self.recent_games[:12]
