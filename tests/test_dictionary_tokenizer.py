"""測試本地 SQLite 詞典、Yomitan .zip 匯入、分詞與 1T 判定。"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.tokenizer import (
    JapaneseTokenizer,
    build_conjugated_inline_furigana,
    build_conjugated_ruby,
    build_ruby_word,
    clean_hooked_text,
    highlight_target_in_sentence,
)


class TestDictionaryAndTokenizer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_dict.db"
        self.engine = DictionaryEngine(db_path=self.db_path, load_starter=True)
        self.tokenizer = JapaneseTokenizer(self.engine)

    def tearDown(self) -> None:
        self.engine.close()
        self.tmp.cleanup()

    def test_starter_dict_lookup_and_deinflection(self) -> None:
        match = self.engine.lookup_exact_or_deinflect("かぶれすぎた")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.lemma, "かぶれる")
        self.assertTrue(match.bilingual_entries)
        self.assertTrue(match.monolingual_entries)

        match2 = self.engine.lookup_exact_or_deinflect("踏ん張れなかった")
        self.assertIsNotNone(match2)
        assert match2 is not None
        self.assertEqual(match2.lemma, "踏ん張る")
        self.assertEqual(match2.reading, "ふんばる")

    def test_yomitan_zip_import_structured_content_and_meta(self) -> None:
        zip_path = Path(self.tmp.name) / "sample_yomitan.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "index.json",
                json.dumps({"title": "測試明鏡国語辞典", "format": 3, "revision": "2026"}),
            )
            zf.writestr(
                "term_bank_1.json",
                json.dumps(
                    [
                        [
                            "暁",
                            "あかつき",
                            "n",
                            "",
                            100,
                            [
                                {
                                    "type": "structured-content",
                                    "content": [
                                        {"tag": "div", "content": "夜明け前のまだ暗いころ。"},
                                        {"tag": "div", "content": "望んでいたことが実現したとき。"},
                                    ],
                                }
                            ],
                            1,
                            "",
                        ]
                    ],
                    ensure_ascii=False,
                ),
            )
            zf.writestr(
                "term_meta_bank_1.json",
                json.dumps(
                    [
                        ["暁", "pitch", {"reading": "あかつき", "pitches": [{"position": 0}]}],
                        ["暁", "freq", {"reading": "あかつき", "frequency": 4321}],
                    ],
                    ensure_ascii=False,
                ),
            )

        res = self.engine.import_yomitan_zip(zip_path)
        self.assertEqual(res["kind"], "monolingual")
        self.assertEqual(res["entry_count"], 1)

        found = self.engine.lookup_exact_or_deinflect("暁")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.reading, "あかつき")
        self.assertIn("0", found.pitches)
        self.assertEqual(found.frequency, 4321)
        self.assertIn("夜明け前", found.primary_definition("monolingual"))

    def test_1t_detection_and_highlighting(self) -> None:
        sent = "外の世界を知りたいという想いは、募る一方だ"
        known = {"外", "世界", "知る", "想い", "一方だ", "一方"}
        tok_sent = self.tokenizer.tokenize(sent, known_words=known)

        self.assertTrue(tok_sent.is_1t_sentence)
        one_t_tokens = [t for t in tok_sent.tokens if t.is_1t_candidate]
        self.assertEqual(len(one_t_tokens), 1)
        self.assertEqual(one_t_tokens[0].lemma, "募る")

        highlighted = highlight_target_in_sentence(sent, one_t_tokens[0].start, one_t_tokens[0].end)
        self.assertEqual(highlighted, "外の世界を知りたいという想いは、【募る】一方だ")

    def test_ruby_and_clean_hooked_text(self) -> None:
        self.assertEqual(build_ruby_word("食べる", "たべる"), "<ruby>食<rt>た</rt></ruby>べる")
        self.assertEqual(build_ruby_word("頃合い", "ころあい"), "<ruby>頃合<rt>ころあ</rt></ruby>い")
        self.assertEqual(build_conjugated_ruby("急いでる", "急ぐ", "いそぐ"), "<ruby>急<rt>いそ</rt></ruby>いでる")
        self.assertEqual(build_conjugated_ruby("泳げない", "泳ぐ", "およぐ"), "<ruby>泳<rt>およ</rt></ruby>げない")
        self.assertEqual(build_conjugated_ruby("怖く", "怖い", "こわい"), "<ruby>怖<rt>こわ</rt></ruby>く")
        self.assertEqual(build_conjugated_inline_furigana("急いでる", "急ぐ", "いそぐ"), "急(いそ)いでる")
        self.assertEqual(clean_hooked_text("ああいいううええ"), "あいうえ")

    def test_wiktionary_inflection_stub_passthrough_and_footer_strip(self) -> None:
        zip_path = Path(self.tmp.name) / "wty_stub_test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "index.json",
                json.dumps({"title": "Wiktionary 日中測試", "format": 3, "revision": "2026"}),
            )
            zf.writestr(
                "term_bank_1.json",
                json.dumps(
                    [
                        [
                            "怖く",
                            "こわく",
                            "adj-i",
                            "",
                            10,
                            ["怖いcontinuative\nWiktionary | Kaikki"],
                            1,
                            "",
                        ],
                        [
                            "怖い",
                            "こわい",
                            "adj-i",
                            "",
                            100,
                            [
                                "词源派生自強い的古典形式。\n在中世原意派生出吓人的。\n可怕的、令人害怕的2 例私は怖い。\nWiktionary | Kaikki"
                            ],
                            2,
                            "",
                        ],
                    ],
                    ensure_ascii=False,
                ),
            )

        self.engine.import_yomitan_zip(zip_path)
        found = self.engine.lookup_exact_or_deinflect("怖く")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.lemma, "怖い")
        primary = found.primary_definition("bilingual")
        self.assertIn("可怕", primary)
        self.assertNotIn("词源", primary)
        self.assertNotIn("Wiktionary | Kaikki", primary)
        best = found.best_concise_meaning("bilingual")
        self.assertIn("可怕", best)
        self.assertNotIn("例：", best)

    def test_dictionary_enable_disable_priority_and_catalog_match(self) -> None:
        # 匯入一部模擬英文詞典與一部模擬日中詞典
        for fname, title, term, reading, gloss in [
            ("wty_zh.zip", "wty-ja-zh", "確認", "かくにん", "確認、核實"),
            ("jmdict_en.zip", "JMdict [2026]", "確認", "かくにん", "confirmation; verification"),
        ]:
            zp = Path(self.tmp.name) / fname
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("index.json", json.dumps({"title": title, "format": 3, "revision": "1"}))
                zf.writestr(
                    "term_bank_1.json",
                    json.dumps([[term, reading, "n", "", 10, [gloss], 1, ""]], ensure_ascii=False),
                )
            self.engine.import_yomitan_zip(zp)

        # 確認目錄已安裝狀態偵測正確
        inst_zh = self.engine.find_installed_for_catalog("wty-ja-zh")
        inst_en = self.engine.find_installed_for_catalog("jmdict-en")
        self.assertIsNotNone(inst_zh)
        self.assertIsNotNone(inst_en)
        assert inst_zh is not None and inst_en is not None

        # 套用「zh_focus」預設後，英文 JMdict 應自動停用，查詞不再混入英文！
        self.engine.apply_dictionary_preset("zh_focus")
        m = self.engine.lookup_exact_or_deinflect("確認")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertIn("確認", m.best_concise_meaning("bilingual"))
        self.assertNotIn("confirmation", m.primary_definition("bilingual"))

        # 手動停用日中詞典後再確認
        self.engine.set_dictionary_enabled(inst_zh["id"], False)
        self.assertFalse(self.engine.find_installed_for_catalog("wty-ja-zh")["enabled"])

        # 獨立不規則動詞 したら -> する 必須正確還原（不誤判北海道方言「再見」）
        from galgame_anki.core.deinflector import deinflect

        cands = [c.term for c in deinflect("したら")]
        self.assertIn("する", cands)

    def test_frequency_disambiguation_wakatta_and_contextual_meaning(self) -> None:
        """回歸測試：即使詞典同時收錄冷門詞「分かつ (わかつ)」，「わかった」仍必須精準還原為「分かる / わかる（明白了）」！"""
        from galgame_anki.core.context_meaning import (
            format_contextual_meaning,
            rank_senses_by_context,
        )

        # 刻意灌入高分冷門詞「分かつ (わかつ)」，驗證詞頻＋Janome 統計消歧不會被誤導
        zp = Path(self.tmp.name) / "wakatsu_trap.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("index.json", json.dumps({"title": "wty-trap", "format": 3, "revision": "1"}))
            zf.writestr(
                "term_bank_1.json",
                json.dumps(
                    [["分かつ", "わかつ", "v5t", "", 999, ["1. 分别，分离。"], 1, ""]],
                    ensure_ascii=False,
                ),
            )
            zf.writestr(
                "term_meta_bank_1.json",
                json.dumps([["分かつ", "freq", {"reading": "わかつ", "frequency": 28000}]], ensure_ascii=False),
            )
        self.engine.import_yomitan_zip(zp)

        ts = self.tokenizer.tokenize("『わ、わかった！　えいっ』")
        w_tok = next((t for t in ts.tokens if t.surface == "わかった"), None)
        self.assertIsNotNone(w_tok)
        assert w_tok is not None and w_tok.lookup is not None
        self.assertIn(w_tok.lemma, ("わかる", "分かる"))
        self.assertNotEqual(w_tok.lemma, "分かつ")

        base_m = w_tok.lookup.best_concise_meaning("bilingual", strict=True)
        ctx_m = format_contextual_meaning(w_tok.surface, w_tok.lemma, base_m, w_tok.lookup.reasons)
        self.assertIn("明白", ctx_m)
        self.assertNotIn("分离", ctx_m)

        # 驗證整句語境義項排序
        ranked = rank_senses_by_context(["分别，分离", "明白，理解", "区分"], "我明白了！")
        self.assertEqual(ranked[0], "明白，理解")

        # 驗證「おいおい」與「足(あし)」不再誤判
        ts2 = self.tokenizer.tokenize("『おいおい、そんな事したら……』")
        oi_tok = next(t for t in ts2.tokens if t.surface == "おいおい")
        self.assertIn("喂喂", oi_tok.lookup.best_concise_meaning("bilingual"))
        self.assertIn("<ruby>事<rt>こと</rt></ruby>", ts2.furigana_html)

        ts3 = self.tokenizer.tokenize("おっこちるわよ！あんたその足じゃ泳げないでしょっ")
        self.assertIn("<ruby>足<rt>あし</rt></ruby>", ts3.furigana_html)


if __name__ == "__main__":
    unittest.main()
