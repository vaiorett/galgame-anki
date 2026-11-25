"""測試跨詞典語意去重、語境相關性排序，以及借鑑 CETPath 的離線日語「🌳 聯想記憶樹」。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from galgame_anki.core.anki_client import AnkiCardPayload, AnkiConnectClient
from galgame_anki.core.anki_templates import BACK_TEMPLATE, CARD_CSS, NOTE_FIELDS
from galgame_anki.core.context_meaning import rank_senses_by_context
from galgame_anki.core.dictionary import (
    DictEntry,
    DictionaryEngine,
    LookupMatch,
    _is_chinese_gloss,
    extract_concise_senses,
)
from galgame_anki.core.voice_pack import VoicePackManager
from galgame_anki.core.word_associations import WordAssociationEngine


def _make_entry(
    term: str,
    reading: str,
    glosses: list[str],
    dict_name: str = "test-dict",
    dict_kind: str = "bilingual",
    pos: str = "名詞",
) -> DictEntry:
    return DictEntry(
        term=term,
        reading=reading,
        pos=pos,
        glosses=glosses,
        dict_name=dict_name,
        dict_kind=dict_kind,
    )


class TestDedupAndAssociations(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_semantic_dedup_and_wiktionary_example_citation_stripping(self) -> None:
        """驗證跨詞典語意子集去重、Wiktionary 例句與 ISBN 引用略過、純英文釋義降噪。"""
        entries_soto = [
            _make_entry("外", "そと", ["外面、外部、戶外"], dict_name="內建口語核心詞庫（雙語）"),
            _make_entry(
                "外",
                "そと",
                ["外面\n2 例箱の外\n箱子的外面\n外は雨\n外面在下雨"],
                dict_name="wty-ja-zh",
            ),
            _make_entry("外", "そと", ["outside\nexterior"], dict_name="JMdict"),
        ]
        senses = extract_concise_senses(entries_soto, max_senses=6)
        self.assertEqual(senses, ["外面、外部、戶外"])

        # Wiktionary 含 ISBN 書目引用的例句組不得外洩中文譯文
        entries_sekai = [
            _make_entry("世界", "せかい", ["世界、天地、領域"], dict_name="內建口語核心詞庫"),
            _make_entry(
                "世界",
                "せかい",
                [
                    "地球上的所有地方或國家\n"
                    "3 例世界の各国から\n"
                    "來自世界各國\n"
                    "そしてぼくはふたつの世界の境界を破ぶってこっちへとびこんできたんだ‼\n"
                    "而我將這兩個世界的邊界打破，到這邊來了！!\n"
                    "1988年 7月30日 [1984年7月25日]，藤子・F・不二雄，→ISBN，162頁：\n"
                    "はじめて見る広い世界が、楽しみだったわ。\n"
                    "我一直期待著，第一次看見這廣闊的世界。\n"
                    "1997年 6月30日 [1980年1月25日]，藤子・F・不二雄，→ISBN，188頁：\n"
                    "內心世界\n"
                    "1 例彼はこの世界に住んでいないようです。\n"
                    "他好像活在自己的世界裡。\n"
                    "女性人名\n"
                    "姓氏"
                ],
                dict_name="wty-ja-zh",
            ),
        ]
        sekai_senses = extract_concise_senses(entries_sekai, max_senses=6)
        self.assertIn("世界、天地、領域", sekai_senses)
        self.assertIn("地球上的所有地方或國家", sekai_senses)
        self.assertIn("內心世界", sekai_senses)
        for leaked in ("我一直期待著", "來自世界各國", "女性人名", "姓氏", "ISBN"):
            self.assertFalse(any(leaked in s for s in sekai_senses), f"不該外洩：{leaked} in {sekai_senses}")

    def test_chinese_gloss_guard_and_pos_stub_cleaning(self) -> None:
        """驗證英文用法註記不會誤判成中文，且詞性存根／編號多義項能乾淨拆分清理。"""
        self.assertFalse(_is_chinese_gloss("as 下から, 下より, etc."))
        self.assertTrue(_is_chinese_gloss("（感情・風勢等）越來越強烈、加劇"))

        mono_entries = [
            _make_entry(
                "募る",
                "つのる",
                ["自動\n勢いなどがますます激しくなる。\nlaw\n非形式的\n他動\n広く呼び掛けて探し集める。"],
                dict_name="wty-ja-ja",
                dict_kind="monolingual",
            )
        ]
        mono_senses = extract_concise_senses(mono_entries, max_senses=4)
        self.assertEqual(mono_senses, ["勢いなどがますます激しくなる", "広く呼び掛けて探し集める"])

        bi_entries = [
            _make_entry(
                "応募",
                "おうぼ",
                ["名?自 サ 应招，应征"],
                dict_name="wty-ja-zh",
            ),
            _make_entry(
                "募る",
                "つのる",
                ["①（感情・風勢等）越來越強烈、加劇 ②募集、招募"],
                dict_name="內建精選雙語詞庫",
            ),
        ]
        oubo_senses = extract_concise_senses([bi_entries[0]], max_senses=4)
        self.assertEqual(oubo_senses, ["应招，应征"])

        tsunoru_senses = extract_concise_senses([bi_entries[1]], max_senses=4)
        self.assertEqual(tsunoru_senses, ["（感情・風勢等）越來越強烈、加劇", "募集、招募"])

        match = LookupMatch(
            surface="外",
            lemma="外",
            reading="そと",
            reasons=[],
            bilingual_entries=[
                _make_entry("外", "そと", ["外面、外部、戶外"]),
                _make_entry("外", "そと", ["外面"]),
            ],
        )
        self.assertEqual(match.best_concise_meaning("bilingual"), "外面、外部、戶外")

    def test_contextual_relevance_ranking_with_japanese_and_chinese_cues(self) -> None:
        """驗證語境排序能同時利用日語搭配線索（一方だ／を）與中文譯文精準推舉首選義項。"""
        senses = ["募集、招募", "（感情・風勢等）越來越強烈、加劇"]

        # 僅靠日語原句「募る一方だ」（無中文翻譯時），也能推舉自動詞漸強義項
        ranked_intrans = rank_senses_by_context(
            senses,
            sentence_translation="",
            raw_sentence="外の世界を知りたいという想いは、募る一方だ",
            target_surface="募る",
        )
        self.assertEqual(ranked_intrans[0], "（感情・風勢等）越來越強烈、加劇")

        # 有「参加者を募る」＋譯文「招募參加者」，自動推舉他動詞招募義項
        ranked_trans = rank_senses_by_context(
            list(reversed(senses)),
            sentence_translation="招募參加者。",
            raw_sentence="参加者を募る。",
            target_surface="募る",
        )
        self.assertEqual(ranked_trans[0], "募集、招募")

    def _build_fixture_dict(self) -> DictionaryEngine:
        db_path = self.tmp_dir / "dict_test.db"
        engine = DictionaryEngine(db_path=db_path, load_starter=False)
        with engine._lock:
            cur = engine._conn.cursor()
            cur.execute(
                "INSERT INTO dictionaries (name, kind, revision, entry_count, priority, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("test-zh", "bilingual", "1", 20, 10, 1),
            )
            bi_id = cur.lastrowid
            cur.execute(
                "INSERT INTO dictionaries (name, kind, revision, entry_count, priority, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("test-freq", "meta", "1", 20, 40, 1),
            )
            meta_id = cur.lastrowid

            terms = [
                ("分かる", "わかる", "自動詞", ["明白、懂、理解"]),
                ("分ける", "わける", "他動詞", ["分開、分配"]),
                ("熱い", "あつい", "形容詞", ["燙的、滾熱的"]),
                ("暑い", "あつい", "形容詞", ["（天氣）炎熱的"]),
                ("厚い", "あつい", "形容詞", ["厚的、深厚的"]),
                ("募る", "つのる", "動詞", ["變得強烈、加劇", "募集、招募"]),
                ("募集", "ぼしゅう", "名詞", ["募集、徵集"]),
                ("応募", "おうぼ", "名詞", ["應徵、報名"]),
                ("言い募る", "いいつのる", "動詞", ["激烈爭辯"]),
                ("世界", "せかい", "名詞", ["世界、天地"]),
                ("世界中", "せかいじゅう", "名詞", ["全世界"]),
                ("視界", "しかい", "名詞", ["視線範圍"]),
            ]
            for term, reading, pos, glosses in terms:
                cur.execute(
                    "INSERT INTO terms (dict_id, term, reading, pos, score, glosses_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (bi_id, term, reading, pos, 100, json.dumps(glosses, ensure_ascii=False)),
                )

            freqs = [
                ("分かる", 80),
                ("分ける", 600),
                ("熱い", 900),
                ("暑い", 700),
                ("厚い", 1200),
                ("募る", 4600),
                ("募集", 1800),
                ("応募", 2400),
                ("言い募る", 9800),
                ("世界", 126),
                ("世界中", 1400),
                ("視界", 1493),
            ]
            for term, rank in freqs:
                cur.execute(
                    "INSERT INTO term_meta (dict_id, term, reading, mode, data_json) VALUES (?, ?, ?, ?, ?)",
                    (meta_id, term, "", "freq", json.dumps({"frequency": rank})),
                )
            engine._conn.commit()
        return engine

    def test_word_association_tree_branches_known_priority_and_spoiler_safety(self) -> None:
        """驗證聯想記憶樹三枝（自他／同音、以熟帶生同漢字詞族、複合詞）＋防劇透作品重現＋Anki 欄位寫入。"""
        dict_eng = self._build_fixture_dict()
        vp_db = self.tmp_dir / "vp_test.db"
        vp = VoicePackManager(db_path=vp_db)

        # 建立模擬劇本行（seq 1, 2 在進度內；seq 50 為結局劇透）
        pack_id = "demo-pack"
        with vp._lock:
            vp._conn.execute(
                "INSERT INTO voice_packs (pack_id, game_name, game_dir, mode, line_count, voiced_line_count, asset_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pack_id, "ATRI Demo", str(self.tmp_dir), "kirikiri", 3, 0, 0),
            )
            vp._conn.executemany(
                "INSERT INTO dialogue_lines (pack_id, seq_index, scene_file, speaker, raw_text, clean_text, norm_exact, norm_core, voice_ids_json, translation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (pack_id, 1, "b101.ks.scn", "夏生", "世界は海に沈んでいた。", "世界は海に沈んでいた。", "世界は海に沈んでいた", "世界は海に沈んでいた", "[]", "世界沉沒在海中。"),
                    (pack_id, 2, "b101.ks.scn", "アトリ", "この世界はとても広いのですね！", "この世界はとても広いのですね！", "この世界はとても広いのですね", "この世界はとても広いのですね", "[]", "這個世界真遼闊呢！"),
                    (pack_id, 50, "b701.ks.scn", "アトリ", "最後に世界を救う真実を話します。", "最後に世界を救う真実を話します。", "最後に世界を救う真実を話します", "最後に世界を救う真実を話します", "[]", "最後告訴你結局劇透。"),
                ],
            )
            vp._conn.commit()
            vp._cursors[pack_id] = 10

        assoc = WordAssociationEngine(dict_eng, vp)

        # 1) 自他動詞配對：分かる -> 分ける
        tree_wakaru = assoc.build_tree("分かる", "わかる", known_words={"分ける"})
        self.assertTrue(any(n.term == "分ける" and n.relation == "transitivity" for n in tree_wakaru.confusables))

        # 2) 同音異義詞辨析：熱い -> 暑い / 厚い
        tree_atsui = assoc.build_tree("熱い", "あつい", known_words={"暑い"})
        conf_terms = [n.term for n in tree_atsui.confusables]
        self.assertIn("暑い", conf_terms)
        self.assertIn("厚い", conf_terms)
        self.assertEqual(conf_terms[0], "暑い")  # Anki 熟詞優先排列

        # 3) 同漢字詞族「以熟帶生」＋複合詞：募る（応募 詞頻 2400 雖低於 募集 1800，但為熟詞應排第 1）
        tree_tsunoru = assoc.build_tree("募る", "つのる", known_words={"応募"})
        self.assertGreaterEqual(len(tree_tsunoru.kanji_family), 2)
        self.assertEqual(tree_tsunoru.kanji_family[0].term, "応募")
        self.assertTrue(tree_tsunoru.kanji_family[0].is_known)
        self.assertTrue(any(n.term == "言い募る" for n in tree_tsunoru.compounds))

        # 4) 同作品防劇透重現句：cursor=10 僅回傳 b101，絕不回傳 seq=50 的 b701 結局！
        tree_sekai = assoc.build_tree("世界", "せかい", active_pack_id=pack_id)
        self.assertTrue(any(n.term == "世界中" for n in tree_sekai.compounds))
        self.assertEqual(len(tree_sekai.game_recurrences), 2)
        self.assertTrue(all(r.scene_file == "b101.ks.scn" for r in tree_sekai.game_recurrences))

        # 5) HTML 與 Anki 模板欄位驗證（嚴守原生主題，不含舊版 #60a5fa）
        self.assertIn("RelatedWords", NOTE_FIELDS)
        self.assertIn("{{#RelatedWords}}", BACK_TEMPLATE)
        self.assertNotIn("#60a5fa", CARD_CSS)
        anki_html = tree_tsunoru.to_anki_html()
        self.assertIn("応募", anki_html)
        self.assertIn("言い募る", anki_html)
        self.assertIn("✓", anki_html)
        panel_html = tree_sekai.to_panel_html("sakura")
        self.assertIn("本作品其他出現場景", panel_html)

        # 6) AnkiConnectClient 寫入 RelatedWords 欄位
        client = AnkiConnectClient("http://127.0.0.1:8765")
        captured_params: dict = {}

        def _fake_invoke(action: str, **params):
            if action == "addNote":
                captured_params.update(params)
                return 424242
            return None

        with patch.object(client, "invoke", side_effect=_fake_invoke):
            note_id = client.add_card(
                "GalAnki",
                "GalAnki Refold Card",
                AnkiCardPayload(
                    target_word="募る",
                    target_reading="つのる",
                    pitch_accent="2",
                    sentence="想いは、【募る】一方だ。",
                    sentence_furigana="想いは、募る一方だ。",
                    definition_bilingual="變得強烈、加劇",
                    definition_monolingual="勢いなどがますます激しくなる",
                    related_words_html=anki_html,
                ),
            )
        self.assertEqual(note_id, 424242)
        self.assertEqual(captured_params["note"]["fields"]["RelatedWords"], anki_html)

        vp.close()
        dict_eng.close()

    def test_concurrent_dictionary_and_association_thread_safety(self) -> None:
        """回歸測試：主執行緒查詞與背景執行緒建構聯想記憶樹同時存取 SQLite 不得發生競態錯誤。"""
        dict_eng = self._build_fixture_dict()
        assoc = WordAssociationEngine(dict_eng)
        errors: list[Exception] = []

        def _worker_assoc() -> None:
            try:
                for _ in range(15):
                    assoc.build_tree("世界", "せかい", known_words={"視界"})
                    assoc.build_tree("熱い", "あつい", known_words={"暑い"})
            except Exception as exc:
                errors.append(exc)

        def _worker_lookup() -> None:
            try:
                for _ in range(30):
                    m = dict_eng.lookup_exact_or_deinflect("募る")
                    assert m is not None
                    dict_eng.lookup_exact_or_deinflect("分かる")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_worker_assoc),
            threading.Thread(target=_worker_lookup),
            threading.Thread(target=_worker_assoc),
            threading.Thread(target=_worker_lookup),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        dict_eng.close()

    def test_card_forge_dialog_context_ranking_and_related_words(self) -> None:
        """Offscreen UI 測試：精煉製卡視窗會用 pack_translation 自動重排釋義膠囊並輸出 RelatedWords。"""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication

        from galgame_anki.ui.card_forge_dialog import CardForgeDialog

        app = QApplication.instance() or QApplication([])
        dlg = CardForgeDialog(
            stage_mode="bilingual",
            sentence_highlighted="参加者を【募る】。",
            raw_sentence="参加者を募る。",
            target_word="募る",
            target_reading="つのる",
            pitch_accent="2",
            bi_senses=["變得強烈、加劇", "募集、招募"],
            mono_senses=["広く呼び掛けて探し集める"],
            default_bi="變得強烈、加劇",
            default_mono="広く呼び掛けて探し集める",
            sentence_translation="",
            pack_translation="招募參加者。",
            audio_clip=None,
            screenshot_clip=None,
            include_audio_default=False,
            include_screenshot_default=False,
            thread_pool=QThreadPool.globalInstance(),
            related_words_html='<div class="rel-group">測試聯想</div>',
            related_words_preview="🌿 同字：✓募集(募集)",
        )
        # 透過 pack_translation「招募參加者。」與日語「を」，自動把「募集、招募」排到 #1 並預填
        self.assertEqual(dlg.bi_edit.toPlainText().strip(), "募集、招募")
        self.assertTrue(dlg.include_related_chk.isChecked())
        dlg._confirm()
        self.assertIsNotNone(dlg.result_data)
        self.assertIn("測試聯想", dlg.result_data.related_words_html)

        dlg.include_related_chk.setChecked(False)
        dlg._confirm()
        self.assertIsNotNone(dlg.result_data)
        self.assertEqual(dlg.result_data.related_words_html, "")

        dlg.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
