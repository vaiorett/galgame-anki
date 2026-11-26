"""主視窗／覆蓋層生命週期、手動啟動 Anki、外置 Okular 剪貼簿伴讀與全域字重降檔回歸測試。"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_anki.adapters.okular_bridge import (
    OkularContext,
    clean_okular_clipboard_text,
    parse_okular_document_title,
    query_okular_context,
)
from galgame_anki.adapters.text_sources import TextIngestionManager
from galgame_anki.config import AppConfig
from galgame_anki.ui.main_window import MainWindow
from galgame_anki.ui.theme import build_stylesheet, soften_rich_html


class TestOkularCompanionAndLaunch(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_stays_visible_overlay_manual_and_manual_anki_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp):
            cfg = AppConfig(
                anki_url="http://127.0.0.1:1",
                ws_enabled=False,
                clipboard_watch=False,
                auto_sync_known_words=False,
                auto_launch_anki=False,  # 模擬使用者關閉開機自動啟動 Anki
                start_in_tray_overlay=False,
            )
            win = MainWindow(config=cfg, load_demo=True)
            win.mpv_bridge.stop()
            win._tray_available = True  # 即使系統匣可用，開覆蓋層也不應隱藏主頁
            self.addCleanup(lambda: (setattr(win, "_force_quit", True), win.close()))

            # 0) 確認已完全移除內建閱讀器、漫畫與 OCR 功能，且頂列維持極簡
            self.assertFalse(hasattr(win, "open_media_reader"))
            self.assertFalse(hasattr(win, "_reader_window"))
            self.assertFalse(hasattr(win, "ocr_engine"))
            self.assertFalse(hasattr(win, "trigger_region_ocr"))
            self.assertFalse(hasattr(win, "ocr_btn"))
            self.assertFalse(hasattr(win.overlay, "ocr_btn"))

            # 1) 預設開啟主頁且覆蓋層隱藏
            win.show()
            self.app.processEvents()
            self.assertTrue(win.isVisible())
            self.assertFalse(win.overlay.isVisible())

            # 2) 手動打開覆蓋層後，主頁仍維持開啟不隱藏
            win.overlay_btn.click()
            self.app.processEvents()
            self.assertTrue(win.overlay.isVisible())
            self.assertTrue(win.isVisible())

            # 關閉覆蓋層，主頁仍開啟
            win.overlay_btn.click()
            self.app.processEvents()
            self.assertFalse(win.overlay.isVisible())
            self.assertTrue(win.isVisible())

            # 3) 即使 auto_launch_anki=False，手動點擊「啟動 Anki」仍必須實際呼叫啟動且不強制最小化
            launched_calls: list[bool] = []

            def _fake_launch(minimize_on_wayland: bool = True):
                launched_calls.append(minimize_on_wayland)
                return False, "測試回報：Flatpak Anki 模擬錯誤"

            with (
                patch.object(win.anki, "ping", side_effect=RuntimeError("offline")),
                patch("galgame_anki.ui.main_window.launch_anki_background", side_effect=_fake_launch),
            ):
                # 開機自動同步不應啟動 Anki（因 auto_launch_anki=False）
                win._background_sync_anki(allow_auto_launch=True, force_launch=False)
                for _ in range(30):
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertEqual(launched_calls, [])

                # 使用者手動點「啟動 Anki」按鈕：必須呼叫 launch_anki_background(minimize_on_wayland=False)
                win._on_anki_pill_clicked()
                for _ in range(40):
                    self.app.processEvents()
                    if launched_calls and "模擬錯誤" in win.statusBar().currentMessage():
                        break
                    time.sleep(0.01)
                self.assertEqual(launched_calls, [False])
                self.assertIn("模擬錯誤", win.statusBar().currentMessage())

    def test_okular_dbus_parser_and_clipboard_line_join(self) -> None:
        # 1) PDF / EPUB 折行清理：日文字元之間無縫合併，英文單詞之間保留空格
        self.assertEqual(
            clean_okular_clipboard_text("外の世界を知りたいと\nいう想いは、\n募る一方だ。"),
            "外の世界を知りたいという想いは、募る一方だ。",
        )
        self.assertEqual(
            clean_okular_clipboard_text("Natural language\nprocessing in Japan"),
            "Natural language processing in Japan",
        )
        self.assertEqual(
            parse_okular_document_title("file:///home/user/Books/%E6%9C%AC%E5%A5%BD%E3%81%8D%E3%81%AE%E4%B8%8B%E5%89%8B%E4%B8%8A.epub"),
            "本好きの下剋上",
        )

        # 2) 模擬 qdbus-qt6 回應解析執行中 Okular 的書名與頁碼
        def _fake_qdbus(cmd, **_kwargs):
            if len(cmd) == 1:
                return subprocess.CompletedProcess(cmd, 0, stdout="org.kde.okular-1002\norg.kde.okular-2045\n", stderr="")
            method = cmd[-1]
            if method == "org.kde.okular.currentDocument":
                return subprocess.CompletedProcess(cmd, 0, stdout="file:///books/%E6%98%9F%E9%99%8D%E3%82%8B%E5%9B%B3%E6%9B%B8%E9%A4%A8.epub\n", stderr="")
            if method == "org.kde.okular.currentPage":
                return subprocess.CompletedProcess(cmd, 0, stdout="17\n", stderr="")
            if method == "org.kde.okular.pages":
                return subprocess.CompletedProcess(cmd, 0, stdout="192\n", stderr="")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        with patch("galgame_anki.adapters.okular_bridge.subprocess.run", side_effect=_fake_qdbus):
            ctx = query_okular_context(qdbus_exe="/usr/bin/qdbus-qt6")
            self.assertIsNotNone(ctx)
            assert ctx is not None
            self.assertEqual(ctx.title, "星降る図書館")
            self.assertEqual(ctx.page, 17)
            self.assertEqual(ctx.total_pages, 192)
            self.assertEqual(ctx.label, "星降る図書館 · p.17")

        # Okular 未開啟時靜默回傳 None
        with patch(
            "galgame_anki.adapters.okular_bridge.subprocess.run",
            return_value=subprocess.CompletedProcess(["qdbus"], 0, stdout="org.freedesktop.DBus\n", stderr=""),
        ):
            self.assertIsNone(query_okular_context(qdbus_exe="/usr/bin/qdbus-qt6"))

    def test_okular_clipboard_companion_end_to_end(self) -> None:
        mgr = TextIngestionManager()
        self.addCleanup(mgr.shutdown)

        # 剪貼簿文字進流自動接合 Okular PDF 折行
        got_sents: list[str] = []
        mgr.sentence_received.connect(lambda txt, _src: got_sents.append(txt))
        mgr._handle_raw_text("約束した\nはずなのに、\nもう忘れてしまったの？", source="clipboard")
        self.assertEqual(got_sents, ["約束したはずなのに、もう忘れてしまったの？"])

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp):
            cfg = AppConfig(
                anki_url="http://127.0.0.1:1",
                ws_enabled=False,
                clipboard_watch=False,
                okular_companion_enabled=False,
                auto_sync_known_words=False,
                auto_launch_anki=False,
                start_in_tray_overlay=False,
            )
            win = MainWindow(config=cfg, load_demo=True)
            win.mpv_bridge.stop()
            self.addCleanup(lambda: (setattr(win, "_force_quit", True), win.close()))

            fake_ctx = OkularContext(title="本好きの下剋上", page=23, total_pages=310)
            with (
                patch.object(win.ingestion, "start_clipboard_watch", return_value="mocked") as m_watch,
                patch("galgame_anki.ui.main_window.query_okular_context", return_value=fake_ctx),
            ):
                win._toggle_okular_companion(True)
                self.assertTrue(win.config.okular_companion_enabled)
                self.assertTrue(win.config.clipboard_watch)
                m_watch.assert_called_once()

                win.ingestion._handle_raw_text("だって地球が\n壊れたら、\nもう会えない。", source="clipboard")
                for _ in range(40):
                    self.app.processEvents()
                    if win._current_record and win._current_record.media_title == "本好きの下剋上 · p.23":
                        break
                    time.sleep(0.02)

            rec = win._current_record
            self.assertIsNotNone(rec)
            assert rec is not None
            self.assertEqual(rec.raw_text, "だって地球が壊れたら、もう会えない。")
            self.assertEqual(rec.media_title, "本好きの下剋上 · p.23")

    def test_global_font_weights_stepped_down_one_tier_and_no_manga_modules(self) -> None:
        """驗證全域 UI 與核心 HTML 模板已降一檔，且已徹底移除漫畫與 OCR 模組。"""
        repo_root = Path(__file__).resolve().parents[1] / "galgame_anki"
        self.assertFalse((repo_root / "core" / "novel_reader.py").exists())
        self.assertFalse((repo_root / "core" / "manga_reader.py").exists())
        self.assertFalse((repo_root / "core" / "manga_ocr_engine.py").exists())
        self.assertFalse((repo_root / "ui" / "reader_window.py").exists())
        self.assertFalse((repo_root / "ui" / "okular_dialog.py").exists())

        heavy_re = re.compile(r"font-weight\s*:\s*(?:600|700|bold)|setBold\(True\)")
        offenders: list[str] = []
        for py_path in sorted(repo_root.rglob("*.py")):
            text = py_path.read_text(encoding="utf-8")
            for idx, line in enumerate(text.splitlines(), 1):
                if heavy_re.search(line):
                    offenders.append(f"{py_path.relative_to(repo_root)}:{idx}: {line.strip()}")
        self.assertEqual(offenders, [], f"偵測到未降檔的過粗字重：{offenders}")

        for theme in ("sakura", "starlight"):
            qss = build_stylesheet(theme)
            self.assertNotIn("font-weight: 700", qss)
            self.assertNotIn("font-weight: 600", qss)
            self.assertIn("font-weight: 500", qss)

        self.assertEqual(
            soften_rich_html("<b>標題</b>與<strong>重點</strong>"),
            '<span style="font-weight:500;">標題</span>與<span style="font-weight:500;">重點</span>',
        )


if __name__ == "__main__":
    unittest.main()
