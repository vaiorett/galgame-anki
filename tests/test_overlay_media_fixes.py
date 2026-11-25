import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton
from galgame_anki.adapters.mpv_bridge import _subtitle_snapshot
from galgame_anki.core.dictionary import LookupMatch
from galgame_anki.core.grammar_engine import translate_sentence_free
from galgame_anki.core.tokenizer import Token
from galgame_anki.ui.lookup_popup import OverlayLookupPopup


class TestOverlayMedia(unittest.TestCase):
    def test_mpv_sentence_snapshot(self):
        fields = ["1.5", "2.8", "video.mkv", "/media", "標題", "2", "", "zh-TW",
                  "1.6", "2.8", "快點啦！", "早くしてよね！"]
        event = _subtitle_snapshot("\x1f".join(fields))
        self.assertEqual((event.video_path, event.audio_stream_index), ("/media/video.mkv", 2))
        self.assertEqual((event.sub_start, event.sub_end), (1.5, 2.8))
        self.assertEqual(event.chinese_subtitle, "快點啦！")
        fields[8] = "0"
        self.assertFalse(_subtitle_snapshot("\x1f".join(fields)).chinese_subtitle)

    @patch("galgame_anki.core.grammar_engine.httpx.get")
    def test_speaker_kept_separate(self, get):
        get.return_value.json.return_value = [[["別睡過頭喔！"]]]
        self.assertEqual(translate_sentence_free("（萌美）寝坊しないでよ！"), "（萌美）別睡過頭喔！")
        self.assertEqual(get.call_args.kwargs["params"]["q"], "寝坊しないでよ！")

    def test_popup_surface_and_candidates(self):
        app = QApplication.instance() or QApplication([])
        popup = OverlayLookupPopup("starlight")
        self.addCleanup(popup.close)
        match = LookupMatch("しない", "する", "する", ())
        match.alt_matches = [LookupMatch(word, word, "", ()) for word in ["しなう", "撓う", "市内"]]
        popup.popup_for_token(Token("しない", "する", "する", 0, 3, lookup=match), "bilingual", False)
        app.processEvents()
        self.assertEqual(popup.grab().toImage().pixelColor(7, 60).alpha(), 222)
        for button in popup.alt_bar.findChildren(QPushButton):
            self.assertGreaterEqual(button.height(), button.fontMetrics().height() + 4)
            self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()))
