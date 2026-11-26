import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from galgame_anki.core.grammar_engine import OfflineGrammarEngine
from galgame_anki.core.japanese_reference import ReferenceIndex, reference_topics
from galgame_anki.core.learning_progress import LearningProgressStore
from galgame_anki.ui.blueprint_dialog import LearningBlueprintDialog


class ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.grammar = OfflineGrammarEngine()
        cls.index = ReferenceIndex(cls.grammar)

    def test_all_topics_reachable_and_existing_grammar_indexed(self):
        topics = self.index.topics
        self.assertEqual(len(topics), len(reference_topics(self.grammar)))
        for t in topics.values():
            seen = {t.key}
            self.assertTrue(t.rows and t.rule)
            while t.before:
                self.assertNotIn(t.before, seen)
                seen.add(t.before)
                t = topics[t.before]
        self.assertEqual({key[8:] for key in topics if key.startswith("pattern-")},
                         {p["id"] for p in self.grammar.patterns})
        self.assertEqual({key[9:] for key in topics if key.startswith("particle-")},
                         set(self.grammar.particles))

    def test_boundaries_readings_and_search_by_meaning(self):
        for query, key, reading in (
            ("四月", "months", "しがつ"), ("４月", "months", "しがつ"),
            ("七月", "months", "しちがつ"), ("くがつ", "months", "くがつ"),
            ("十二月", "months", "じゅうにがつ"), ("四個月", "monthspan", "よんかげつ"),
            ("两个月", "monthspan", "にかげつ"), ("４ヶ月", "monthspan", "よんかげつ"),
            ("十四號", "dates", "じゅうよっか"), ("ハツカ", "dates", "はつか"),
            ("24日", "dates", "にじゅうよっか"), ("31日", "dates", "さんじゅういちにち"),
            ("三個人", "counter-人", "さんにん"), ("二十歲", "counter-歳", "はたち"),
            ("8分鐘", "minutes", "はっぷん"), ("51分", "minutes", "ごじゅういっぷん"),
            ("60分", "minutes", "ろくじゅっぷん"),
        ):
            with self.subTest(query=query):
                result, row = self.index.find(query)[0]
                self.assertEqual(result, key)
                self.assertEqual(self.index.topics[key].rows[row][1], reading)
        self.assertEqual({self.index.topics[k].rows[i][1] for k,i in self.index.find("一日")
                          if k in ("dates", "durations")}, {"ついたち","いちにち"})
        for key, total in (("months",12), ("dates",31), ("minutes",60)):
            self.assertEqual(len({r[0] for r in self.index.topics[key].rows[:total]}), total)
        self.assertEqual(self.index.find("７月二十四號"), [("dates", 23), ("months", 6)])
        self.assertEqual(self.index.date_parts("二月二十九日"), (2, 29))
        self.assertIsNone(self.index.date_parts("二月三十日"))
        # 已移除 294 列生詞表雜訊，改由精準索引涵蓋繁簡、羅馬音、24 小時制與精確助詞
        self.assertNotIn("core_spoken_dict", self.index.topics)
        self.assertNotIn("starter_dict", self.index.topics)
        self.assertIn("galanki-quickstart", self.index.topics)
        self.assertEqual(self.index.find("五十音")[0][0], "kana")
        self.assertEqual(self.index.find("shi")[0][0], "kana")
        self.assertIn(self.index.find("kya")[0][0], ("voicing", "kana-combinations"))
        self.assertEqual(self.index.find("rya")[0][0], "kana-combinations")
        self.assertEqual(self.index.find("4ヵ月")[0][0], "monthspan")
        self.assertEqual(self.index.find("13時")[0][0], "hours")
        self.assertEqual(self.index.find("に")[0][0], "particle-に")
        self.assertEqual(self.index.find("は")[0][0], "particle-は")
        self.assertTrue(self.index.find("被动"))
        self.assertTrue(self.index.find("动词"))

    def test_search_from_dialog_and_navigate_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LearningProgressStore(Path(tmp) / "test.db")
            self.addCleanup(store.close)
            dlg = LearningBlueprintDialog(self.grammar, store, 0, "lv2")
            self.addCleanup(dlg.close)
            ref = dlg.reference
            dlg.find_reference("二十四號")
            self.assertIs(dlg.tabs.currentWidget(), ref)
            self.assertIn("命中條目 二十四日 にじゅうよっか 24號", " ".join(ref.reader.toPlainText().split()))
            self.assertIn("にじゅうよっか", ref.reader.toPlainText())
            ref.search.setText("四月二十四日")
            self.assertIn("四月二十四日 しがつ にじゅうよっか", " ".join(ref.reader.toPlainText().split()))
            ref.follow(QUrl("topic:months"))
            self.assertEqual(ref.results.currentItem().text(), "月份：一年十二個月")
            ref.open_topic("pattern-n4_tara")
            ref.follow(QUrl("lesson:current"))
            self.assertIs(dlg.tabs.currentWidget(), dlg.grammar_tab)
            self.assertEqual(dlg._selected_pattern["id"], "n4_tara")
            dlg.find_reference("xyzzzz")
            self.assertEqual(ref.results.count(), 0)
            self.assertIn("沒有找到", ref.reader.toPlainText())
            ref.browse.click()
            self.assertGreater(ref.results.count(), 0)
