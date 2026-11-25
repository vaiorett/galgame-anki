"""單螢幕隱形伴讀、一鍵直寫＋右下角撤銷、背景自動啟動 Anki 與極簡 UI 減重自動化測試。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_anki.adapters.hook_engine import HookThreadInfo
from galgame_anki.adapters.window_pin import (
    TOAST_WINDOW_TITLE,
    build_kwin_minimize_anki_script,
    build_kwin_pin_script,
)
from galgame_anki.config import AppConfig
from galgame_anki.core.anki_client import (
    AnkiConnectClient,
    detect_anki_launch_command,
    launch_anki_background,
)
from galgame_anki.ui.main_window import MainWindow
from galgame_anki.ui.settings_dialog import SettingsDialog


class TestSingleScreenUX(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_defaults_and_kwin_scripts_for_single_screen_mode(self) -> None:
        cfg = AppConfig()
        self.assertTrue(cfg.start_in_tray_overlay)
        self.assertTrue(cfg.auto_launch_anki)
        self.assertFalse(cfg.quick_forge_before_mine)

        # KWin 腳本涵蓋右下角收錄通知條置頂＋一次性自動最小化 Anki（不誤傷 GalAnki）
        pin_js = build_kwin_pin_script()
        self.assertIn(TOAST_WINDOW_TITLE, pin_js)
        self.assertIn("positionToastBottomRight", pin_js)

        min_js = build_kwin_minimize_anki_script(15000)
        self.assertIn("w.minimized = true", min_js)
        self.assertIn("GalAnki", min_js)
        self.assertIn("done = true", min_js)

        # offscreen 測試環境絕不實際啟動外部 Anki
        ok, msg = launch_anki_background()
        self.assertFalse(ok)
        self.assertIn("測試模式", msg)

        # 偵測本機 Anki（若有安裝 Flatpak 或原生 anki 應回傳非空清單）
        cmd = detect_anki_launch_command()
        if cmd is not None:
            self.assertTrue(any("anki" in part.lower() for part in cmd))

    def test_anki_delete_notes_rpc(self) -> None:
        client = AnkiConnectClient("http://127.0.0.1:8765")
        calls: list[tuple[str, dict]] = []

        def _fake_invoke(action: str, **params):
            calls.append((action, params))
            return None

        with patch.object(client, "invoke", side_effect=_fake_invoke):
            client.delete_notes([101, 202])
        self.assertEqual(calls, [("deleteNotes", {"notes": [101, 202]})])

    def test_one_click_direct_mine_undo_and_shift_forge_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                ws_enabled=False,
                clipboard_watch=False,
                auto_sync_known_words=False,
                auto_launch_anki=False,
                capture_audio_on_mine=False,
                capture_screenshot_on_mine=False,
                quick_forge_before_mine=False,
                start_in_tray_overlay=True,
                ui_theme="starlight",
            )
            cfg.save(Path(tmp) / "config.json")
            win = MainWindow(config=cfg, load_demo=True)
            win.mpv_bridge.stop()
            self.addCleanup(win.close)

            # 1) 驗證主視窗與覆蓋層極簡減重（已移除 OCR、重複按鈕、預設隱藏雜訊列）
            self.assertFalse(hasattr(win, "ocr_btn"))
            self.assertFalse(hasattr(win, "blueprint_btn"))
            self.assertFalse(hasattr(win, "theme_btn"))
            self.assertFalse(hasattr(win.overlay, "ocr_btn"))
            self.assertTrue(win.thread_combo.isHidden())
            self.assertTrue(win.manual_input.isHidden())

            # 僅在 Hook 出現 ≥2 條線程時才自動顯示線程切換器
            t1 = HookThreadInfo(thread_key="a", handle="1", hook_name="TextOut", hook_code="H1", last_text="あ")
            t2 = HookThreadInfo(thread_key="b", handle="2", hook_name="GetGlyph", hook_code="H2", last_text="い")
            win._on_hook_threads_updated([t1], "auto")
            self.assertTrue(win.thread_combo.isHidden())
            win._on_hook_threads_updated([t1, t2], "auto")
            self.assertFalse(win.thread_combo.isHidden())
            win._on_hook_state_changed(False, "已中斷")
            self.assertTrue(win.thread_combo.isHidden())

            # 2) 一鍵直寫：不開 CardForgeDialog，直接寫入並彈出右下角「↩ 撤銷」浮動通知條
            rec = win._current_record
            assert rec is not None
            tok = next((t for t in rec.tokenized.tokens if t.lemma == "募る"), rec.tokenized.tokens[0])
            deleted_ids: list[int] = []

            def _fake_delete(ids: list[int]) -> None:
                deleted_ids.extend(ids)

            with (
                patch.object(win.anki, "ensure_deck", return_value=None),
                patch.object(win.anki, "ensure_note_type", return_value=None),
                patch.object(win.anki, "add_card", return_value=778899),
                patch.object(win.anki, "delete_notes", side_effect=_fake_delete),
                patch("galgame_anki.ui.main_window.CardForgeDialog") as mock_forge_cls,
            ):
                win._mine_record_token(rec, tok)
                mock_forge_cls.assert_not_called()

                for _ in range(40):
                    self.app.processEvents()
                    if win.store.today_mined_count == 1:
                        break
                    time.sleep(0.02)

                self.assertEqual(win.store.today_mined_count, 1)
                self.assertIn(tok.lemma, win.store.known_words)
                self.assertTrue(win.undo_toast.isVisible())
                self.assertFalse(win.undo_toast.undo_btn.isHidden())

                # 3) 點擊右下角「↩ 撤銷」：呼叫 delete_notes 並還原今日計數與熟詞表
                win.undo_toast.undo_btn.click()
                for _ in range(50):
                    self.app.processEvents()
                    if deleted_ids and win.store.today_mined_count == 0:
                        break
                    time.sleep(0.02)

                self.assertEqual(deleted_ids, [778899])
                self.assertEqual(win.store.today_mined_count, 0)
                self.assertNotIn(tok.lemma, win.store.known_words)

                # 4) 按住 Shift（force_forge=True）才開啟精煉編輯窗
                mock_forge_cls.return_value.exec.return_value = 0
                win._mine_record_token(rec, tok, force_forge=True)
                mock_forge_cls.assert_called_once()

            # 5) SettingsDialog 讀寫單螢幕新設定
            dlg = SettingsDialog(cfg, win.thread_pool, win)
            self.assertTrue(dlg.tray_mode_chk.isChecked())
            self.assertFalse(dlg.auto_launch_anki_chk.isChecked())
            self.assertFalse(dlg.forge_chk.isChecked())
            dlg.auto_launch_anki_chk.setChecked(True)
            dlg._save_and_accept()
            self.assertTrue(cfg.auto_launch_anki)
            dlg.close()


if __name__ == "__main__":
    unittest.main()
