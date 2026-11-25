"""GalAnki 命令列與 GUI 主入口點。"""

from __future__ import annotations

import argparse
import sys

from galgame_anki.adapters import create_platform_adapter
from galgame_anki.config import AppConfig
from galgame_anki.core.anki_client import AnkiConnectClient


def _run_cli_diagnose() -> int:
    adapter = create_platform_adapter()
    diag = adapter.diagnose_environment()
    print(f"平台：{diag.platform_name}（工作階段：{diag.session_type}）")
    for name, ok in diag.tools.items():
        print(f"  [{'OK' if ok else '--'}] {name}")
    for note in diag.notes:
        print(f"  {note}")
    return 0


def _run_cli_init_anki(config: AppConfig) -> int:
    client = AnkiConnectClient(config.anki_url)
    report = client.one_click_initialize(config.deck_name, config.note_type)
    for line in report.messages:
        print(line)
    return 0 if report.connected else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="galgame-anki",
        description="GalAnki — 邊玩 Galgame 邊學日語的一鍵 Anki 製卡工具",
    )
    parser.add_argument(
        "--init-anki",
        action="store_true",
        help="不開啟視窗，直接於終端機執行一鍵 Anki 初始化（建立牌組、三階段模板與 FSRS 檢查）",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="檢查目前系統環境（Wayland / Wine / Anki / FFmpeg）",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="啟動 GUI 並載入原攻略經典範例句",
    )
    parser.add_argument(
        "--window",
        action="store_true",
        help="啟動時直接開啟主控制台視窗（預設收進系統匣、只顯示字幕覆蓋層）",
    )
    args = parser.parse_args(argv)
    config = AppConfig.load()

    if args.diagnose:
        return _run_cli_diagnose()
    if args.init_anki:
        return _run_cli_init_anki(config)

    from pathlib import Path

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from galgame_anki.ui.main_window import MainWindow
    from galgame_anki.ui.theme import build_stylesheet

    app = QApplication(sys.argv)
    app.setApplicationName("GalAnki")
    app.setDesktopFileName("galanki")
    icon_path = Path(__file__).resolve().parent / "data" / "galanki.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(build_stylesheet(config.ui_theme))

    window = MainWindow(config=config, load_demo=args.demo)
    if config.start_in_tray_overlay and window._tray_available and not args.window and not args.demo:
        app.setQuitOnLastWindowClosed(False)
        window.show_overlay_only_mode()
        window.undo_toast.show_info(
            "🌸 GalAnki 已於系統匣待命｜覆蓋層已開啟（懸停查詞・一鍵收錄・點系統匣開控制台）",
            duration_ms=4200,
        )
    else:
        if window._tray_available and config.start_in_tray_overlay:
            app.setQuitOnLastWindowClosed(False)
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
