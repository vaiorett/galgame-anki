import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl
from unittest.mock import patch
from galgame_anki.ui.main_window import MainWindow
from galgame_anki.config import AppConfig

from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.grammar_engine import OfflineGrammarEngine
from galgame_anki.core.learning_progress import LearningProgressStore
from galgame_anki.core.study_steps import EXAMPLES, REQUIRES
from galgame_anki.core.study_map import CONCEPTS, focus_for, lineage
from galgame_anki.core.tokenizer import JapaneseTokenizer, Token
from galgame_anki.ui.blueprint_dialog import LearningBlueprintDialog
from galgame_anki.ui.lookup_popup import LookupDetailPanel, OverlayLookupPopup


class StudyMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_current_sentence_button_and_overlay_keep_selected_context(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp):
            cfg = AppConfig(ws_enabled=False, clipboard_watch=False, auto_sync_known_words=False,
                            capture_audio_on_mine=False, start_in_tray_overlay=False)
            win = MainWindow(config=cfg)
            try:
                win._ingest_sentence("地球が壊れたら…", "manual")
                rec = win._current_record
                token = next(t for t in rec.tokenized.tokens if t.surface == "壊れたら")
                win._show_token_in_panel(token)
                with patch.object(win, "open_learning_blueprint") as open_map:
                    win.map_btn.click()
                    study = open_map.call_args.kwargs["study"]
                    self.assertEqual((study[0].key, study[1], study[2]), ("condition", rec.raw_text, "壊れる"))
                    win.overlay.popup.popup_for_token(token, "bilingual", False, sentence=rec.raw_text,
                                                      tokens=rec.tokenized.tokens, show=False)
                    win._ingest_sentence("もう帰ります。", "manual")
                    win.overlay.popup.study_link.linkActivated.emit("map")
                    self.assertEqual(open_map.call_args.kwargs["study"], study)
            finally:
                win._force_quit = True
                win.close()

    def test_each_step_supplies_the_next_step(self):
        self.assertEqual(set(EXAMPLES), set(CONCEPTS))
        def check_prerequisites(topic, visited):
            self.assertNotIn(topic, visited)
            for parent, reason in REQUIRES.get(topic, ()):
                self.assertIn(parent, CONCEPTS)
                self.assertTrue(reason)
                check_prerequisites(parent, visited | {topic})
        for key in CONCEPTS:
            check_prerequisites(key, set())
        token = Token("壊れたら", "壊れる", "", 3, 7, verb_type="一段")
        for sentence, end in (("地球が壊れたら…", "後項尚未說出"), ("機械が壊れたら、呼んで。", "呼んで")):
            walk = focus_for(token, sentence=sentence).walk
            self.assertEqual([step[2] for step in walk.steps], ["壊れた", "壊れたら", end])
            self.assertEqual([walk.before(i) for i in range(3)], ["壊れる", "壊れた", "壊れたら"])
        ability = focus_for(Token("言えなかった", "言う", "", 0, 6, verb_type="五段")).walk
        self.assertEqual([step[2] for step in ability.steps], ["言える", "言えない", "言えなかった"])

    def test_related_concepts_and_contrast(self):
        for key in CONCEPTS:
            path = lineage(key)
            self.assertEqual(len(path), len(set(path)))
            self.assertTrue(all(CONCEPTS[k].branch == CONCEPTS[key].branch for k in path))
        for surface, lemma, group, expected in (
            ("言わなかった", "言う", "五段", "negative"), ("言えなかった", "言う", "五段", "ability"),
            ("見えなかった", "見える", "一段", "negative"), ("遊んじゃった", "遊ぶ", "五段", "completion"),
            ("壊れたら", "壊れる", "一段", "condition"), ("開ける", "開ける", "一段", "pair"),
        ):
            token = Token(surface, lemma, "", 0, len(surface), verb_type=group)
            self.assertEqual(focus_for(token).key, expected)

    def test_focus_from_both_readers_then_review_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            dictionary = DictionaryEngine(Path(tmp) / "dict.db")
            self.addCleanup(dictionary.close)
            sentence = "地球が壊れたら…"
            tokens = JapaneseTokenizer(dictionary).tokenize(sentence).tokens
            token = next(t for t in tokens if t.surface == "壊れたら")
            panel = LookupDetailPanel()
            self.addCleanup(panel.close)
            popup = OverlayLookupPopup()
            self.addCleanup(popup.close)
            panel.show_token(token, sentence=sentence, tokens=tokens)
            popup.popup_for_token(token, "bilingual", False, sentence=sentence, tokens=tokens, show=False)
            focus = panel.study_link.focus
            self.assertEqual(focus, popup.study_link.focus)
            self.assertEqual(focus.vocabulary, "地球が壊れる　↔　地球を壊す")
            self.assertIn("壊れる → 壊れた → 壊れたら", focus.evidence)
            self.assertEqual((focus.key, focus.lexical_key), ("condition", "pair"))
            db_path = Path(tmp) / "progress.db"
            store = LearningProgressStore(db_path)
            dialog = LearningBlueprintDialog(OfflineGrammarEngine(), store, 0, "lv2")
            study = dialog.study_map
            panel.study_link.requested.connect(study.show_focus)
            panel.study_link.linkActivated.emit("map")
            self.assertIs(dialog.tabs.currentWidget(), study)
            self.assertIn(sentence, study.reader.toPlainText())
            self.assertEqual(study.step, 0)
            study.next_step.click()
            self.assertIn("沿用上一步的結果", study.reader.toPlainText())
            self.assertIn("壊れた", study.reader.toPlainText())
            study.next_step.click()
            self.assertIn("後項尚未說出", study.reader.toPlainText())
            study.next_step.click()
            self.assertIn("先要理解", study.reader.toPlainText())
            study.previous_step.click()
            study.save.click()
            study.keep()
            self.assertEqual(len(store.study_examples("condition")), 1)
            study.select("roles")
            self.assertNotIn(sentence, study.reader.toPlainText())
            study.select("condition")
            study.follow(QUrl("pattern:index"))
            self.assertIs(dialog.tabs.currentWidget(), dialog.grammar_tab)
            dialog.close()
            store.close()
            restored = LearningProgressStore(db_path)
            self.addCleanup(restored.close)
            dialog = LearningBlueprintDialog(OfflineGrammarEngine(), restored, 0, "lv2")
            self.addCleanup(dialog.close)
            study = dialog.study_map
            study.select("condition")
            study.review_btn.click()
            self.assertIn(sentence, study.reader.toPlainText())
            self.assertNotIn("壊す", study.reader.toPlainText())
            study.recall.click()
            self.assertIn("壊す", study.reader.toPlainText())
