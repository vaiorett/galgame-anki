"""測試 4 項 AJATT 升級：
1. SVG 高低音調曲線與四型著色（平板／頭高／中高／尾高）＋ Anki PitchGraph 欄位
2. 人物聲音嚴格分軌之濃縮被動聽力 MP3 + .lrc 匯出（絕不混音不同角色、自動過濾多人合聲）
3. 1T 黃金句礦場（Sentence Bank）兩階段掃描、防劇透章節、角色過濾與詞頻排序
4. mpv 多句合併（含時間軸合併）、無字幕自動倍速快進（sub-transition）、句尾自動暫停與重播
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from galgame_anki.adapters.mpv_bridge import MpvSubtitleBridge, parse_mpv_ipc_message
from galgame_anki.config import AppConfig
from galgame_anki.core.anki_client import AnkiCardPayload, AnkiConnectClient
from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.pitch_accent import (
    analyze_pitch_accent,
    render_pitch_graphs_html,
    render_pitch_svg,
    split_kana_to_moras,
)
from galgame_anki.core.sentence_bank import SentenceBankScanner
from galgame_anki.core.tokenizer import JapaneseTokenizer
from galgame_anki.core.voice_pack import VoicePackManager
from galgame_anki.ui.main_window import MainWindow
from galgame_anki.ui.pitch_widget import PitchGraphWidget
from galgame_anki.ui.sentence_bank_dialog import SentenceBankDialog
from galgame_anki.ui.voice_pack_dialog import VoicePackDialog


def _make_ogg_tone(path: Path, freq: int = 440, dur: float = 0.25) -> None:
    res = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={dur}",
            "-c:a",
            "libvorbis",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode(errors="ignore"))


class TestAjattUpgrades(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    # === 1. SVG 高低音調曲線與四型著色 ===
    def test_pitch_mora_split_classification_svg_and_widget(self) -> None:
        self.assertEqual(split_kana_to_moras("きょう"), ["きょ", "う"])
        self.assertEqual(split_kana_to_moras("がっこう"), ["が", "っ", "こ", "う"])
        self.assertEqual(split_kana_to_moras("しゃしん"), ["しゃ", "し", "ん"])

        heiban = analyze_pitch_accent("さくら", 0)
        atamadaka = analyze_pitch_accent("ねこ", 1)
        nakadaka = analyze_pitch_accent("たまご", 2)
        odaka = analyze_pitch_accent("いぬ", 2)

        assert heiban and atamadaka and nakadaka and odaka
        self.assertEqual(heiban.pitch_type, "heiban")
        self.assertEqual(heiban.levels, (False, True, True, True))      # 助詞維持高
        self.assertEqual(atamadaka.pitch_type, "atamadaka")
        self.assertEqual(atamadaka.levels, (True, False, False))
        self.assertEqual(nakadaka.pitch_type, "nakadaka")
        self.assertEqual(nakadaka.levels, (False, True, False, False))
        self.assertEqual(odaka.pitch_type, "odaka")
        self.assertEqual(odaka.levels, (False, True, False))            # 助詞下降！

        svg = render_pitch_svg("がっこう", 0, use_css_colors=True)
        self.assertIn("<svg ", svg)
        self.assertIn("pt-heiban", svg)
        self.assertIn("<polyline", svg)
        self.assertIn("pt-particle", svg)

        html_out = render_pitch_graphs_html("はし", ["1", "2"], use_css_colors=True)
        self.assertIn("頭高 [1]", html_out)
        self.assertIn("尾高 [2]", html_out)

        # 驗證 AnkiConnectClient.add_card 自動填入 PitchGraph SVG 欄位
        client = AnkiConnectClient()
        captured_note: dict = {}

        def _fake_invoke(action: str, **params):
            if action == "addNote":
                captured_note.update(params["note"])
                return 987654
            return None

        with patch.object(client, "invoke", side_effect=_fake_invoke):
            nid = client.add_card(
                "Galgame日語",
                "GalImmersion-OneClick",
                AnkiCardPayload(
                    target_word="学校",
                    target_reading="がっこう",
                    pitch_accent="0",
                    sentence="【学校】に行く",
                    sentence_furigana="",
                    definition_bilingual="學校",
                    definition_monolingual="",
                ),
            )
        self.assertEqual(nid, 987654)
        self.assertIn("<svg ", captured_note["fields"]["PitchGraph"])
        self.assertIn("pt-heiban", captured_note["fields"]["PitchGraph"])

        # 驗證 QPainter 原生 PitchGraphWidget 可正常繪製不崩潰
        w = PitchGraphWidget(compact=True, dark_mode=False)
        w.set_pitch("がっこう", ["0", "2"])
        self.assertFalse(w.isHidden())
        pix = w.grab()
        self.assertGreater(pix.width(), 40)
        w.close()

    # === 2. 人物分類濃縮被動聽力 MP3（絕不混音不同角色） ===
    def test_character_isolated_condensed_audio_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_dir = root / "HeroineGal"
            (game_dir / "voice").mkdir(parents=True)
            _make_ogg_tone(game_dir / "voice" / "ATR_001.ogg", freq=520, dur=0.25)
            _make_ogg_tone(game_dir / "voice" / "ATR_002.ogg", freq=600, dur=0.25)
            _make_ogg_tone(game_dir / "voice" / "MIN_001.ogg", freq=380, dur=0.25)

            scn1 = {
                "scenes": [
                    {
                        "texts": [
                            [
                                "アトリ",
                                None,
                                [["アトリ", "「おはようございます、夏生さん！」"], ["", ""], ["", ""], ["", "「早安，夏生先生！」"]],
                                [{"voice": "ATR_001"}],
                            ],
                            [
                                "水菜萌",
                                None,
                                [["水菜萌", "「夏生くん、起きて！」"], ["", ""], ["", ""], ["", "「夏生，快起床！」"]],
                                [{"voice": "MIN_001"}],
                            ],
                            [
                                "みんな",
                                None,
                                [["みんな", "『ええええっ！？』"], ["", ""], ["", ""], ["", "『哎哎哎！？』"]],
                                [{"voice": "ATR_001"}, {"voice": "MIN_001"}],
                            ],
                        ]
                    }
                ]
            }
            scn2 = {
                "scenes": [
                    {
                        "texts": [
                            [
                                "アトリ",
                                None,
                                [["アトリ", "「高性能ですから！」"], ["", ""], ["", ""], ["", "「因為我是高性能的！」"]],
                                [{"voice": "ATR_002"}],
                            ],
                        ]
                    }
                ]
            }
            (game_dir / "ch01.scn.json").write_text(json.dumps(scn1, ensure_ascii=False), encoding="utf-8")
            (game_dir / "ch02.scn.json").write_text(json.dumps(scn2, ensure_ascii=False), encoding="utf-8")

            mgr = VoicePackManager(db_path=root / "vp.db")
            try:
                summary = mgr.index_game_directory(game_dir)
                scenes = mgr.list_pack_scenes(summary.pack_id)
                self.assertEqual(scenes, ["ch01.scn.json", "ch02.scn.json"])

                # 角色分類必須嚴格排除多人合聲（みんな），僅計單人獨白！
                chars = mgr.list_pack_characters(summary.pack_id)
                by_key = {c.char_key: c for c in chars}
                self.assertEqual(set(by_key.keys()), {"prefix:ATR", "prefix:MIN"})
                self.assertEqual(by_key["prefix:ATR"].display_name, "アトリ")
                self.assertEqual(by_key["prefix:ATR"].solo_line_count, 2)
                self.assertEqual(by_key["prefix:MIN"].display_name, "水菜萌")
                self.assertEqual(by_key["prefix:MIN"].solo_line_count, 1)

                # 只匯出喜歡的角色（アトリ）並驗證絕不含水菜萌或合聲台詞
                out_atr = root / "out_atr"
                res1 = mgr.export_condensed_character_audio(
                    pack_id=summary.pack_id,
                    char_keys=["prefix:ATR"],
                    output_dir=out_atr,
                    gap_seconds=0.3,
                    write_lrc=True,
                )
                self.assertEqual(len(res1["files"]), 1)
                f_atr = res1["files"][0]
                self.assertEqual(f_atr["char_name"], "アトリ")
                self.assertEqual(f_atr["line_count"], 2)
                self.assertTrue(Path(f_atr["mp3_path"]).exists())
                lrc_txt = Path(f_atr["lrc_path"]).read_text(encoding="utf-8")
                self.assertIn("おはようございます", lrc_txt)
                self.assertIn("高性能ですから", lrc_txt)
                self.assertNotIn("起きて", lrc_txt)
                self.assertNotIn("ええええっ", lrc_txt)

                # 用 ffmpeg 驗證輸出 MP3 有效無錯
                v = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", f_atr["mp3_path"], "-f", "null", "-"],
                    capture_output=True,
                )
                self.assertEqual(v.returncode, 0)
                self.assertEqual(v.stderr, b"")

                # 若同時勾選兩位角色，必須各自輸出獨立 MP3（絕不合併在同一個檔案），且章節防劇透有效
                out_both = root / "out_both"
                res2 = mgr.export_condensed_character_audio(
                    pack_id=summary.pack_id,
                    char_keys=["prefix:ATR", "prefix:MIN"],
                    output_dir=out_both,
                    start_scene="ch01.scn.json",
                    end_scene="ch01.scn.json",
                )
                self.assertEqual(len(res2["files"]), 2)
                names = {x["char_name"]: x["line_count"] for x in res2["files"]}
                self.assertEqual(names, {"アトリ": 1, "水菜萌": 1})

                # UI Tab 2 煙霧測試
                cfg = AppConfig(active_voice_pack_id=summary.pack_id)
                dlg = VoicePackDialog(cfg, mgr, QThreadPool.globalInstance(), initial_tab=1)
                self.assertEqual(dlg.tabs.currentIndex(), 1)
                self.assertEqual(dlg.char_list.count(), 2)
                dlg.close()
            finally:
                mgr.close()

    # === 3. 1T 黃金句礦場（Sentence Bank） ===
    def test_sentence_bank_1t_mining_and_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_dir = root / "MineGal"
            (game_dir / "voice").mkdir(parents=True)
            _make_ogg_tone(game_dir / "voice" / "ATR_101.ogg", freq=500)
            _make_ogg_tone(game_dir / "voice" / "ATR_102.ogg", freq=560)

            scn = {
                "scenes": [
                    {
                        "texts": [
                            [
                                "アトリ",
                                None,
                                [["アトリ", "米を炊いて握り飯を作る"], ["", ""], ["", ""], ["", "煮飯做飯糰"]],
                                [{"voice": "ATR_101"}],
                            ],
                            [
                                "アトリ",
                                None,
                                [["アトリ", "外の世界を知りたいという想いは、募る一方だ"], ["", ""], ["", ""], ["", "越來越強烈"]],
                                [{"voice": "ATR_102"}],
                            ],
                        ]
                    }
                ]
            }
            (game_dir / "ep01.scn.json").write_text(json.dumps(scn, ensure_ascii=False), encoding="utf-8")

            mgr = VoicePackManager(db_path=root / "vp.db")
            d = DictionaryEngine(db_path=root / "dict.db", load_starter=True)
            tok = JapaneseTokenizer(d)
            scanner = SentenceBankScanner(tok, d)
            try:
                summary = mgr.index_game_directory(game_dir)
                # 假設已知「米」「炊く」「作る」，剩下「握り飯」就是標準 1T 句！
                known = {"米", "炊く", "作る"}
                scan = scanner.scan_candidates(
                    voice_packs=mgr,
                    pack_id=summary.pack_id,
                    known_words=known,
                    voiced_only=True,
                    max_unknown=1,
                )
                self.assertGreater(scan.coverage_pct, 0.0)
                self.assertEqual(len(scan.items), 1)
                self.assertEqual(scan.items[0].target_lemma, "握り飯")
                self.assertEqual(scan.items[0].voice_ids, ["ATR_101"])

                # UI 煙霧測試
                cfg = AppConfig(active_voice_pack_id=summary.pack_id)
                dlg = SentenceBankDialog(cfg, mgr, scanner, known, QThreadPool.globalInstance())
                self.assertEqual(dlg.pack_combo.count(), 1)
                dlg.close()
            finally:
                mgr.close()
                d.close()

    # === 4. mpv 多句合併 + sub-transition 自動倍速 + 句尾暫停 + 重播 ===
    def test_mpv_multiline_merge_and_sub_transition_ipc(self) -> None:
        # A. 測試 IPC 狀態機（auto_pause / auto_speedup / replay）
        bridge = MpvSubtitleBridge()
        sent_cmds: list[list] = []
        bridge._connected = True
        with patch.object(bridge, "send_ipc_command", side_effect=lambda cmd: (sent_cmds.append(cmd) or True)):
            # 1) 句尾自動暫停模式
            bridge.configure_immersion(auto_speedup=False, fast_speed=2.0, auto_pause_sub_end=True)
            state: dict[str, object] = {}
            ev = parse_mpv_ipc_message({"event": "property-change", "name": "sub-start", "data": 12.0}, state)
            self.assertIsNone(ev)
            ev = parse_mpv_ipc_message({"event": "property-change", "name": "sub-text", "data": "おはよう"}, state)
            self.assertIsNotNone(ev)
            assert ev is not None
            bridge._on_sub_active(ev.sub_start, ev.sub_end)
            # 字幕消失 -> 應立刻送出 pause=True
            ev_empty = parse_mpv_ipc_message({"event": "property-change", "name": "sub-text", "data": ""}, state)
            self.assertIsNone(ev_empty)
            self.assertTrue(state.pop("sub_just_cleared", False))
            bridge._on_sub_cleared()
            self.assertIn(["set_property", "pause", True], sent_cmds)

            # 2) 重播當前字幕 -> 跳回 sub_start - 0.05 並取消暫停
            sent_cmds.clear()
            ok = bridge.replay_current_subtitle()
            self.assertTrue(ok)
            self.assertEqual(sent_cmds[0][0], "seek")
            self.assertAlmostEqual(sent_cmds[0][1], 11.95, places=2)
            self.assertIn(["set_property", "pause", False], sent_cmds)

            # 3) sub-transition 無字幕自動倍速快進 -> 等待 0.45s 進入 2.25x，下一句出現立刻回 1.0x
            sent_cmds.clear()
            bridge.configure_immersion(auto_speedup=True, fast_speed=2.25, auto_pause_sub_end=False)
            bridge._on_sub_cleared()
            time.sleep(0.45)
            self.assertIn(["set_property", "speed", 2.25], sent_cmds)
            bridge._on_sub_active(20.0, 22.0)
            self.assertIn(["set_property", "speed", 1.0], sent_cmds)

        # B. 測試 MainWindow 多句合併連同 mpv [sub_start, sub_end] 一起合併
        cfg = AppConfig(first_run_completed=True, ws_enabled=False, clipboard_watch=False)
        win = MainWindow(config=cfg, load_demo=False)
        try:
            win._ingest_sentence("明日、学校で", source="mpv", video_path="/tmp/a.mkv", sub_start=10.0, sub_end=12.0)
            win._ingest_sentence("待ってるからね", source="mpv", video_path="/tmp/a.mkv", sub_start=12.1, sub_end=14.8)
            win._merge_previous_sentence()
            cur = win.store.latest()
            assert cur is not None
            self.assertEqual(cur.raw_text, "明日、学校で待ってるからね")
            self.assertAlmostEqual(cur.sub_start or 0.0, 10.0, places=2)
            self.assertAlmostEqual(cur.sub_end or 0.0, 14.8, places=2)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
