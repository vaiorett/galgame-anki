"""測試內建自動 Hook 引擎（PE 架構偵測、UTF-16LE 串流切行、線程評分與防抖拼接）與本地 OCR 清理。"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from galgame_anki.adapters.hook_engine import (
    GameHookManager,
    HookLineEvent,
    _find_utf16le_newline,
    detect_pe_arch,
    merge_incremental_text,
    parse_textractor_line,
    score_hook_thread,
)
from galgame_anki.adapters.ocr_engine import clean_ocr_japanese_text
from galgame_anki.core.hook_cleaner import (
    clean_galgame_hook_text,
    collapse_progressive_prefixes,
    is_progressive_extension,
)
from galgame_anki.core.tokenizer import clean_hooked_text


def _make_fake_pe(path: Path, machine: int) -> None:
    buf = bytearray(0x100)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 0x80)
    buf[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", buf, 0x84, machine)
    path.write_bytes(buf)


class TestHookAndOCR(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_detect_pe_arch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p32 = Path(tmp) / "game32.exe"
            p64 = Path(tmp) / "game64.exe"
            _make_fake_pe(p32, 0x014C)
            _make_fake_pe(p64, 0x8664)
            self.assertEqual(detect_pe_arch(p32), "x86")
            self.assertEqual(detect_pe_arch(p64), "x64")

    def test_utf16le_newline_boundary(self) -> None:
        # 「《」為 U+300A -> UTF-16LE 位元組為 \x0a\x30，不應被誤判為換行
        text = "《あたしは高性能ですから！》\r\n次の台詞\r\n"
        raw = text.encode("utf-16le")
        idx = _find_utf16le_newline(raw)
        self.assertGreater(idx, 0)
        self.assertEqual(idx % 2, 0)
        first_line = raw[:idx].removesuffix(b"\r\x00").decode("utf-16le")
        self.assertEqual(first_line, "《あたしは高性能ですから！》")

    def test_parse_and_score_hook_threads(self) -> None:
        line = "[5:148:4012AB:0:0:KiriKiriZ:HS-4@4012AB] 「あたしは高性能ですから！」"
        ev = parse_textractor_line(line)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.hook_name, "KiriKiriZ")
        self.assertEqual(ev.text, "「あたしは高性能ですから！」")

        s_dialogue = score_hook_thread("KiriKiriZ", "「あたしは高性能ですから！」", 3)
        s_console = score_hook_thread("Console", "Textractor: pipe connected", 10)
        s_file = score_hook_thread("CreateFileW", "bgimage.xp3", 20)
        self.assertGreater(s_dialogue, s_console)
        self.assertGreater(s_dialogue, s_file)
        self.assertGreater(s_dialogue, 50.0)

    def test_merge_and_dedup_repeated_text(self) -> None:
        # 打字機逐字拼接
        buf = ""
        for ch in ("「あ", "たしは", "高性能ですから！」"):
            buf = merge_incremental_text(buf, ch)
        self.assertEqual(buf, "「あたしは高性能ですから！」")

        # 漸進前綴覆寫
        buf2 = ""
        for part in ("あた", "あたし", "あたしは"):
            buf2 = merge_incremental_text(buf2, part)
        self.assertEqual(buf2, "あたしは")

        # 2x/3x 描邊重複字與雙層整句去重
        self.assertEqual(clean_hooked_text("あああたたたししし"), "あたし")
        self.assertEqual(clean_hooked_text("高性能ですから！高性能ですから！"), "高性能ですから！")

    def test_progressive_prefix_collapse_real_atri_screenshot(self) -> None:
        # 100% 還原使用者在 ATRI 截圖中實際遇到的 KiriKiriZ 漸進前綴累積字串
        spaced = (
            "『 『おい 『おいおい、 『おいおい、そ 『おいおい、そん "
            "『おいおい、そんな事 『おいおい、そんな事し 『おいおい、そんな事したら "
            "『おいおい、そんな事したら… 『おいおい、そんな事したら…… "
            "『おいおい、そんな事したら……』 『おいおい、そんな事したら……』"
        )
        self.assertEqual(clean_galgame_hook_text(spaced), "『おいおい、そんな事したら……』")

        # 無空白直接拼接版
        unspaced = spaced.replace(" ", "")
        self.assertEqual(clean_galgame_hook_text(unspaced), "『おいおい、そんな事したら……』")

        # 帶角色名前綴版
        with_speaker = "キャサリン " + spaced
        self.assertEqual(clean_galgame_hook_text(with_speaker), "キャサリン 『おいおい、そんな事したら……』")

        # 無引號普通旁白漸進重繪
        narration = "ああたりあたりはあたりは静あたりは静かだ。"
        self.assertEqual(collapse_progressive_prefixes(narration), "あたりは静かだ。")

        # 正常日語疊詞與語氣詞絕不可誤傷！
        self.assertEqual(clean_galgame_hook_text("『おいおい、そんな事したら……』"), "『おいおい、そんな事したら……』")
        self.assertEqual(clean_galgame_hook_text("おいおい、どんどん進むね"), "おいおい、どんどん進むね")
        self.assertEqual(clean_galgame_hook_text("ふわふわの雲が流れていく"), "ふわふわの雲が流れていく")
        self.assertEqual(clean_galgame_hook_text("えええっ、本当？！"), "えええっ、本当？！")
        self.assertEqual(clean_galgame_hook_text("あああ、びっくりした！"), "あああ、びっくりした！")

        # 驗證停頓後半句延伸判定
        self.assertTrue(is_progressive_extension("『おいおい、そんな事したら…", "『おいおい、そんな事したら……』"))
        self.assertFalse(is_progressive_extension("『おいおい、そんな事したら……』", "次の全然違う台詞"))

    def test_hook_cleaner_performance_no_gui_freeze(self) -> None:
        import time

        # 模擬遊戲啟動時 Hook 吐出的 2,500 字與 10,000 字長腳本字串，必須在 50ms 內完成絕不卡死 UI
        big_2500 = "".join(f"行{i}:テスト文字列データ{i * 7}、" for i in range(150))
        big_10000 = big_2500 * 4
        t0 = time.perf_counter()
        clean_galgame_hook_text(big_2500)
        clean_galgame_hook_text(big_10000)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 0.05, f"清洗耗時過久 ({elapsed:.3f}s)，可能阻塞 GUI！")

    def test_hook_manager_coalescing_and_thread_selection(self) -> None:
        mgr = GameHookManager()
        received: list[tuple[str, str]] = []
        extended: list[tuple[str, str]] = []
        mgr.dialogue_received.connect(lambda txt, key: received.append((txt, key)))
        mgr.dialogue_extended.connect(lambda txt, key: extended.append((txt, key)))

        # 模擬 KiriKiriZ 逐字送出 + 雜訊檔案名線程
        mgr._on_hook_event_main_thread(
            HookLineEvent("2", "100", "1", "0", "0", "CreateFileW", "H0", "data.xp3")
        )
        for chunk in ("「外の世界を", "知りたいという想いは、", "募る一方だ」"):
            mgr._on_hook_event_main_thread(
                HookLineEvent("5", "100", "2", "0", "0", "KiriKiriZ", "HS0", chunk)
            )

        loop = QEventLoop()
        QTimer.singleShot(520, loop.quit)
        loop.exec()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "「外の世界を知りたいという想いは、募る一方だ」")
        self.assertIn("KiriKiriZ", received[0][1])

        # 模擬遊戲在標點停頓 > 400ms 後繼續印出後半段 -> 應觸發原地延長而非丟字
        mgr._on_hook_event_main_thread(
            HookLineEvent("5", "100", "2", "0", "0", "KiriKiriZ", "HS0", "「外の世界を知りたいという想いは、募る一方だ」と彼女は言った。")
        )
        loop2 = QEventLoop()
        QTimer.singleShot(520, loop2.quit)
        loop2.exec()
        self.assertEqual(len(extended), 1)
        self.assertEqual(extended[0][0], "「外の世界を知りたいという想いは、募る一方だ」と彼女は言った。")

    def test_clean_ocr_japanese_text(self) -> None:
        raw = "外 の 世界 を 知り たい と い う 想 い は 、\n募 る 一 方 だ"
        self.assertEqual(clean_ocr_japanese_text(raw), "外の世界を知りたいという想いは、募る一方だ")
        raw_en = "ATRI -My Dear Moments- の 世界"
        self.assertEqual(clean_ocr_japanese_text(raw_en), "ATRI -My Dear Moments- の世界")


if __name__ == "__main__":
    unittest.main()
