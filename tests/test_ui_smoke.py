"""PySide6 Offscreen GUI 煙霧測試：確保主視窗、覆蓋層、學習藍圖、媒體字幕連線、精靈與設定視窗運作無誤。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_anki.adapters.mpv_bridge import MpvSubtitleEvent
from galgame_anki.config import AppConfig
from galgame_anki.ui.blueprint_dialog import LearningBlueprintDialog
from galgame_anki.ui.card_forge_dialog import CardForgeDialog
from galgame_anki.ui.extra_dialogs import DifficultyGuideDialog, DictionaryManagerDialog
from galgame_anki.ui.main_window import MainWindow
from galgame_anki.ui.media_bridge_dialog import MediaBridgeDialog
from galgame_anki.ui.settings_dialog import SettingsDialog
from galgame_anki.ui.setup_wizard import SetupWizardDialog


class TestUISmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_overlay_blueprint_media_and_dialogs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                ws_enabled=False,
                clipboard_watch=False,
                auto_sync_known_words=False,
                capture_audio_on_mine=False,
                ui_theme="sakura",
            )
            cfg.save(Path(tmp) / "config.json")

            win = MainWindow(config=cfg, load_demo=True)
            self.assertIsNotNone(win._current_record)
            self.assertIsNotNone(win._current_record.grammar_analysis)

            # 1) 模擬輸入攻略 Lv.3 例句與原地延長不漏字
            win._ingest_sentence("『誠、あなたは少しこちらの世界に", "hook")
            orig_ts = win._current_record.timestamp
            win._on_hook_dialogue_extended("『誠、あなたは少しこちらの世界にかぶれすぎた。私自身もね』", "k")
            self.assertEqual(
                win._current_record.raw_text,
                "『誠、あなたは少しこちらの世界にかぶれすぎた。私自身もね』",
            )
            self.assertEqual(win._current_record.timestamp, orig_ts)

            # 2) 模擬 mpv 影片字幕進流
            win._on_mpv_subtitle(
                MpvSubtitleEvent(
                    text="外の世界を知りたいという想いは、募る一方だ",
                    media_title="TestAnime_01.mkv",
                    video_path="/tmp/fake.mkv",
                    sub_start=10.0,
                    sub_end=13.5,
                )
            )
            self.assertEqual(win._current_record.source, "mpv")
            self.assertEqual(win._current_record.media_title, "TestAnime_01.mkv")
            self.assertGreaterEqual(win.progress_store.get_sentences_read(), 2)

            # 3) 點擊助詞「は」確認顯示白話助詞角色 + 複製 Web AI 提問詞
            ha_tok = next((t for t in win._current_record.tokenized.tokens if t.surface == "は"), None)
            self.assertIsNotNone(ha_tok)
            assert ha_tok is not None
            win._show_token_in_panel(ha_tok)
            self.assertIn("助詞", win.lookup_panel.trace_label.text())
            win._on_panel_copy_prompt(ha_tok)

            # 4) 階段與二次元雙主題切換
            win._set_stage_mode("lv3")
            self.assertEqual(win.config.stage_mode, "lv3")
            win._toggle_theme()
            self.assertEqual(win.config.ui_theme, "starlight")
            win._toggle_theme()
            self.assertEqual(win.config.ui_theme, "sakura")

            # 5) 測試原生級「遮蓋替換模式」覆蓋層：固定尺寸絕不跳動、底色與字體切換、閒置隱藏浮動微工具列、無阻膠囊恢復原尺寸
            win._toggle_overlay(True)
            self.assertTrue(win.overlay.isVisible())
            self.assertTrue(win.overlay._replace_mode)
            self.assertTrue(win.overlay._always_on_top)
            self.assertEqual(win.overlay.pin_btn.text(), "📌 置頂")
            self.assertAlmostEqual(win.overlay.windowOpacity(), 1.0, places=2)
            self.assertFalse(win.overlay.toolbar_widget.isVisible())

            win.overlay._toggle_always_on_top(False)
            self.assertFalse(win.overlay._always_on_top)
            self.assertFalse(win.config.overlay_always_on_top)
            win.overlay._toggle_always_on_top(True)
            self.assertTrue(win.overlay._always_on_top)
            self.assertTrue(win.config.overlay_always_on_top)

            win.overlay._set_chrome_active(True)
            self.assertTrue(win.overlay.toolbar_widget.isVisible())
            win.overlay._set_chrome_active(False)
            self.assertFalse(win.overlay.toolbar_widget.isVisible())

            # 底色預設切換（深黑 -> 暖白 -> 純黑 -> 深黑）與 A+/A- 字體縮放
            orig_backdrop = win.overlay._backdrop
            win.overlay._cycle_backdrop()
            self.assertNotEqual(win.overlay._backdrop, orig_backdrop)
            orig_fs = win.overlay._font_size
            win.overlay._adjust_font_size(+2)
            self.assertEqual(win.overlay._font_size, orig_fs + 2)
            self.assertEqual(win.config.overlay_font_size, orig_fs + 2)

            win.overlay.resize(820, 128)
            win._ingest_sentence("『わ、わかった！　えいっ』", "hook")
            self.assertEqual((win.overlay.width(), win.overlay.height()), (820, 128))
            win._ingest_sentence(
                "明日以降、学園で顔を合わせても、まるで今日のことがなかったかのように振舞います。そしてまた新しい朝が来るのだ。",
                "hook",
            )
            self.assertEqual((win.overlay.width(), win.overlay.height()), (820, 128))
            win.overlay._toggle_lock(True)
            self.assertTrue(win.overlay._locked)
            self.assertTrue(win.overlay._guide_dismissed)
            win.overlay._toggle_lock(False)
            win.overlay.toggle_freeflow_mode()
            self.assertTrue(win.overlay._freeflow_mode)
            win.overlay.toggle_freeflow_mode()
            self.assertFalse(win.overlay._freeflow_mode)
            self.assertEqual((win.overlay.width(), win.overlay.height()), (820, 128))

            # 6) 測試系統學習藍圖與 mpv 字幕對話框（確認偵測到本機 Flatpak mpv）
            bp_dlg = LearningBlueprintDialog(
                grammar=win.grammar_engine,
                progress_store=win.progress_store,
                known_words_count=len(win.store.known_words),
                stage_mode=cfg.stage_mode,
                theme_name=cfg.ui_theme,
                parent=win,
            )
            self.assertGreater(bp_dlg.grammar_list.count(), 30)
            bp_dlg.close()

            media_dlg = MediaBridgeDialog(cfg, win.mpv_bridge, win)
            self.assertIn("已偵測到", media_dlg.mpv_status.text())
            self.assertTrue(media_dlg.hide_mpv_sub_chk.isChecked())
            media_dlg.close()

            # 7) 測試精靈、詞典管理、難度指南與設定視窗（已移除付費 AI 分頁）
            wizard = SetupWizardDialog(
                config=cfg,
                adapter=win.adapter,
                dictionary=win.dictionary,
                anki=win.anki,
                thread_pool=win.thread_pool,
                parent=win,
            )
            self.assertIn("Linux", wizard.diag_label.text())
            wizard.close()

            dict_dlg = DictionaryManagerDialog(win.dictionary, win.thread_pool, win)
            self.assertGreater(dict_dlg.dict_list.count(), 0)
            dict_dlg.close()

            # 測試 Refold 精煉製卡確認視窗
            forge = CardForgeDialog(
                stage_mode="lv2",
                sentence_highlighted="外の世界を知りたいという想いは、【募る】一方だ",
                raw_sentence="外の世界を知りたいという想いは、募る一方だ",
                target_word="募る",
                target_reading="つのる",
                pitch_accent="2",
                bi_senses=["越來越強烈", "募集"],
                mono_senses=["しだいに激しくなる。"],
                default_bi="越來越強烈",
                default_mono="しだいに激しくなる。",
                sentence_translation="想要了解外面世界的心意越來越強烈。",
                audio_clip=(b"ID3FAKE", "v.mp3"),
                screenshot_clip=None,
                include_audio_default=True,
                include_screenshot_default=False,
                thread_pool=win.thread_pool,
                parent=win,
            )
            forge._confirm()
            self.assertIsNotNone(forge.result_data)
            assert forge.result_data is not None
            self.assertEqual(forge.result_data.definition_bilingual, "越來越強烈")
            self.assertEqual(forge.result_data.sentence_translation, "想要了解外面世界的心意越來越強烈。")
            forge.close()

            guide_dlg = DifficultyGuideDialog("sakura", win)
            guide_dlg.close()

            settings = SettingsDialog(cfg, win.thread_pool, win)
            self.assertEqual(settings.deck_edit.text(), "Galgame日語")
            settings.close()

            win.close()


if __name__ == "__main__":
    unittest.main()
