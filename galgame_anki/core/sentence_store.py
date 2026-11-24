"""近期台詞時光機佇列、每日防爆倉預算與原攻略選作難度指南載入器。"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from galgame_anki.core.tokenizer import TokenizedSentence

if TYPE_CHECKING:
    from galgame_anki.core.grammar_engine import SentenceGrammarAnalysis


@dataclass
class SentenceRecord:
    record_id: int
    raw_text: str
    tokenized: TokenizedSentence
    timestamp: float = field(default_factory=time.time)
    end_timestamp: float | None = None
    source: str = "manual"  # hook | mpv | browser | ws | clipboard | ocr | manual | demo
    media_title: str = ""
    video_path: str = ""
    sub_start: float | None = None
    sub_end: float | None = None
    mined_words: set[str] = field(default_factory=set)
    grammar_analysis: SentenceGrammarAnalysis | None = None
    sentence_translation: str = ""
    audio_clip: tuple[bytes, str] | None = None  # (mp3_bytes, filename)
    screenshot_clip: tuple[bytes, str] | None = None  # (webp_bytes, filename)


class SentenceStore:
    """保留最近 50 句歷史台詞，支援原地延長更新、精準語音時間戳與每日防爆倉守衛。"""

    def __init__(self, max_history: int = 50) -> None:
        self._records: deque[SentenceRecord] = deque(maxlen=max_history)
        self._next_id: int = 1
        self.known_words: set[str] = set()
        self.today_mined_count: int = 0

    def add(
        self,
        tokenized: TokenizedSentence,
        source: str = "manual",
        media_title: str = "",
        video_path: str = "",
        sub_start: float | None = None,
        sub_end: float | None = None,
        grammar_analysis: SentenceGrammarAnalysis | None = None,
    ) -> SentenceRecord:
        now = time.time()
        # 避免與最新一句完全重複時刷屏
        if self._records and self._records[0].raw_text == tokenized.raw:
            return self._records[0]

        # 標記上一句的結束時間，讓語音切片能精準在下一句開口前截斷
        if self._records and self._records[0].end_timestamp is None:
            self._records[0].end_timestamp = now

        rec = SentenceRecord(
            record_id=self._next_id,
            raw_text=tokenized.raw,
            tokenized=tokenized,
            timestamp=now,
            source=source,
            media_title=media_title,
            video_path=video_path,
            sub_start=sub_start,
            sub_end=sub_end,
            grammar_analysis=grammar_analysis,
        )
        self._next_id += 1
        self._records.appendleft(rec)
        # 僅在記憶體保留最近 12 句畫面快照，兼顧時光機回溯與極低記憶體佔用
        for old_idx, old_rec in enumerate(self._records):
            if old_idx >= 12 and old_rec.screenshot_clip is not None:
                old_rec.screenshot_clip = None
        return rec

    def extend_latest(
        self,
        tokenized: TokenizedSentence,
        grammar_analysis: SentenceGrammarAnalysis | None = None,
    ) -> SentenceRecord:
        """原地延長最新一句台詞（保留最初開口的 timestamp，避免漏字也保留完整語音開頭）。"""
        if not self._records:
            return self.add(tokenized, source="hook", grammar_analysis=grammar_analysis)
        latest = self._records[0]
        latest.raw_text = tokenized.raw
        latest.tokenized = tokenized
        latest.grammar_analysis = grammar_analysis
        latest.audio_clip = None  # 內容變長，下次製卡/試聽重新切完整音訊
        return latest

    def all_records(self) -> list[SentenceRecord]:
        return list(self._records)

    def get_by_id(self, record_id: int) -> SentenceRecord | None:
        for r in self._records:
            if r.record_id == record_id:
                return r
        return None

    def latest(self) -> SentenceRecord | None:
        return self._records[0] if self._records else None

    def mark_mined(self, record: SentenceRecord, lemma: str) -> None:
        record.mined_words.add(lemma)
        self.known_words.add(lemma)
        self.today_mined_count += 1


def load_difficulty_guide() -> dict[str, Any]:
    """載入內建的 galgame.dev/topic/639 生肉難度分級與避雷表。"""
    path = Path(__file__).resolve().parent.parent / "data" / "game_difficulty_guide.json"
    if not path.exists():
        return {"tiers": [], "avoid_for_beginners": []}
    return json.loads(path.read_text(encoding="utf-8"))
