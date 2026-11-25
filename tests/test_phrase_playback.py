import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication, QSlider
from galgame_anki.adapters.mpv_bridge import MpvSubtitleBridge
from galgame_anki.core.context_meaning import explain_lookup_in_sentence
from galgame_anki.core.dictionary import DictionaryEngine, DictEntry
from galgame_anki.core.tokenizer import JapaneseTokenizer
from galgame_anki.ui.lookup_popup import LookupDetailPanel, OverlayLookupPopup
from galgame_anki.ui.overlay_window import OverlayWindow


class TestPhrasePlayback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        engine = DictionaryEngine(Path(tmp.name) / "dict.db")
        cls.addClassCleanup(engine.close)
        cls.tokenizer = JapaneseTokenizer(engine)

    def test_te_form_boundaries(self):
        for text, expected in (("息を殺して待つ", "殺して"), ("本を読んで、寝た。", "読んで"),
                               ("殺している", "殺している"), ("殺していた", "殺していた")):
            with self.subTest(text=text):
                token = next(t for t in self.tokenizer.tokenize(text).tokens if t.surface == expected)
                self.assertEqual(text[token.start:token.end], expected)
        self.assertIn("で", [t.surface for t in self.tokenizer.tokenize("学校で話して").tokens])
        for text, role in (("殺して。", "可能是在請求"), ("殺してください", "請對方"), ("殺してから", "先完成")):
            tok = self.tokenizer.tokenize(text).tokens[0]
            self.assertIn(role, explain_lookup_in_sentence(tok, text, "")[1])

    def test_lookup_uses_sentence_and_both_languages(self):
        text = "息を殺して待つ"
        token = self.tokenizer.tokenize(text).tokens[2]
        token.lookup.bilingual_entries.append(DictEntry("殺す", "ころす", "v5", ["to kill"], "EN", "bilingual"))
        panel = LookupDetailPanel()
        panel.show_token(token, sentence=text)
        visible = panel.bi_view.toPlainText()
        for expected in ("屏住呼吸", "殺す → 殺して"):
            self.assertIn(expected, visible)
        for repeated in (text, "命を奪う", "to kill"):
            self.assertNotIn(repeated, visible)
        self.assertIn("命を奪う", panel.mono_view.toPlainText())
        self.assertIn("to kill", panel.en_view.toPlainText())
        popup = OverlayLookupPopup()
        popup.popup_for_token(token, "bilingual", False, sentence=text)
        self.assertIn("屏住呼吸", popup.def_label.text())
        self.assertNotIn("其他：", popup.def_label.text())
        self.addCleanup(popup.close)
        for width in (385, 420):
            popup.setFixedWidth(width)
            self.app.processEvents()
            region = QRegion(popup._panel_path().toFillPolygon().toPolygon())
            image = popup.grab().toImage()
            for point in (QPoint(1, 1), QPoint(width-2, popup.height()-2)):
                self.assertFalse(region.contains(point))
                self.assertEqual(image.pixelColor(point * image.devicePixelRatio()).alpha(), 0)

    def test_overlay_subtitle_controls_without_timeline(self):
        window = OverlayWindow()
        self.addCleanup(window.close)
        window.set_mpv_connected(True)
        self.assertEqual(window.findChildren(QSlider), [])
        self.assertTrue(window.replay_action.isVisible())
        bridge = MpvSubtitleBridge()
        bridge._connected = True
        with patch.object(bridge, "send_ipc_command", return_value=True) as send:
            self.assertTrue(bridge.loop_subtitle(4, 6))
            send.assert_any_call(["set_property", "ab-loop-b", 6])
            bridge.step_subtitle(-1)
            send.assert_any_call(["set_property", "ab-loop-b", "no"])
            send.assert_called_with(["sub-seek", -1], request_id=91)
        window.set_mpv_connected(False)
        self.assertFalse(window.replay_action.isVisible())
