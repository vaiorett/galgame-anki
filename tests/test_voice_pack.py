"""測試遊戲解包（XP3 / 已解包資料夾）、台詞↔純淨人聲對照（0% BGM）、劇情游標防撞車、旁白略過與匯出。"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_anki.adapters.media_utils import extract_video_audio_mp3, extract_video_frame_webp
from galgame_anki.adapters.xp3_psb import clean_psb_dialogue_text, extract_dialogues_from_psb_obj
from galgame_anki.config import AppConfig
from galgame_anki.core.voice_pack import VoicePackManager, normalize_dialogue_for_match
from galgame_anki.ui.main_window import MainWindow


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
        raise RuntimeError(f"ffmpeg failed to synthesize ogg: {res.stderr.decode(errors='ignore')}")


def _build_synthetic_xp3(xp3_path: Path, entries: dict[str, bytes]) -> None:
    """打包極簡標準 XP3v2（File + info/segm/adlr）封包供免解壓直讀單元測試使用。"""
    blob = bytearray(b"XP3\r\n \n\x1a\x8bg\x01")
    blob.extend(struct.pack("<Q", 0))  # placeholder for index offset

    file_records: list[tuple[str, int, int, int, int]] = []
    for name, raw in entries.items():
        comp = zlib.compress(raw)
        offset = len(blob)
        blob.extend(comp)
        adlr = zlib.adler32(raw) & 0xFFFFFFFF
        file_records.append((name, offset, len(raw), len(comp), adlr))

    idx_raw = bytearray()
    for name, off, osz, csz, adlr in file_records:
        u16 = name.encode("utf-16le")
        info_c = struct.pack("<IQQH", 0, osz, csz, len(name)) + u16
        segm_c = struct.pack("<IQQQ", 1, off, osz, csz)
        adlr_c = struct.pack("<I", adlr)

        sub = bytearray()
        sub.extend(b"info" + struct.pack("<Q", len(info_c)) + info_c)
        sub.extend(b"segm" + struct.pack("<Q", len(segm_c)) + segm_c)
        sub.extend(b"adlr" + struct.pack("<Q", len(adlr_c)) + adlr_c)

        idx_raw.extend(b"File" + struct.pack("<Q", len(sub)) + sub)

    idx_comp = zlib.compress(bytes(idx_raw))
    idx_off = len(blob)
    blob.extend(struct.pack("<BQQ", 1, len(idx_comp), len(idx_raw)))
    blob.extend(idx_comp)
    struct.pack_into("<Q", blob, 11, idx_off)
    xp3_path.write_bytes(bytes(blob))


class TestVoicePackEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_clean_psb_ruby_and_dialogue_normalization(self) -> None:
        raw = "[かみしろ,1]神白[みなも,2]水菜萌です\nよろしくお願いします"
        cleaned = clean_psb_dialogue_text(raw)
        self.assertEqual(cleaned, "神白水菜萌ですよろしくお願いします")

        n_exact, n_core = normalize_dialogue_for_match("キャサリン「起きた？　うなされてたわよ？」")
        self.assertEqual(n_exact, "キャサリン「起きた？うなされてたわよ？」")
        self.assertEqual(n_core, "起きた？うなされてたわよ")

    def test_unpacked_scn_json_cursor_chorus_narration_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "MockNovel"
            voice_dir = game_dir / "voice"
            voice_dir.mkdir(parents=True)
            _make_ogg_tone(voice_dir / "vo_001.ogg", freq=440)
            _make_ogg_tone(voice_dir / "vo_002.ogg", freq=880)

            scn_obj = {
                "scenes": [
                    {
                        "texts": [
                            [
                                "少女",
                                None,
                                [
                                    ["少女", "「……うん」"],
                                    ["Girl", "\"...Mm.\""],
                                    ["少女", "「……嗯」"],
                                    ["少女", "「……嗯」"],
                                ],
                                [{"voice": "vo_001"}],
                            ],
                            [
                                None,
                                None,
                                [
                                    [None, "暗闇の中にいた。"],
                                    [None, "I was in the darkness."],
                                    [None, "身处于一片黑暗之中。"],
                                    [None, "身處一片黑暗之中。"],
                                ],
                                [],
                            ],
                            [
                                "アトリ",
                                None,
                                [
                                    ["アトリ", "「よろしくお願いしますね、夏生さん」"],
                                    ["Atri", "\"Please treat me well, Natsuki-san.\""],
                                    ["亚托莉", "「请多多指教哦，夏生先生」"],
                                    ["亞托莉", "「請多多指教喔，夏生先生」"],
                                ],
                                [{"voice": "vo_001"}],
                            ],
                            [
                                "少女",
                                None,
                                [
                                    ["少女", "「……うん」"],
                                    ["Girl", "\"...Mm.\""],
                                    ["少女", "「……嗯（二）」"],
                                    ["少女", "「……嗯（二）」"],
                                ],
                                [{"voice": "vo_002"}],
                            ],
                            [
                                "みんな",
                                None,
                                [
                                    ["みんな", "『ええええ！　本当ですか！？』"],
                                    ["Everyone", "\"Eeeeh! Really!?\""],
                                    ["大家", "『哎哎哎！真的吗！？』"],
                                    ["大家", "『哎哎哎！真的嗎！？』"],
                                ],
                                [{"voice": "vo_001"}, {"voice": "vo_002"}],
                            ],
                        ]
                    }
                ]
            }
            (game_dir / "prologue.scn.json").write_text(
                json.dumps(scn_obj, ensure_ascii=False),
                encoding="utf-8",
            )

            # 順便驗證直接對 obj 解析的結構
            parsed = extract_dialogues_from_psb_obj(scn_obj, "prologue.scn.json")
            self.assertEqual(len(parsed), 5)
            self.assertEqual(parsed[4].voice_ids, ["vo_001", "vo_002"])

            mgr = VoicePackManager(db_path=Path(tmp) / "vp.db")
            try:
                summary = mgr.index_game_directory(game_dir)
                self.assertEqual(summary.mode, "unpacked")
                self.assertEqual(summary.line_count, 5)
                self.assertEqual(summary.voiced_line_count, 4)
                self.assertEqual(summary.asset_count, 2)

                # 第 1 次遇到「……うん」應命中 seq=0 (vo_001)
                m1 = mgr.match_and_extract_voice("「……うん」", pack_id=summary.pack_id)
                self.assertTrue(m1.matched_line)
                self.assertTrue(m1.has_voice)
                self.assertEqual(m1.seq_index, 0)
                self.assertEqual(m1.voice_ids, ["vo_001"])
                self.assertIsNotNone(m1.audio_clip)
                assert m1.audio_clip is not None
                self.assertGreater(len(m1.audio_clip[0]), 500)

                # 無配音旁白：必須回報 matched_line=True 但 has_voice=False，絕不產生假音訊
                m_nar = mgr.match_and_extract_voice("暗闇の中にいた。", pack_id=summary.pack_id)
                self.assertTrue(m_nar.matched_line)
                self.assertFalse(m_nar.has_voice)
                self.assertIsNone(m_nar.audio_clip)
                self.assertEqual(m_nar.translation, "身處一片黑暗之中。")

                # 推進劇情至第 3 句
                m_atri = mgr.match_and_extract_voice("アトリ「よろしくお願いしますね、夏生さん」", pack_id=summary.pack_id)
                self.assertTrue(m_atri.has_voice)
                self.assertEqual(m_atri.seq_index, 2)
                self.assertEqual(m_atri.translation, "「請多多指教喔，夏生先生」")

                # 第 2 次遇到「……うん」時游標已前進，必須精準命中 seq=3 (vo_002)！
                m2 = mgr.match_and_extract_voice("「……うん」", pack_id=summary.pack_id)
                self.assertTrue(m2.has_voice)
                self.assertEqual(m2.seq_index, 3)
                self.assertEqual(m2.voice_ids, ["vo_002"])

                # 多人合聲混音
                m_chorus = mgr.match_and_extract_voice("『ええええ！本当ですか！？』", pack_id=summary.pack_id)
                self.assertTrue(m_chorus.has_voice)
                self.assertEqual(m_chorus.voice_ids, ["vo_001", "vo_002"])
                self.assertIsNotNone(m_chorus.audio_clip)

                # 實體匯出純人聲 + JSON 對照表
                export_dir = Path(tmp) / "exported"
                exp = mgr.export_pack_assets(summary.pack_id, export_dir)
                self.assertEqual(exp["exported_voices"], 2)
                self.assertTrue((export_dir / "voice" / "vo_001.ogg").exists())
                self.assertTrue((export_dir / "dialogue_voice_index.json").exists())
            finally:
                mgr.close()

    def test_synthetic_xp3_ks_direct_indexing_without_disk_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "SynthGame"
            game_dir.mkdir(parents=True)
            ogg_path = Path(tmp) / "atr_001.ogg"
            _make_ogg_tone(ogg_path, freq=520)

            ks_script = (
                "*start|\n"
                "@vo s=atr_001\n"
                "「おはようございます、夏生さん！」[p]\n"
                "辺りはまだ薄暗かった。[p]\n"
            ).encode("utf-8")

            _build_synthetic_xp3(
                game_dir / "data.xp3",
                {
                    "scenario/day01.ks": ks_script,
                    "voice/atr_001.ogg": ogg_path.read_bytes(),
                },
            )

            mgr = VoicePackManager(db_path=Path(tmp) / "vp_xp3.db")
            try:
                summary = mgr.index_game_directory(game_dir)
                self.assertEqual(summary.mode, "xp3_cx")
                self.assertEqual(summary.line_count, 2)
                self.assertEqual(summary.voiced_line_count, 1)
                self.assertEqual(summary.asset_count, 1)

                res = mgr.match_and_extract_voice("「おはようございます、夏生さん！」", pack_id=summary.pack_id)
                self.assertTrue(res.matched_line)
                self.assertTrue(res.has_voice)
                self.assertEqual(res.voice_ids, ["atr_001"])
                self.assertIsNotNone(res.audio_clip)
            finally:
                mgr.close()

    def test_main_window_extracts_pure_voice_pack_without_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_dir = root / "SampleGal"
            (game_dir / "voice").mkdir(parents=True)
            _make_ogg_tone(game_dir / "voice" / "v100.ogg", freq=660)
            scn_obj = {
                "scenes": [
                    {
                        "texts": [
                            [
                                "アトリ",
                                None,
                                [
                                    ["アトリ", "「高性能ですから！」"],
                                    ["Atri", "\"Because I'm high performance!\""],
                                    ["亚托莉", "「因为我是高性能的！」"],
                                    ["亞托莉", "「因為我是高性能的！」"],
                                ],
                                [{"voice": "v100"}],
                            ],
                            [
                                None,
                                None,
                                [
                                    [None, "静かな波の音が聞こえる。"],
                                    [None, "I can hear quiet waves."],
                                    [None, "能听见宁静的海浪声。"],
                                    [None, "能聽見寧靜的海浪聲。"],
                                ],
                                [],
                            ],
                        ]
                    }
                ]
            }
            (game_dir / "s01.scn.json").write_text(json.dumps(scn_obj, ensure_ascii=False), encoding="utf-8")

            vp_db = root / "voice_packs.db"
            temp_mgr = VoicePackManager(db_path=vp_db)
            summary = temp_mgr.index_game_directory(game_dir)
            temp_mgr.close()

            cfg = AppConfig(
                first_run_completed=True,
                ws_enabled=False,
                clipboard_watch=False,
                capture_audio_on_mine=True,
                active_voice_pack_id=summary.pack_id,
                current_game_name="SampleGal",
            )
            win = MainWindow(config=cfg, load_demo=False)
            self.assertFalse(hasattr(win, "audio_recorder"))
            self.assertIsNone(extract_video_audio_mp3("/nonexistent/video.mkv", 0.0, 1.0))
            self.assertIsNone(extract_video_frame_webp("/nonexistent/video.mkv", 0.5))
            # 替換為隔離測試用的 VoicePackManager
            win.voice_packs.close()
            win.voice_packs = VoicePackManager(db_path=vp_db)
            try:
                win._ingest_sentence("「高性能ですから！」", source="hook", media_title="SampleGal")
                rec = win.store.latest()
                assert rec is not None
                clip = win._extract_audio_for_record(rec)
                self.assertIsNotNone(clip)
                self.assertEqual(rec.audio_source, "voice_pack")
                self.assertEqual(rec.pack_translation, "「因為我是高性能的！」")

                # 測試旁白：matched 但無配音，標記為 silent 且不產生音訊
                win._ingest_sentence("静かな波の音が聞こえる。", source="hook", media_title="SampleGal")
                rec2 = win.store.latest()
                assert rec2 is not None
                clip2 = win._extract_audio_for_record(rec2)
                self.assertIsNone(clip2)
                self.assertEqual(rec2.audio_source, "silent")

                # 測試未命中劇本的句子：直接回傳 None（無系統錄音）
                win._ingest_sentence("スクリプトに存在しないテスト文です。", source="hook", media_title="SampleGal")
                rec3 = win.store.latest()
                assert rec3 is not None
                self.assertIsNone(win._extract_audio_for_record(rec3))
            finally:
                win.close()


if __name__ == "__main__":
    unittest.main()
