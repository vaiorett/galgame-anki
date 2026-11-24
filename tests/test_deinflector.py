"""測試日語活用形還原引擎（涵蓋 galgame.dev/topic/639 真實卡片句子）。"""

from __future__ import annotations

import unittest

from galgame_anki.core.deinflector import deinflect


class TestDeinflector(unittest.TestCase):
    def _terms(self, surface: str) -> set[str]:
        return {c.term for c in deinflect(surface)}

    def test_guide_card_conjugations(self) -> None:
        # 攻略實拍卡片真實活用形
        self.assertIn("踏ん張る", self._terms("踏ん張れなかった"))
        self.assertIn("かぶれる", self._terms("かぶれすぎた"))
        self.assertIn("振舞う", self._terms("振舞います"))
        self.assertIn("歩く", self._terms("歩いた"))
        self.assertIn("知る", self._terms("知りたい"))
        self.assertIn("炊く", self._terms("炊いて"))
        self.assertIn("負ける", self._terms("負けた"))
        self.assertIn("入れる", self._terms("入れ"))
        self.assertIn("合わせる", self._terms("合わせて"))
        self.assertIn("ない", self._terms("なかった"))

    def test_complex_chain_conjugations(self) -> None:
        self.assertIn("食べる", self._terms("食べられなかった"))
        self.assertIn("忘れる", self._terms("忘れてしまった"))
        self.assertIn("忘れる", self._terms("忘れちゃった"))
        self.assertIn("話す", self._terms("話していました"))
        self.assertIn("騒がしい", self._terms("騒がしくなかった"))
        self.assertIn("来る", self._terms("来ませんでした"))

    def test_deinflection_reasons_trace(self) -> None:
        cands = [c for c in deinflect("踏ん張れなかった") if c.term == "踏ん張る"]
        self.assertTrue(cands, "應還原出「踏ん張る」")
        self.assertGreaterEqual(len(cands[0].reasons), 3)


if __name__ == "__main__":
    unittest.main()
