"""Phase 1–3 全量修復與邏輯拆解工作台自動化回歸測試。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QJSEngine
from PySide6.QtWidgets import QApplication

from galgame_anki.adapters.text_sources import TextIngestionManager, is_allowed_ws_origin
from galgame_anki.adapters.window_pin import build_kwin_minimize_anki_script
from galgame_anki.config import AppConfig
from galgame_anki.core.context_meaning import explain_lookup_in_sentence, format_contextual_meaning
from galgame_anki.core.deinflector import deinflect
from galgame_anki.core.dictionary import (
    DictEntry,
    DictionaryEngine,
    _guess_dict_kind,
    classify_entry_language,
)
from galgame_anki.core.grammar_engine import OfflineGrammarEngine
from galgame_anki.core.learning_progress import LearningProgressStore
from galgame_anki.core.sentence_store import SentenceRecord, SentenceStore
from galgame_anki.core.study_map import focus_for
from galgame_anki.core.study_steps import build_logic_pipeline, build_mutations
from galgame_anki.core.tokenizer import (
    JapaneseTokenizer,
    Token,
    TokenizedSentence,
    build_conjugated_inline_furigana,
)
from galgame_anki.core.verb_lesson import lesson_for_token, verb_group
from galgame_anki.ui.main_window import MainWindow
from galgame_anki.ui.overlay_window import OverlayWindow


class TestPhase123Regressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmpdir.cleanup)
        cls.dictionary = DictionaryEngine(Path(cls._tmpdir.name) / "dict.sqlite")
        cls.addClassCleanup(cls.dictionary.close)
        cls.tokenizer = JapaneseTokenizer(cls.dictionary)
        cls.grammar = OfflineGrammarEngine()

    def test_spoken_polarity_and_deinflection_accuracy(self) -> None:
        # 1) 食べちゃだめ → 禁止；行かなきゃ → 必須；食べてない / 食べてなかった → 否定 / 過去否定
        for surf, lemma, base, reasons, expected_substr in (
            ("食べちゃだめ", "食べる", "吃", ("口語禁止",), "不可"),
            ("行かなきゃ", "行く", "去", ("口語必須",), "必須"),
            ("食べてない", "食べる", "吃", ("進行形", "否定形"), "沒"),
            ("食べてなかった", "食べる", "吃", ("進行形", "過去否定"), "沒"),
        ):
            with self.subTest(surf=surf):
                out = format_contextual_meaning(surf, lemma, base, reasons)
                self.assertIn(expected_substr, out)

        # 2) Tokenizer 與 Janome Pass 2 正確合併「食べてなかった」與「泳げない」
        toks = self.tokenizer.tokenize("昨日は食べてなかった。").tokens
        self.assertIn("食べてなかった", [t.surface for t in toks])
        t_eat = next(t for t in toks if t.surface == "食べてなかった")
        self.assertEqual(t_eat.lemma, "食べる")
        self.assertIsNotNone(lesson_for_token(t_eat))

        toks_swim = self.tokenizer.tokenize("海では泳げない。").tokens
        t_swim = next(t for t in toks_swim if "泳げ" in t.surface)
        self.assertEqual(t_swim.lemma, "泳ぐ")
        self.assertEqual(focus_for(t_swim).key, "ability")

        # 3) 〜やすい / 〜にくい 正常反推原形
        self.assertIn("食べる", [c.term for c in deinflect("食べやすい")])
        self.assertIn("読む", [c.term for c in deinflect("読みにくい")])
        self.assertIn("来る", [c.term for c in deinflect("来られる")])

        # 4) 不規則動詞「来る」正確假名（不可輸出 くるない / くるた）
        for line, expected_inline, expected_rt in (
            ("彼は来ない。", "来(こ)ない", "<ruby>来<rt>こ</rt></ruby>ない"),
            ("友達が来た。", "来(き)た", "<ruby>来<rt>き</rt></ruby>た"),
            ("明日来ます。", "来(き)ます", "<ruby>来<rt>き</rt></ruby>ます"),
            ("来れば分かる。", "来(く)れ", "<ruby>来<rt>く</rt></ruby>れ"),
        ):
            with self.subTest(line=line):
                parsed = self.tokenizer.tokenize(line)
                kuru_tok = next(t for t in parsed.tokens if "来" in t.surface)
                inline = build_conjugated_inline_furigana(kuru_tok.surface, kuru_tok.lemma, kuru_tok.reading)
                self.assertIn(expected_inline, inline)
                self.assertIn(expected_rt, parsed.furigana_html)

        # 5) 五段 -する 動詞不誤判成サ變 vs
        self.assertEqual(verb_group("こする", "五段"), "v5")
        self.assertEqual(verb_group("勉強する", "サ變"), "vs")

    def test_grammar_false_positive_suppression(self) -> None:
        cases = (
            ("パンを食べる。", "n4_potential", False),
            ("写真を見せる。", "n4_potential", False),
            ("少し考える。", "n2_uru_enai", False),
            ("高くて買えない。", "n2_uru_enai", False),
            ("言葉が足りない。", "n4_ba", False),
            ("それはやばい。", "n4_ba", False),
            ("とても嬉しい。", "n4_temo", False),
            ("手紙が届く。", "n4_teoku", False),
            ("本を読んだ。", "n5_noda", False),
            ("水を飲んだ。", "n5_noda", False),
            ("待ってたんだね。", "n5_noda", True),
            ("行かなければならない。", "n4_ba", True),
        )
        for line, pid, expected in cases:
            with self.subTest(line=line, pid=pid):
                analysis = self.grammar.analyze(self.tokenizer.tokenize(line))
                hit_ids = {h.pattern_id for h in analysis.grammar_hits}
                self.assertEqual(pid in hit_ids, expected, f"line={line}, hits={hit_ids}")

    def test_dictionary_kind_language_and_monolingual_non_starvation(self) -> None:
        self.assertEqual(_guess_dict_kind("Daijisen"), "monolingual")
        self.assertEqual(_guess_dict_kind("Kojien"), "monolingual")
        self.assertEqual(_guess_dict_kind("JMdict"), "bilingual")
        self.assertEqual(_guess_dict_kind("Jitendex"), "bilingual")

        # 即使詞典名含 jmdict，只要釋義是中文就歸類為 zh
        zh_in_jmdict = DictEntry("世界", "せかい", "n", ["世界，地球"], "JMdict-zh", "bilingual")
        self.assertEqual(classify_entry_language(zh_in_jmdict), "zh")

        # 單語詞條不被大量雙語詞條擠掉（split LIMIT）
        with tempfile.TemporaryDirectory() as tmp:
            eng = DictionaryEngine(Path(tmp) / "starve.sqlite")
            self.addCleanup(eng.close)
            with eng._lock:
                cur = eng._conn.execute(
                    "INSERT INTO dictionaries(name, kind, revision, entry_count, enabled, priority) "
                    "VALUES ('BigBi', 'bilingual', '1', 40, 1, 1)"
                )
                bi_id = int(cur.lastrowid)
                cur2 = eng._conn.execute(
                    "INSERT INTO dictionaries(name, kind, revision, entry_count, enabled, priority) "
                    "VALUES ('Daijisen', 'monolingual', '1', 1, 1, 50)"
                )
                mono_id = int(cur2.lastrowid)
                eng._conn.executemany(
                    "INSERT INTO terms(dict_id, term, reading, pos, glosses_json, score) VALUES (?, ?, ?, ?, ?, ?)",
                    [(bi_id, "テスト", "てすと", "n", f'["釋義{i}"]', 100 - i) for i in range(40)],
                )
                eng._conn.execute(
                    "INSERT INTO terms(dict_id, term, reading, pos, glosses_json, score) VALUES (?, ?, ?, ?, ?, ?)",
                    (mono_id, "テスト", "てすと", "n", '["試験・検査のこと。"]', 10),
                )
                eng._conn.commit()
            match = eng.lookup_exact_or_deinflect("テスト")
            self.assertIsNotNone(match)
            assert match is not None
            self.assertTrue(match.monolingual_entries)
            self.assertIn("試験", match.monolingual_entries[0].glosses[0])

    def test_security_kwin_websocket_origin_and_self_copy_guard(self) -> None:
        # 1) KWin minimize-anki 腳本絕不最小化 GalAnki
        js = QJSEngine()
        js.evaluate("""
            function win(caption, resourceClass) {
                return {caption: caption, resourceClass: resourceClass, minimized: false};
            }
            var w_anki = win("Anki", "anki");
            var w_gal = win("GalAnki", "galanki");
            var w_gal2 = win("Anki 設定 - GalAnki", "galgame-anki");
            var workspace = {windowList: () => [w_anki, w_gal, w_gal2]};
        """)
        res = js.evaluate(build_kwin_minimize_anki_script(1000))
        self.assertFalse(res.isError(), res.toString())
        self.assertTrue(js.evaluate("w_anki.minimized").toBool())
        self.assertFalse(js.evaluate("w_gal.minimized").toBool())
        self.assertFalse(js.evaluate("w_gal2.minimized").toBool())

        # 2) WebSocket Origin 檢查阻擋外部網頁 CSWSH（含子網域偽裝）
        self.assertTrue(is_allowed_ws_origin(""))
        self.assertTrue(is_allowed_ws_origin("http://127.0.0.1:6677"))
        self.assertTrue(is_allowed_ws_origin("http://localhost:3000"))
        self.assertFalse(is_allowed_ws_origin("http://localhost.evil.com"))
        self.assertFalse(is_allowed_ws_origin("https://evil.example.com"))

        # 3) mark_self_copied 防止複製 AI 提問詞回流進台詞
        mgr = TextIngestionManager()
        received: list[str] = []
        mgr.sentence_received.connect(received.append)
        prompt = "請幫我解析這句日文：地球が壊れたら…"
        mgr.mark_self_copied(prompt)
        mgr._handle_raw_text(prompt, source="clipboard")
        self.assertEqual(received, [])

    def test_progress_store_queue_and_sentence_store_unmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LearningProgressStore(Path(tmp) / "p.sqlite")
            self.addCleanup(store.close)
            # 手動標記狀態不應偽造 seen_count=1
            store.set_grammar_status("n5_noda", "N5", "mastered")
            prog = store.get_all_grammar_progress()["n5_noda"]
            self.assertEqual(prog["status"], "mastered")
            self.assertEqual(prog["seen_count"], 0)

            # 待拆解難句隊列 CRUD
            qid = store.enqueue_study_sentence("地球が壊れたら…", "壊れたら", "壊れる")
            self.assertGreater(qid, 0)
            items = store.list_study_queue()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["focus_surface"], "壊れたら")
            store.mark_study_queue_done(qid)
            self.assertEqual(store.list_study_queue(), [])

        # SentenceStore.unmark_mined 同步回退
        sstore = SentenceStore()
        tok = Token("世界", "世界", "せかい", 0, 2)
        rec = sstore.add(TokenizedSentence("世界", [tok], 1, True, "世界"))
        sstore.mark_mined(rec, "世界")
        self.assertEqual(sstore.today_mined_count, 1)
        self.assertIn("世界", rec.mined_words)
        sstore.unmark_mined(rec, "世界")
        self.assertEqual(sstore.today_mined_count, 0)
        self.assertNotIn("世界", rec.mined_words)
        self.assertNotIn("世界", sstore.known_words)

    def test_overlay_blind_listen_wayland_pos_guard_and_non_modal_studio(self) -> None:
        # 1) 覆蓋層盲聽模式預設關閉；開啟後新句子先隱藏字幕，核對後顯示
        overlay = OverlayWindow("starlight", self.grammar)
        self.addCleanup(overlay.close)
        self.assertFalse(overlay._blind_listen)
        tok = Token("世界", "世界", "せかい", 0, 2)
        rec = SentenceRecord(1, "世界", TokenizedSentence("世界", [tok], 0, False, "世界"), source="mpv")
        overlay._toggle_blind_listen(True)
        overlay.show_record(rec)
        self.assertTrue(overlay.sentence_widget.isHidden())
        self.assertFalse(overlay.blind_hint_label.isHidden())
        overlay.reveal_blind_subtitle()
        self.assertFalse(overlay.sentence_widget.isHidden())
        self.assertTrue(overlay.blind_hint_label.isHidden())

        # 2) Wayland 下 (0, 0) 不覆寫儲存座標
        saved_states: list[dict] = []
        overlay.state_saved.connect(saved_states.append)
        with patch.object(QGuiApplication, "platformName", return_value="wayland"):
            overlay.move(0, 0)
            overlay._emit_state()
        self.assertEqual((saved_states[-1]["x"], saved_states[-1]["y"]), (-1, -1))

        # 3) MainWindow：非模態工作台單例、待拆解隊列與空句清空殘留選詞
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp):
            cfg = AppConfig(
                ws_enabled=False,
                clipboard_watch=False,
                auto_sync_known_words=False,
                auto_launch_anki=False,
                start_in_tray_overlay=False,
            )
            win = MainWindow(config=cfg)
            win.mpv_bridge.stop()
            self.addCleanup(lambda: (setattr(win, "_force_quit", True), win.close()))

            # 全標點句（auto_tok=None）必須清空 lookup_panel 避免誤收錄上句詞
            win._ingest_sentence("地球が壊れたら…", "manual")
            self.assertIsNotNone(win.lookup_panel._current_token)
            win._ingest_sentence("……！？", "manual")
            self.assertIsNone(win.lookup_panel._current_token)
            self.assertFalse(win.lookup_panel.mine_btn.isEnabled())

            # 非模態 3 視圖工作台與 Shift+拆解排入隊列
            win._enqueue_study_sentence("地球が壊れたら…", "壊れたら", "壊れる")
            win.open_learning_blueprint()
            bp1 = win._blueprint_dialog
            self.assertIsNotNone(bp1)
            assert bp1 is not None
            self.assertEqual(bp1.tabs.count(), 3)
            self.assertFalse(bp1.isModal())
            self.assertEqual(bp1.study_map.queue_list.count(), 1)
            # 重開為同一個單例視窗
            win.open_learning_blueprint(open_tab="grammar")
            self.assertIs(win._blueprint_dialog, bp1)
            self.assertIs(bp1.tabs.currentWidget(), bp1.grammar_tab)

    def test_logic_pipeline_and_mutation_lab_generation(self) -> None:
        line = "地球が壊れたら…"
        toks = self.tokenizer.tokenize(line).tokens
        tok = next(t for t in toks if t.surface == "壊れたら")
        focus = focus_for(tok, tokens=toks, sentence=line)
        analysis = self.grammar.analyze(self.tokenizer.tokenize(line))
        pipe = build_logic_pipeline(
            sentence=line,
            token=tok,
            tokens=toks,
            focus=focus,
            analysis=analysis,
        )
        self.assertGreaterEqual(len(pipe.steps), 3)
        for st in pipe.steps:
            self.assertGreaterEqual(len(st.options), 2)
            self.assertTrue(any(o.is_correct for o in st.options))
        muts = build_mutations(line, tok.lemma, tok.surface)
        self.assertGreaterEqual(len(muts), 4)
        self.assertTrue(any("壊れ" in m.after_form for m in muts))
        self.assertTrue(any("壊し" in m.after_sentence or "壊す" in m.after_form for m in muts))


if __name__ == "__main__":
    unittest.main()
