"""系統化學習藍圖進度持久化（自動紀錄在遊戲/影片中遇見與掌握的 N5→N1 語法與閱讀量）。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from galgame_anki.config import get_data_dir
from galgame_anki.core.grammar_engine import GrammarHit


class LearningProgressStore:
    """本地 SQLite 追蹤個人 N5→N1 語法藍圖點亮進度與累計閱讀句數。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (get_data_dir() / "learning_progress.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS grammar_progress (
                pattern_id TEXT PRIMARY KEY,
                level TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'new', -- new | seen | learning | mastered
                last_example TEXT NOT NULL DEFAULT '',
                last_source TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS study_examples (
                topic TEXT NOT NULL, lemma TEXT NOT NULL, sentence TEXT NOT NULL,
                excerpt TEXT NOT NULL, saved_at REAL NOT NULL,
                PRIMARY KEY(topic, lemma, sentence)
            );
            CREATE TABLE IF NOT EXISTS study_stats (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._conn.commit()

    def keep_study_example(self, topic: str, lemma: str, sentence: str, excerpt: str):
        with self._conn:
            self._conn.execute("INSERT INTO study_examples VALUES (?, ?, ?, ?, ?) "
                               "ON CONFLICT(topic, lemma, sentence) DO UPDATE SET "
                               "excerpt=excluded.excerpt, saved_at=excluded.saved_at",
                               (topic, lemma, sentence, excerpt, time.time()))

    def study_examples(self, topic: str):
        return self._conn.execute("SELECT lemma, sentence, excerpt FROM study_examples WHERE topic=? "
                                  "ORDER BY saved_at DESC", (topic,)).fetchall()

    def record_sentence_seen(self, hits: list[GrammarHit], sentence: str, source_name: str = "") -> None:
        """每讀到一句台詞（Galgame / mpv），自動點亮對應語法節點並留存真實例句。"""
        now = time.time()
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO study_stats (key, value) VALUES ('sentences_read', 1)
            ON CONFLICT(key) DO UPDATE SET value = value + 1
            """
        )
        for h in hits:
            existing = cur.execute(
                "SELECT seen_count, status FROM grammar_progress WHERE pattern_id = ?",
                (h.pattern_id,),
            ).fetchone()
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO grammar_progress
                    (pattern_id, level, seen_count, status, last_example, last_source, updated_at)
                    VALUES (?, ?, 1, 'seen', ?, ?, ?)
                    """,
                    (h.pattern_id, h.level, sentence, source_name, now),
                )
            else:
                cur.execute(
                    """
                    UPDATE grammar_progress
                    SET seen_count = seen_count + 1,
                        last_example = ?,
                        last_source = CASE WHEN ? != '' THEN ? ELSE last_source END,
                        updated_at = ?
                    WHERE pattern_id = ?
                    """,
                    (sentence, source_name, source_name, now, h.pattern_id),
                )
        self._conn.commit()

    def set_grammar_status(self, pattern_id: str, level: str, status: str) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO grammar_progress (pattern_id, level, seen_count, status, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(pattern_id) DO UPDATE SET status = ?, updated_at = ?
            """,
            (pattern_id, level, status, now, status, now),
        )
        self._conn.commit()

    def get_all_grammar_progress(self) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM grammar_progress").fetchall()
        return {r["pattern_id"]: dict(r) for r in rows}

    def get_sentences_read(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM study_stats WHERE key = 'sentences_read'"
        ).fetchone()
        return int(row["value"]) if row else 0
