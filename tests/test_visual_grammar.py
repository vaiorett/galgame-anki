import configparser
import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from galgame_anki.adapters.window_pin import (
    KWIN_RULE_SECTION, OVERLAY_WINDOW_TITLE, POPUP_WINDOW_TITLE, ensure_kwin_overlay_layer_rule,
)
from galgame_anki.core.context_meaning import explain_lookup_in_sentence
from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.grammar_engine import OfflineGrammarEngine, SentenceGrammarAnalysis
from galgame_anki.core.tokenizer import JapaneseTokenizer, Token
from galgame_anki.core.verb_lesson import build_lesson, lesson_for_token
from galgame_anki.ui.lookup_popup import LookupDetailPanel
from galgame_anki.ui.overlay_window import OverlayWindow
from galgame_anki.ui.verb_explorer import VerbExplorer


class TestVisualLearning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        dictionary = DictionaryEngine(Path(directory.name) / "dictionary.sqlite")
        cls.addClassCleanup(dictionary.close)
        cls.tokenizer = JapaneseTokenizer(dictionary)
        cls.grammar = OfflineGrammarEngine()

    def test_same_intent_different_endings(self):
        for lemma, group, form, expected in (
            ("食べる", "v1", "te", "食べて"), ("帰る", "v5", "te", "帰って"),
            ("泳ぐ", "v5", "ta", "泳いだ"), ("買う", "v5", "nai", "買わない"),
            ("行く", "v5", "te", "行って"), ("問う", "v5", "ta", "問うた"),
            ("ある", "v5", "nai", "ない"), ("くださる", "v5", "masu", "くださいます"),
            ("勉強する", "vs", "masu", "勉強します"), ("くる", "vk", "nai", "こない"),
            ("座る", "v5", "tero", "座ってろ"), ("読む", "v5", "teiru", "読んでいる"),
        ):
            with self.subTest(lemma=lemma, form=form):
                lesson = build_lesson(lemma, group, form)
                self.assertEqual(lesson.surface, expected)
                self.assertEqual(lesson.fixed + lesson.old, lemma)
        invalid = Token("帰て", "帰る", "", 0, 2, verb_type="五段")
        self.assertIsNone(lesson_for_token(invalid))
        self.assertIsNone(lesson_for_token(Token("ホテル", "ホテル", "", 0, 3), preview=True))

    def test_hover_fullscreen_keeps_input_for_player_and_mouse_for_popup(self):
        window = OverlayWindow("starlight", self.grammar)
        self.addCleanup(window.close)
        popup = window.popup
        self.addCleanup(popup.close)
        for widget in (window, popup):
            self.assertTrue(widget.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
            self.assertTrue(widget.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
            self.assertFalse(widget.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        player = QWidget()
        self.addCleanup(player.close)
        window.show()
        player.showFullScreen()
        player.activateWindow()
        self.app.processEvents()
        self.assertIs(self.app.focusWindow(), player.windowHandle())
        token = self.tokenizer.tokenize("パンを食べてください").tokens[2]
        window._on_token_hovered(token, QPoint(300, 350))
        self.app.processEvents()
        self.assertTrue(popup.isVisible())
        self.assertTrue(player.isFullScreen())
        self.assertIs(self.app.focusWindow(), player.windowHandle())
        self.assertIs(popup.parentWidget(), window)
        self.assertIn("台詞裡", popup.form_label.text())
        self.assertEqual(popup.findChildren(QPushButton), [popup.mine_btn, popup.audio_btn, popup.prompt_btn])
        received = []
        popup.audio_preview_requested.connect(lambda: received.append(True))
        QTest.mouseClick(popup.audio_btn, Qt.MouseButton.LeftButton)
        self.assertEqual(received, [True])
        self.assertIs(self.app.focusWindow(), player.windowHandle())
        window._on_token_hover_left()
        window._on_popup_hover_enter()
        self.assertFalse(window._popup_hide_timer.isActive())
        window._on_popup_hover_leave()
        self.assertTrue(window._popup_hide_timer.isActive())
        window._maybe_hide_popup()
        self.assertFalse(popup.isVisible())

    def test_kwin_rule_targets_only_galanki_windows(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, XDG_CONFIG_HOME=directory):
            rules = Path(directory) / "kwinrulesrc"
            rules.write_text("[General]\nOrder=other\n\n[other]\nDescription=另一條規則\n", encoding="utf-8")
            with patch("galgame_anki.adapters.window_pin.is_kde_wayland_kwin_available", return_value=True):
                ensure_kwin_overlay_layer_rule(reconfigure=False)
            saved = rules.read_text(encoding="utf-8")
            parser = configparser.ConfigParser()
            parser.read_string(saved)
            rule = parser[KWIN_RULE_SECTION]
            self.assertFalse(rule.getboolean("acceptfocus"))
            self.assertEqual(rule.getint("acceptfocusrule"), 2)
            for title in (OVERLAY_WINDOW_TITLE, POPUP_WINDOW_TITLE):
                self.assertRegex(title, re.compile(rule["title"]))
            for title in ("mpv", "Plasma", "GalAnki 主控台"):
                self.assertNotRegex(title, re.compile(rule["title"]))
            self.assertEqual(parser["other"]["Description"], "另一條規則")
            ensure_kwin_overlay_layer_rule(kwinrules_path=rules, reconfigure=False)
            self.assertEqual(rules.read_text(encoding="utf-8"), saved)

    def test_entire_subtitle_with_otherwise(self):
        line = "黙って座ってろ じゃないと撃つぞ！"
        parsed = self.tokenizer.tokenize(line)
        self.assertEqual("".join(t.surface for t in parsed.tokens), line)
        command = next(t for t in parsed.tokens if t.surface == "座ってろ")
        self.assertEqual(lesson_for_token(command).form, "tero")
        token = next(t for t in parsed.tokens if t.surface == "じゃないと")
        self.assertEqual(line[token.start:token.end], token.surface)
        self.assertEqual(explain_lookup_in_sentence(token, line, "")[0], "要不然……")
        panel = LookupDetailPanel()
        self.addCleanup(panel.close)
        token.lookup = None
        panel.show_token(token, sentence=line)
        self.assertIn("要不然……", panel.bi_view.toPlainText())
        window = OverlayWindow()
        self.addCleanup(window.close)
        self.addCleanup(window.popup.close)
        window.popup.popup_for_token(token, "bilingual", False, sentence=line)
        self.assertIn("要不然……", window.popup.def_label.text())
        for other in ("彼じゃないと思う。", "学生じゃないと入れない。", "じゃない。"):
            self.assertNotIn("じゃないと", [t.surface for t in self.tokenizer.tokenize(other).tokens])

    def test_sudachi_morphemes_use_the_same_lesson_when_available(self):
        def morpheme(surface, start, base, reading, pos, group="*"):
            node = Mock()
            node.surface.return_value = surface
            node.begin.return_value = start
            node.end.return_value = start + len(surface)
            node.dictionary_form.return_value = base
            node.reading_form.return_value = reading
            node.part_of_speech.return_value = (pos, "*", "*", "*", group, "*")
            return node

        nodes = [
            morpheme("食べ", 0, "食べる", "タベ", "動詞", "下一段-バ行"),
            morpheme("て", 2, "て", "テ", "助詞"),
            morpheme("いる", 3, "いる", "イル", "動詞", "上一段-ア行"),
        ]
        backend = Mock()
        backend.tokenize.return_value = nodes
        with patch.object(self.tokenizer, "_sudachi", backend):
            parsed = self.tokenizer.tokenize("食べている")
        self.assertEqual([t.surface for t in parsed.tokens], ["食べている"])
        self.assertEqual(lesson_for_token(parsed.tokens[0]).form, "teiru")

    def test_explorer_and_card_use_selected_word(self):
        explorer = VerbExplorer("starlight")
        self.addCleanup(explorer.close)
        eat = build_lesson("食べる", "v1", "teiru")
        home = build_lesson("帰る", "v5", "te")
        explorer.select_lesson(eat)
        self.assertIn("食べている", explorer.page.toPlainText())
        self.assertIn("食べて", explorer.page.toPlainText().split("食べている")[0])
        explorer.forms.setCurrentIndex(explorer.forms.findData("nai"))
        self.assertIn("食べない", explorer.page.toPlainText())
        self.assertIn("帰らない", explorer.page.toPlainText())
        analysis = SentenceGrammarAnalysis("食べている。帰って。", verb_lessons=[eat, home])
        self.assertIn(eat.html(), analysis.to_compact_html(focus_word="食べる"))
        self.assertNotIn(home.html(), analysis.to_compact_html(focus_word="食べる"))
        self.assertIn(home.html(), analysis.to_compact_html())

    def test_noun_example_is_anchored_to_its_sentence(self):
        line = "俺はテロとかは違う。"
        token = next(t for t in self.tokenizer.tokenize(line).tokens if t.surface == "とか")
        _, role = explain_lookup_in_sentence(token, line, "")
        self.assertIn("テロ ＋ とか", role)
        self.assertIn("例子", role)
        self.assertIsNotNone(self.grammar.get_particle_info("とか"))
