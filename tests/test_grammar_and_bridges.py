"""測試離線 N5→N1 語法引擎、學習藍圖進度持久化、mpv IPC 字幕解析與 KWin Wayland 置頂腳本。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from galgame_anki.adapters.mpv_bridge import (
    _patch_single_mpv_conf,
    detect_mpv_backend,
    is_mpv_installed,
    parse_mpv_ipc_message,
)
from galgame_anki.adapters.window_pin import (
    OVERLAY_WINDOW_TITLE,
    POPUP_WINDOW_TITLE,
    build_kwin_pin_script,
)
from galgame_anki.adapters.text_sources import parse_ws_payload
from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.grammar_engine import (
    OfflineGrammarEngine,
    build_web_ai_prompt,
    explain_pos_friendly,
)
from galgame_anki.core.learning_progress import LearningProgressStore
from galgame_anki.core.tokenizer import JapaneseTokenizer


class TestGrammarAndMediaBridges(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dict_engine = DictionaryEngine(db_path=Path(self.tmp.name) / "dict.db", load_starter=True)
        self.tokenizer = JapaneseTokenizer(self.dict_engine)
        self.grammar = OfflineGrammarEngine()
        self.progress = LearningProgressStore(db_path=Path(self.tmp.name) / "progress.db")

    def tearDown(self) -> None:
        self.progress.close()
        self.dict_engine.close()
        self.tmp.cleanup()

    def test_offline_grammar_analysis_on_real_galgame_lines(self) -> None:
        # 1) 攻略經典句：〜たい + 〜という + 〜一方だ + 助詞 の/を/は
        tok1 = self.tokenizer.tokenize("外の世界を知りたいという想いは、募る一方だ")
        a1 = self.grammar.analyze(tok1)
        hit_ids_1 = {h.pattern_id for h in a1.grammar_hits}
        self.assertIn("n5_tai", hit_ids_1)
        self.assertIn("n4_toiu", hit_ids_1)
        self.assertIn("n3_ippouda", hit_ids_1)
        self.assertTrue(any(p.surface == "は" for p in a1.particle_roles))
        self.assertIn("一方だ", a1.to_compact_html())

        # 2) ATRI 實機對話句：〜ている/てる口語 + 助詞
        tok2 = self.tokenizer.tokenize("やけに急いでるわねぇ")
        a2 = self.grammar.analyze(tok2)
        self.assertTrue(any("てる" in h.name or "でる" in h.matched_text or "ている" in h.name for h in a2.grammar_hits))

        # 3) ATRI 對話句：〜のか / 質問語氣
        tok3 = self.tokenizer.tokenize("（この子、怖くはないのか……？）")
        a3 = self.grammar.analyze(tok3)
        self.assertTrue(len(a3.particle_roles) >= 1)

        # 4) 攻略 Lv.3 例句：〜すぎる
        tok4 = self.tokenizer.tokenize("『誠、あなたは少しこちらの世界にかぶれすぎた。私自身もね』")
        a4 = self.grammar.analyze(tok4)
        self.assertTrue(any("すぎ" in h.name for h in a4.grammar_hits))

    def test_pos_friendly_and_web_ai_prompt(self) -> None:
        self.assertIn("一段動詞", explain_pos_friendly("v1 vt"))
        self.assertIn("他動詞", explain_pos_friendly("他下一"))
        self.assertIn("い形容詞", explain_pos_friendly("adj-i"))

        tok = self.tokenizer.tokenize("外の世界を知りたいという想いは、募る一方だ")
        analysis = self.grammar.analyze(tok)
        prompt = build_web_ai_prompt(
            sentence=tok.raw,
            target_word="募る",
            target_reading="つのる",
            grammar_hits=analysis.grammar_hits,
        )
        self.assertIn("外の世界を知りたいという想いは、募る一方だ", prompt)
        self.assertIn("募る（つのる）", prompt)
        self.assertIn("一方だ", prompt)

    def test_learning_progress_persistence(self) -> None:
        tok = self.tokenizer.tokenize("外の世界を知りたいという想いは、募る一方だ")
        analysis = self.grammar.analyze(tok)
        self.progress.record_sentence_seen(analysis.grammar_hits, tok.raw, "ATRI")
        self.assertEqual(self.progress.get_sentences_read(), 1)

        prog = self.progress.get_all_grammar_progress()
        self.assertIn("n3_ippouda", prog)
        self.assertEqual(prog["n3_ippouda"]["seen_count"], 1)
        self.assertEqual(prog["n3_ippouda"]["last_source"], "ATRI")

        self.progress.set_grammar_status("n3_ippouda", "N3", "mastered")
        prog2 = self.progress.get_all_grammar_progress()
        self.assertEqual(prog2["n3_ippouda"]["status"], "mastered")

    def test_mpv_ipc_message_parsing(self) -> None:
        state: dict[str, object] = {}
        self.assertIsNone(
            parse_mpv_ipc_message({"event": "property-change", "name": "media-title", "data": "Frieren_EP01.mkv"}, state)
        )
        self.assertIsNone(
            parse_mpv_ipc_message({"event": "property-change", "name": "path", "data": "/videos/frieren01.mkv"}, state)
        )
        self.assertIsNone(
            parse_mpv_ipc_message({"event": "property-change", "name": "sub-start", "data": 12.5}, state)
        )
        self.assertIsNone(
            parse_mpv_ipc_message({"event": "property-change", "name": "sub-end", "data": 15.8}, state)
        )
        ev = parse_mpv_ipc_message(
            {"event": "property-change", "name": "sub-text", "data": "勇者ヒンメルならそうした"},
            state,
        )
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.text, "勇者ヒンメルならそうした")
        self.assertEqual(ev.media_title, "Frieren_EP01.mkv")
        self.assertEqual(ev.video_path, "/videos/frieren01.mkv")
        self.assertEqual(ev.sub_start, 12.5)
        self.assertEqual(ev.sub_end, 15.8)

        # 相同字幕不重複觸發
        self.assertIsNone(
            parse_mpv_ipc_message(
                {"event": "property-change", "name": "sub-text", "data": "勇者ヒンメルならそうした"},
                state,
            )
        )

    def test_mpv_flatpak_detection_and_idempotent_conf_patch(self) -> None:
        info = detect_mpv_backend()
        self.assertIn(info["mode"], ("native", "flatpak", "none"))
        self.assertEqual(is_mpv_installed(), bool(info["installed"]))
        if info["installed"]:
            self.assertTrue(len(info["cmd"]) >= 1)
            self.assertTrue(len(info["config_paths"]) >= 1)

        cfg = Path(self.tmp.name) / "mpv" / "mpv.conf"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("# user config\nvolume=80\n", encoding="utf-8")

        _patch_single_mpv_conf(cfg, "/tmp/galanki-mpv.sock", hide_native_sub=True)
        txt1 = cfg.read_text(encoding="utf-8")
        self.assertIn("volume=80", txt1)
        self.assertIn("input-ipc-server=/tmp/galanki-mpv.sock", txt1)
        self.assertIn("sub-visibility=no", txt1)

        # 二次呼叫為冪等，不可重複寫入
        _patch_single_mpv_conf(cfg, "/tmp/galanki-mpv.sock", hide_native_sub=True)
        txt2 = cfg.read_text(encoding="utf-8")
        self.assertEqual(txt1, txt2)

    def test_kwin_wayland_pin_script_generation(self) -> None:
        js_on = build_kwin_pin_script(pinned=True, initial_geometry=(320, 740, 820, 128))
        self.assertIn(OVERLAY_WINDOW_TITLE, js_on)
        self.assertIn(POPUP_WINDOW_TITLE, js_on)
        self.assertIn("const PINNED = true;", js_on)
        self.assertIn("w.keepAbove = PINNED;", js_on)
        self.assertIn("x: 320, y: 740", js_on)

        js_off = build_kwin_pin_script(pinned=False)
        self.assertIn("const PINNED = false;", js_off)

    def test_ws_payload_metadata_parsing(self) -> None:
        sent, src, title = parse_ws_payload(
            json.dumps(
                {
                    "sentence": "この世界は残酷だ",
                    "source": "hook",
                    "title": "ATRI -My Dear Moments-",
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual(sent, "この世界は残酷だ")
        self.assertEqual(src, "hook")
        self.assertEqual(title, "ATRI -My Dear Moments-")


if __name__ == "__main__":
    unittest.main()
