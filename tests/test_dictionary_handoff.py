"""驗證詞典盤點交接（語塊匹配、義項語境排序、語言分流、Jitendex 結構化、原子更新與按需網頁查證）。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QPushButton

from galgame_anki.config import AppConfig
from galgame_anki.core.context_meaning import explain_lookup_in_sentence
from galgame_anki.core.dictionary import DictionaryEngine, DictEntry, _strip_jitendex_pos_prefix
from galgame_anki.core.phrase_matcher import build_phrase_token, find_phrase_for_token
from galgame_anki.core.tokenizer import JapaneseTokenizer, highlight_target_in_sentence
from galgame_anki.core.web_lookup import build_web_lookup_url
from galgame_anki.ui.extra_dialogs import DictionaryManagerDialog
from galgame_anki.ui.lookup_popup import LookupDetailPanel, OverlayLookupPopup
from galgame_anki.ui.main_window import MainWindow
from galgame_anki.ui.overlay_window import OverlayWindow


def _write_yomitan_zip(
    zip_path: Path,
    title: str,
    terms: list[list],
    meta: list[list] | None = None,
    revision: str = "2026-11-25",
    attribution: str = "CC BY-SA 4.0",
) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "index.json",
            json.dumps(
                {"title": title, "format": 3, "revision": revision, "attribution": attribution},
                ensure_ascii=False,
            ),
        )
        if terms:
            zf.writestr("term_bank_1.json", json.dumps(terms, ensure_ascii=False))
        if meta:
            zf.writestr("term_meta_bank_1.json", json.dumps(meta, ensure_ascii=False))


class TestDictionaryHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.engine = self._build_synthetic_dictionary(self.tmp_dir / "handoff_dict.db")
        self.tokenizer = JapaneseTokenizer(self.engine)

    def tearDown(self) -> None:
        self.engine.close()
        self._tmp.cleanup()

    def _build_synthetic_dictionary(self, db_path: Path) -> DictionaryEngine:
        """建立輕量測試詞典（免依賴本機 312 MB 個人庫），精準重現交接報告中的各項資料型態。"""
        eng = DictionaryEngine(db_path=db_path, load_starter=True)

        # 1) wty-ja-zh（含詞源、同形詞、non-lemma alt-of、簡體與多詞性條目）
        wty_zh = self.tmp_dir / "wty_zh.zip"
        _write_yomitan_zip(
            wty_zh,
            "wty-ja-zh",
            [
                [
                    "君", "きみ", "n", "", 0,
                    ["词源⟨ki₁mi₁⟩\n源自上古日語。\n国家统治者\n天皇；君主；天子\n领主\nWiktionary | Kaikki"],
                    1, "",
                ],
                [
                    "君", "きみ", "pron inf", "", 0,
                    ["词源⟨ki₁mi₁⟩\n源自上古日語。\n你1 例君は誰？\n你是誰？\nWiktionary | Kaikki"],
                    2, "",
                ],
                [
                    "君", "くん", "suf", "", 0,
                    ["词源源自中古漢語。\n后接于男孩子的名字的称谓后缀。\nWiktionary | Kaikki"],
                    3, "",
                ],
                [
                    "君主", "くんしゅ", "", "", 0,
                    ["君主【くんしゅ】\n君主。\n君主国\n君主国。\nWiktionary | Kaikki"],
                    4, "",
                ],
                [
                    "とりま", "とりま", "intj sl", "", 0,
                    ["词源とりあえず、まあ的缩约。\n总之就先，目前，暂且，姑且\nWiktionary | Kaikki"],
                    5, "",
                ],
                [
                    "ガチ", "ガチ", "adj", "", 0,
                    ["词源缩略/变换自ガチンコ。\n真的，确实的，认真的\nWiktionary | Kaikki"],
                    6, "",
                ],
                [
                    "ワンチャン", "ワンチャン", "n", "", 0,
                    ["词源ワンチャンス的截斷形式。\n有机会，或许可以\nWiktionary | Kaikki"],
                    7, "",
                ],
                [
                    "ワンちゃん", "ワンちゃん", "n child", "", 0,
                    ["词源源自ワン + ちゃん。\n小狗，狗狗\nWiktionary | Kaikki"],
                    8, "",
                ],
                [
                    "犬", "いぬ", "n", "", 0,
                    ["狗\n僕人\nWiktionary | Kaikki"],
                    9, "",
                ],
                [
                    "流石", "さすが", "adj_noun", "", 0,
                    [
                        "词源然(さ) (sa，“如此”)\n爲(す) (su，“做”)\n源自さすがに並省略了末尾的に。\n"
                        "詞彙さすがに (sasugani) 由以下的部分組成：\n字面意思是「以這種方式」。\n漢字為熟字訓。\n"
                        "即使……也；虽然……也都\n不愧（是）；毕竟（是）；的确（是）；果然（是）\n"
                        "但是，毕竟还是\nWiktionary | Kaikki"
                    ],
                    10, "",
                ],
                ["さすがに", "さすがに", "non-lemma", "", 0, ["さすがalt-of"], 11, ""],
                ["さすがに", "さすがに", "non-lemma", "", 0, ["流石alternative"], 12, ""],
                ["遣う", "つかう", "v5 vt", "", 0, ["使用，運用"], 13, ""],
                ["腰", "こし", "n", "", 0, ["腰，腰部"], 14, ""],
                ["折る", "おる", "v5 vt", "", 0, ["折斷，彎曲"], 15, ""],
                ["橋", "はし", "n", "", 0, ["橋，橋梁"], 16, ""],
                ["橋", "きょう", "n", "", 0, ["橋（音讀）"], 17, ""],
            ],
        )
        eng.import_yomitan_zip(wty_zh, kind_override="bilingual", priority_override=5, source_id="wty-ja-zh")

        # 2) JMdict（日英備援，含慣用語與詞性主序）
        jmdict = self.tmp_dir / "jmdict.zip"
        _write_yomitan_zip(
            jmdict,
            "JMdict [2026-11-23]",
            [
                ["君", "きみ", "1 pn fam", "", 999800, ["you\nbuddy\npal"], 1, ""],
                ["君", "きみ", "2 n", "", 999799, ["monarch\nruler\nsovereign"], 2, ""],
                ["君", "くん", "1 suf", "", 999800, ["Mr\nmaster\nboy"], 3, ""],
                ["君主", "くんしゅ", "n", "", 1999800, ["monarch\nsovereign\nruler"], 4, ""],
                ["君主国", "くんしゅこく", "n", "", -200, ["monarchy (country)"], 5, ""],
                ["気を遣う", "きをつかう", "exp v5u", "v5", -10200, ["to worry (about)\nto be careful (about)"], 6, ""],
                ["腰を折る", "こしをおる", "1 exp v5r", "v5", -200, ["to interrupt (a story, etc.)"], 7, ""],
                ["腰を折る", "こしをおる", "2 exp v5r", "v5", -201, ["to bend at the waist"], 8, ""],
                ["話の腰を折る", "はなしのこしをおる", "exp v5r id", "v5", -200, ["to interfere\nto butt in\nto interrupt someone"], 9, ""],
                ["とりま", "とりま", "exp sl abbr", "", -200, ["anyhow\nfor now"], 10, ""],
                ["ガチ", "ガチ", "adj-na col", "", -10200, ["serious\nearnest\nreal"], 11, ""],
                ["ワンチャン", "ワンチャン", "1 n abbr", "", -300, ["one chance\nonly opportunity"], 12, ""],
                ["ワンちゃん", "ワンちゃん", "n chn", "", 999800, ["doggie\npooch"], 13, ""],
                ["流石に", "さすがに", "1 adv uk", "", -200, ["as one would expect\nnaturally\nindeed"], 14, ""],
                ["遣う", "つかう", "v5u vt", "v5", 1000, ["to use\nto employ"], 15, ""],
                ["純英詞", "じゅんえいし", "n", "", 100, ["english-only fallback term"], 16, ""],
            ],
        )
        eng.import_yomitan_zip(jmdict, kind_override="bilingual", priority_override=10, source_id="jmdict-en")

        # 3) wty-ja-ja（日日參考，含 君=卵黄 vs 代名詞、気を遣う reading 缺假名、月(ガチ) 陷阱、純日日條目）
        wty_ja = self.tmp_dir / "wty_ja.zip"
        _write_yomitan_zip(
            wty_ja,
            "wty-ja-ja",
            [
                ["君", "きみ", "名", "", 0, ["鳥等の卵の内部にある卵細胞。きいみ。卵黄。\nWiktionary | Kaikki"], 1, ""],
                ["君", "きみ", "代", "", 0, ["同輩又は目下の者に対する二人称。\nWiktionary | Kaikki"], 2, ""],
                ["君主", "くんしゅ", "名", "", 0, ["国家の最高統治者。\nWiktionary | Kaikki"], 3, ""],
                ["君主国", "くんしゅこく", "名", "", 0, ["君主が統治する国家。\nWiktionary | Kaikki"], 4, ""],
                ["気を遣う", "気を遣う", "成句 idio", "", 0, ["あれこれと配慮する。\nWiktionary | Kaikki"], 5, ""],
                ["とりま", "とりま", "感動詞 俗", "", 0, ["語源とりあえず、まあの縮約\nとりあえず\nWiktionary | Kaikki"], 6, ""],
                ["月", "ガチ", "名", "", 0, ["（つき）「つき」を参照。\nWiktionary | Kaikki"], 7, ""],
                ["さすが", "さすが", "副", "", 0, ["予想や期待のとおりで納得するさま。\nWiktionary | Kaikki"], 8, ""],
                ["さすがに", "さすがに", "副", "", 0, ["そうだと思うが、やはり。\nWiktionary | Kaikki"], 9, ""],
                ["純日詞", "じゅんにちし", "名", "", 0, ["日本語辞典にしかない語の説明。\nWiktionary | Kaikki"], 10, ""],
            ],
        )
        eng.import_yomitan_zip(wty_ja, kind_override="monolingual", priority_override=15, source_id="wty-ja-ja")

        # 4) Jitendex（測試 structured-content 節點邊界解析）
        jitendex = self.tmp_dir / "jitendex.zip"
        _write_yomitan_zip(
            jitendex,
            "Jitendex.org [2026-11-23]",
            [
                [
                    "気を遣う", "きをつかう", "", "v5", -1,
                    [
                        {
                            "type": "structured-content",
                            "content": [
                                {"tag": "span", "data": {"content": "part-of-speech-info"}, "content": "exp"},
                                {"tag": "span", "data": {"content": "part-of-speech-info"}, "content": "5-dan"},
                                {
                                    "tag": "ul",
                                    "data": {"content": "glossary"},
                                    "content": [
                                        {"tag": "li", "content": "to worry (about)"},
                                        {"tag": "li", "content": "to fuss (over)"},
                                    ],
                                },
                            ],
                        }
                    ],
                    1, "",
                ],
                [
                    "腰を折る", "こしをおる", "", "v5", 0,
                    [
                        {
                            "type": "structured-content",
                            "content": [
                                {"tag": "span", "data": {"content": "part-of-speech-info"}, "content": "exp"},
                                {"tag": "span", "data": {"content": "part-of-speech-info"}, "content": "5-dan"},
                                {
                                    "tag": "ul",
                                    "data": {"content": "glossary"},
                                    "content": [
                                        {"tag": "li", "content": "to interrupt (a story, etc.)"},
                                        {"tag": "li", "content": "to bend at the waist"},
                                    ],
                                },
                            ],
                        }
                    ],
                    2, "",
                ],
            ],
        )
        eng.import_yomitan_zip(jitendex, kind_override="bilingual", priority_override=25, source_id="jitendex")

        # 5) 音調與詞頻（驗證依讀音精準配對）
        meta_zip = self.tmp_dir / "meta.zip"
        _write_yomitan_zip(
            meta_zip,
            "Kanjium Pitch & JPDB Test",
            [],
            meta=[
                ["君", "pitch", {"reading": "きみ", "pitches": [{"position": 0}]}],
                ["君", "pitch", {"reading": "くん", "pitches": [{"position": 0}]}],
                ["橋", "pitch", {"reading": "はし", "pitches": [{"position": 2}]}],
                ["橋", "pitch", {"reading": "きょう", "pitches": [{"position": 1}]}],
                ["月", "freq", {"reading": "つき", "frequency": 160}],
                ["ガチ", "freq", {"reading": "ガチ", "frequency": 3200}],
            ],
        )
        eng.import_yomitan_zip(meta_zip, kind_override="meta", priority_override=40, source_id="meta-test")
        return eng

    def test_kimi_vs_tanaka_kun_and_kunshu_vs_kunshukoku(self) -> None:
        """驗收：君(きみ)優先「你」不誤出國家統治者或卵黃；田中君辨為くん後綴；君主不誤出君主國且與君主国分開。"""
        ts_kimi = self.tokenizer.tokenize("君は誰？")
        t_kimi = next(t for t in ts_kimi.tokens if t.surface == "君")
        self.assertEqual(t_kimi.reading, "きみ")
        ctx_kimi, _ = explain_lookup_in_sentence(t_kimi, "君は誰？", "")
        self.assertEqual(ctx_kimi, "你")
        assert t_kimi.lookup is not None
        by_lang = t_kimi.lookup.senses_by_language(convert_tw=True)
        self.assertEqual(by_lang["zh"][0].text, "你")
        self.assertIn("二人称", by_lang["ja"][0].text)
        self.assertNotIn("卵黄", by_lang["ja"][0].text)

        ts_tanaka = self.tokenizer.tokenize("田中君は優しい。")
        t_kun = next(t for t in ts_tanaka.tokens if t.surface == "君")
        self.assertEqual(t_kun.reading, "くん")
        ctx_kun, _ = explain_lookup_in_sentence(t_kun, "田中君は優しい。", "")
        self.assertIn("後綴", ctx_kun)

        ts_kunshu = self.tokenizer.tokenize("あの君主は誰？")
        t_kunshu = next(t for t in ts_kunshu.tokens if t.surface == "君主")
        ctx_kunshu, _ = explain_lookup_in_sentence(t_kunshu, "あの君主は誰？", "")
        self.assertEqual(ctx_kunshu, "君主")
        self.assertNotIn("君主國", ctx_kunshu)

        ts_state = self.tokenizer.tokenize("君主国について学ぶ。")
        t_k = next(t for t in ts_state.tokens if t.surface == "君主")
        pm_state = find_phrase_for_token("君主国について学ぶ。", ts_state.tokens, t_k, self.engine)
        self.assertIsNotNone(pm_state)
        assert pm_state is not None
        self.assertEqual(pm_state.surface, "君主国")
        self.assertEqual(pm_state.lookup.reading, "くんしゅこく")

    def test_phrase_idioms_slang_and_standalone_toggle(self) -> None:
        """驗收：気を遣う、話の腰を折る vs 深々と腰を折る、とりま、ガチでワンチャン vs 犬のワンちゃん，並維持原 1T 不變。"""
        # 1) 先輩に気を遣う。（任一詞都能帶出語塊，且不改動原本分詞與 1T 統計）
        s1 = "先輩に気を遣う。"
        ts1 = self.tokenizer.tokenize(s1)
        orig_surfaces = [t.surface for t in ts1.tokens]
        orig_unknown = ts1.unknown_count
        for surf in ("気", "遣う"):
            tok = next(t for t in ts1.tokens if t.surface == surf)
            pm = find_phrase_for_token(s1, ts1.tokens, tok, self.engine)
            self.assertIsNotNone(pm)
            assert pm is not None
            self.assertEqual(pm.surface, "気を遣う")
            ptok = build_phrase_token(pm)
            ctx, _ = explain_lookup_in_sentence(ptok, s1, "")
            self.assertIn("顧慮先輩", ctx)
            self.assertEqual(highlight_target_in_sentence(s1, ptok.start, ptok.end), "先輩に【気を遣う】。")
            # wty-ja-ja 缺假名讀音條目應已對齊並合併入日日分頁
            self.assertTrue(pm.lookup.senses_by_language()["ja"])
        self.assertEqual([t.surface for t in ts1.tokens], orig_surfaces)
        self.assertEqual(ts1.unknown_count, orig_unknown)

        # 活用態「先輩に気を遣った。」亦能還原為語塊「気を遣う」
        s1_past = "先輩に気を遣った。"
        ts1_past = self.tokenizer.tokenize(s1_past)
        tok_past = next(t for t in ts1_past.tokens if "遣" in t.surface)
        pm_past = find_phrase_for_token(s1_past, ts1_past.tokens, tok_past, self.engine)
        self.assertIsNotNone(pm_past)
        assert pm_past is not None
        self.assertEqual(pm_past.lookup.lemma, "気を遣う")

        # 2) 話の腰を折る vs 深々と腰を折る
        s2a = "話の腰を折るつもりはない。"
        ts2a = self.tokenizer.tokenize(s2a)
        pm2a = find_phrase_for_token(s2a, ts2a.tokens, next(t for t in ts2a.tokens if t.surface == "腰"), self.engine)
        self.assertIsNotNone(pm2a)
        assert pm2a is not None
        self.assertEqual(pm2a.surface, "話の腰を折る")
        self.assertIn("打斷話頭", explain_lookup_in_sentence(build_phrase_token(pm2a), s2a, "")[0])

        s2b = "深々と腰を折る。"
        ts2b = self.tokenizer.tokenize(s2b)
        pm2b = find_phrase_for_token(s2b, ts2b.tokens, next(t for t in ts2b.tokens if t.surface == "腰"), self.engine)
        self.assertIsNotNone(pm2b)
        assert pm2b is not None
        self.assertEqual(pm2b.surface, "腰を折る")
        self.assertIn("彎腰", explain_lookup_in_sentence(build_phrase_token(pm2b), s2b, "")[0])

        # 3) とりま / ガチ / ワンチャン vs 犬のワンちゃん
        s3 = "とりま駅に行こう。"
        ts3 = self.tokenizer.tokenize(s3)
        pm3 = find_phrase_for_token(s3, ts3.tokens, ts3.tokens[0], self.engine)
        self.assertIsNotNone(pm3)
        assert pm3 is not None
        self.assertEqual(pm3.surface, "とりま")
        self.assertIn("總之就先", explain_lookup_in_sentence(build_phrase_token(pm3), s3, "")[0])

        s4 = "ガチでワンチャンある。"
        ts4 = self.tokenizer.tokenize(s4)
        t_gachi = next(t for t in ts4.tokens if t.surface == "ガチ")
        self.assertEqual(t_gachi.lemma, "ガチ")
        self.assertIn("真的", explain_lookup_in_sentence(t_gachi, s4, "")[0])
        pm4 = find_phrase_for_token(s4, ts4.tokens, next(t for t in ts4.tokens if t.surface == "ワン"), self.engine)
        self.assertIsNotNone(pm4)
        assert pm4 is not None
        self.assertEqual(pm4.surface, "ワンチャン")
        self.assertIn("有機會", explain_lookup_in_sentence(build_phrase_token(pm4), s4, "")[0])

        s5 = "犬のワンちゃんが走った。"
        ts5 = self.tokenizer.tokenize(s5)
        t_inu = next(t for t in ts5.tokens if t.surface == "犬")
        self.assertEqual(explain_lookup_in_sentence(t_inu, s5, "")[0], "狗")
        pm5 = find_phrase_for_token(s5, ts5.tokens, next(t for t in ts5.tokens if t.surface == "ワン"), self.engine)
        self.assertIsNotNone(pm5)
        assert pm5 is not None
        self.assertEqual(pm5.surface, "ワンちゃん")
        self.assertIn("小狗", explain_lookup_in_sentence(build_phrase_token(pm5), s5, "")[0])

    def test_sasugani_and_labeled_fallbacks_when_no_chinese(self) -> None:
        """驗收：さすがに能找到流石/流石に的繁中與日日義項；無中文時明確標註「日日備援」或「日英備援」。"""
        s = "さすがにそれは無理だ。"
        ts = self.tokenizer.tokenize(s)
        t_sasuga = ts.tokens[0]
        pm = find_phrase_for_token(s, ts.tokens, t_sasuga, self.engine)
        self.assertIsNotNone(pm)
        assert pm is not None
        self.assertEqual(pm.surface, "さすがに")
        ctx, _ = explain_lookup_in_sentence(build_phrase_token(pm), s, "")
        self.assertEqual(ctx, "實在")
        self.assertTrue(pm.lookup.senses_by_language()["ja"])
        self.assertTrue(pm.lookup.senses_by_language()["en"])

        # 純日日條目（無中文、無英文）在雙語模式下須清楚標記「日日備援」
        m_ja = self.engine.lookup_exact_or_deinflect("純日詞")
        self.assertIsNotNone(m_ja)
        assert m_ja is not None
        txt_ja, lang_ja, fb_ja = m_ja.best_sense_with_fallback("bilingual", strict=False)
        self.assertEqual((lang_ja, fb_ja), ("ja", "日日備援"))
        self.assertIn("日本語辞典", txt_ja)

        popup = OverlayLookupPopup()
        self.addCleanup(popup.close)
        from galgame_anki.core.tokenizer import Token

        tok_ja = Token("純日詞", "純日詞", "じゅんにちし", 0, 3, has_dict_entry=True, lookup=m_ja)
        popup.popup_for_token(tok_ja, "bilingual", False)
        self.assertIn("日日備援", popup.def_label.text())

        # 純日英條目（無中文）在雙語模式下須清楚標記「日英備援」
        m_en = self.engine.lookup_exact_or_deinflect("純英詞")
        self.assertIsNotNone(m_en)
        tok_en = Token("純英詞", "純英詞", "じゅんえいし", 0, 3, has_dict_entry=True, lookup=m_en)
        popup.popup_for_token(tok_en, "bilingual", False)
        self.assertIn("日英備援", popup.def_label.text())
        self.assertIn("english-only fallback", popup.def_label.text())

    def test_jitendex_structured_boundaries_and_legacy_compat(self) -> None:
        """驗收：Jitendex structured-content 匯入不黏連 exp5-dan，同時相容舊庫黏連標籤。"""
        # 切到單開 Jitendex 驗證匯入乾淨度
        self.engine.set_en_fallback_source("jitendex")
        for expr in ("気を遣う", "腰を折る"):
            m = self.engine.lookup_exact_or_deinflect(expr)
            self.assertIsNotNone(m)
            assert m is not None
            en_items = m.senses_by_language()["en"]
            self.assertTrue(en_items)
            self.assertNotIn("exp5-dan", en_items[0].text)
            self.assertTrue(en_items[0].text.startswith("to "), en_items[0].text)

        # 舊庫黏連相容清理（同時保護 expect / explain 不被誤切）
        self.assertEqual(_strip_jitendex_pos_prefix("exp5-danto worry (about)"), "to worry (about)")
        self.assertEqual(_strip_jitendex_pos_prefix("exp5-danto interrupt (a story, etc.)"), "to interrupt (a story, etc.)")
        self.assertEqual(_strip_jitendex_pos_prefix("nounchildishdoggie"), "doggie")
        self.assertEqual(_strip_jitendex_pos_prefix("expect something"), "expect something")

    def test_pitch_by_reading_and_preset_separation(self) -> None:
        """驗收：音調嚴格依讀音匹配；繁中優先預設不切斷日日與單一日英備援。"""
        m_hashi = self.engine.lookup_exact_or_deinflect("橋", reading_hint="はし")
        m_kyou = self.engine.lookup_exact_or_deinflect("橋", reading_hint="きょう")
        assert m_hashi is not None and m_kyou is not None
        self.assertEqual(m_hashi.pitches, ["2"])
        self.assertEqual(m_kyou.pitches, ["1"])

        # zh_focus 保留單一日英（JMdict）與日日可查，腰を折る仍查得到
        self.engine.apply_dictionary_preset("zh_focus")
        self.assertEqual(self.engine.get_en_fallback_source(), "jmdict")
        m_koshi = self.engine.lookup_exact_or_deinflect("腰を折る")
        self.assertIsNotNone(m_koshi)
        assert m_koshi is not None
        self.assertTrue(m_koshi.senses_by_language()["en"])

    def test_legacy_db_read_and_atomic_update_success_and_rollback(self) -> None:
        """驗收：舊版 schema 資料庫可正常讀取；詞典更新保留啟用／排序且失敗自動回滾。"""
        legacy_db = self.tmp_dir / "legacy.db"
        conn = sqlite3.connect(str(legacy_db))
        conn.executescript(
            """
            CREATE TABLE dictionaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL DEFAULT 'bilingual',
                revision TEXT DEFAULT '',
                entry_count INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 100
            );
            CREATE TABLE terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dict_id INTEGER NOT NULL,
                term TEXT NOT NULL,
                reading TEXT NOT NULL,
                pos TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                glosses_json TEXT NOT NULL
            );
            CREATE TABLE term_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dict_id INTEGER NOT NULL,
                term TEXT NOT NULL,
                reading TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            INSERT INTO dictionaries (name, kind, revision, entry_count, priority)
            VALUES ('old-zh', 'bilingual', 'v1', 1, 33);
            INSERT INTO terms (dict_id, term, reading, pos, score, glosses_json)
            VALUES (1, '旧詞', 'きゅうし', 'n', 10, '["舊版釋義"]');
            """
        )
        conn.commit()
        conn.close()

        eng = DictionaryEngine(db_path=legacy_db, load_starter=False)
        self.addCleanup(eng.close)
        m = eng.lookup_exact_or_deinflect("旧詞")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.best_concise_meaning("bilingual"), "舊版釋義")

        # 記錄使用者手動停用與自訂優先序
        d0 = next(d for d in eng.list_dictionaries() if d["name"] == "old-zh")
        eng.set_dictionary_enabled(d0["id"], False)

        # 1) 壞掉的新版 zip 必須回滾，舊詞典完好無損
        bad_zip = self.tmp_dir / "bad_update.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("index.json", json.dumps({"title": "old-zh", "revision": "v2-broken"}))
            zf.writestr("term_bank_1.json", "{not valid json")
        with self.assertRaises(Exception):
            eng.import_yomitan_zip(bad_zip)
        d_after_fail = next(d for d in eng.list_dictionaries() if d["name"] == "old-zh")
        self.assertEqual(d_after_fail["revision"], "v1")
        self.assertFalse(d_after_fail["enabled"])
        self.assertEqual(d_after_fail["priority"], 33)

        # 2) 成功更新至 v2，自動保留使用者原本的 enabled=False 與 priority=33
        good_zip = self.tmp_dir / "good_update.zip"
        _write_yomitan_zip(good_zip, "old-zh", [["旧詞", "きゅうし", "n", "", 10, ["新版釋義"], 1, ""]], revision="v2")
        eng.import_yomitan_zip(good_zip)
        d_after_ok = next(d for d in eng.list_dictionaries() if d["name"] == "old-zh")
        self.assertEqual(d_after_ok["revision"], "v2")
        self.assertFalse(d_after_ok["enabled"])
        self.assertEqual(d_after_ok["priority"], 33)
        eng.set_dictionary_enabled(d_after_ok["id"], True)
        self.assertEqual(eng.lookup_exact_or_deinflect("旧詞").best_concise_meaning("bilingual"), "新版釋義")

    def test_phrase_ending_in_spoken_contraction(self) -> None:
        line = "先輩に気を遣ってたんだよ。"
        parsed = self.tokenizer.tokenize(line)
        verb = next(t for t in parsed.tokens if t.surface == "遣ってたんだ")
        phrase = find_phrase_for_token(line, parsed.tokens, verb, self.engine)
        self.assertIsNotNone(phrase)
        self.assertEqual((phrase.surface, phrase.lookup.lemma), ("気を遣ってたんだ", "気を遣う"))

    def test_in_app_dictionary_switch_and_retry(self) -> None:
        from types import SimpleNamespace
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtNetwork import QNetworkReply
        from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QWidget
        from galgame_anki.ui.web_dictionary import show_web_dictionary

        class View(QWidget):
            loadFinished = Signal(bool)
            def setPage(self, page):
                self._page = page
            def load(self, url):
                requests.append(url)

        class Reply(QObject):
            finished = Signal()
            def error(self):
                return QNetworkReply.NetworkError.NoError
            def readAll(self):
                return "<h2>日中辞典 第3版の解説</h2><p>壊れる</p><p>損壞</p><p>出典　小學館</p>".encode()
            def abort(self):
                self.finished.emit()

        requests = []
        pending = []
        def get(request):
            requests.append(request.url())
            pending.append(Reply())
            return pending[-1]

        owner = QWidget()
        self.addCleanup(owner.close)
        modules = {"PySide6.QtWebEngineCore": SimpleNamespace(QWebEnginePage=QObject),
                   "PySide6.QtWebEngineWidgets": SimpleNamespace(QWebEngineView=View)}
        with patch.dict("sys.modules", modules), patch("PySide6.QtNetwork.QNetworkAccessManager.get", side_effect=get):
            show_web_dictionary("壊れる", owner, "kotobank")
            dialog = owner.findChild(QDialog)
            switch = dialog.findChild(QComboBox)
            self.assertEqual(requests[-1].path(), "/jazhword/壊れる")
            pending[0].finished.emit()
            self.assertIn("損壞", dialog.reader.toPlainText())
            self.assertNotIn("出典", dialog.reader.toPlainText())
            dialog.original.click()
            view = dialog.findChild(View)
            view.loadFinished.emit(False)
            self.assertTrue(any("未能載入" in s.text() and s.isVisible()
                                for s in dialog.findChildren(QLabel)))
            for source, host in (("sigure", "www.sigure.tw"), ("japandict", "www.japandict.com"),
                                 ("jisho", "jisho.org"), ("kotobank_ja", "kotobank.jp")):
                switch.setCurrentIndex(switch.findData(source))
                self.assertEqual(requests[-1].host(), host)
                self.assertTrue(requests[-1].path().endswith("壊れる"))
            next(b for b in dialog.findChildren(QPushButton) if b.text() == "重試").click()
            self.assertEqual(requests[-1], requests[-2])
            self.assertEqual(len(requests), 7)
            view.loadFinished.emit(True)
            dialog.close()

    def test_offline_hover_toggle_rapid_sub_switch_and_ondemand_web_lookup(self) -> None:
        """驗收：斷網下懸停／快速換句／語塊切換與按需網頁查證（絕不於懸停自動開網頁）。"""
        self.assertEqual(
            build_web_lookup_url("kotobank", "腰を折る"),
            "https://kotobank.jp/jazhword/%E8%85%B0%E3%82%92%E6%8A%98%E3%82%8B",
        )
        self.assertEqual(
            build_web_lookup_url("jisho", "気を遣う"),
            "https://jisho.org/search/%E6%B0%97%E3%82%92%E9%81%A3%E3%81%86",
        )

        # 驗證懸停與詳情查詞完全不觸發任何瀏覽器或網路請求
        with patch("galgame_anki.ui.lookup_popup.show_web_dictionary") as open_url, \
             patch("httpx.get", side_effect=AssertionError("離線查詞不可聯網")):
            overlay = OverlayWindow("starlight", dictionary=self.engine)
            self.addCleanup(overlay.close)
            self.addCleanup(overlay.popup.close)

            # 快速連續切換多句字幕
            for line in (
                "君は誰？",
                "あの君主は誰？",
                "とりま駅に行こう。",
                "ガチでワンチャンある。",
                "先輩に気を遣う。",
            ):
                from galgame_anki.core.sentence_store import SentenceRecord

                rec = SentenceRecord(record_id=1, raw_text=line, source="mpv", tokenized=self.tokenizer.tokenize(line))
                overlay.show_record(rec)

            # 懸停最後一句「先輩に気を遣う。」的「遣う」 -> 自動帶出語塊「気を遣う」
            tok_tsukau = next(t for t in overlay._current_record.tokenized.tokens if t.surface == "遣う")
            overlay._on_token_hovered(tok_tsukau, QPoint(240, 260))
            self.app.processEvents()
            self.assertEqual(overlay.popup.title_label.text(), "気を遣う")
            self.assertIn("顧慮先輩", overlay.popup.def_label.text())
            open_url.assert_not_called()

            # 切換至獨立詞模式，驗證可順利回到單詞「遣う」再切回語塊
            overlay.popup._toggle_phrase_mode(False)
            self.assertEqual(overlay.popup.title_label.text(), "遣う")
            overlay.popup._toggle_phrase_mode(True)
            self.assertEqual(overlay.popup.title_label.text(), "気を遣う")

            # 主視窗詳情：支援 繁中／日日／日英 分流，且僅在使用者主動點擊網頁查證時才開啟網址
            panel = LookupDetailPanel("starlight")
            self.addCleanup(panel.close)
            pm = find_phrase_for_token("先輩に気を遣う。", overlay._current_record.tokenized.tokens, tok_tsukau, self.engine)
            assert pm is not None
            panel.show_token(tok_tsukau, sentence="先輩に気を遣う。", phrase_token=build_phrase_token(pm))
            self.assertIn("顧慮先輩", panel.bi_view.toPlainText())
            self.assertIn("配慮", panel.mono_view.toPlainText())
            self.assertIn("to worry", panel.en_view.toPlainText())
            open_url.assert_not_called()

            # 主動點擊網頁查證 -> 只傳目前語塊「気を遣う」，絕不傳整句
            opened = panel.open_web_lookup("kotobank")
            self.assertIn("%E6%B0%97%E3%82%92%E9%81%A3%E3%81%86", opened)
            self.assertNotIn("%E5%85%88%E8%BC%A9", opened)
            open_url.assert_called_once_with("気を遣う", panel, "kotobank")
            open_url.reset_mock()
            # 懸停浮窗已精簡移除「線上辭典」，保留主面板語塊／獨立詞切換後按需查證
            self.assertFalse(hasattr(overlay.popup, "web_btn"))
            panel._toggle_phrase_mode(False)
            panel.open_web_lookup("kotobank")
            open_url.assert_called_once_with("遣う", panel, "kotobank")

            # 詞典管理視窗日英備援切換器煙霧測試
            from PySide6.QtCore import QThreadPool

            dlg = DictionaryManagerDialog(self.engine, QThreadPool.globalInstance())
            self.addCleanup(dlg.close)
            self.assertGreaterEqual(dlg.en_fallback_combo.count(), 4)


if __name__ == "__main__":
    unittest.main()
